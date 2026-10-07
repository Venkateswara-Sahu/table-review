import io
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tests.fixtures.pdf_factory import (
    blank_report,
    padded_report,
    rotated_cropped_report,
    two_page_report,
)


def upload(client, path, *, key=None, filename=None):
    with path.open("rb") as source:
        return client.post(
            "/api/v1/documents",
            files={"file": (filename or path.name, source, "application/pdf")},
            headers={"Idempotency-Key": key or str(uuid4())},
        )


@pytest.fixture
def owned_document(client, tmp_path):
    response = upload(client, two_page_report(tmp_path / "report.pdf"))
    assert response.status_code == 201
    return response.json()


def test_upload_list_detail_and_private_original(client, client_b, owned_document):
    id = owned_document["id"]
    assert owned_document["page_count"] == 2
    assert client.get("/api/v1/documents").json()["items"][0]["id"] == id
    assert client.get(f"/api/v1/documents/{id}").json()["filename"] == "report.pdf"
    source = client.get(f"/api/v1/documents/{id}/source")
    assert source.status_code == 200 and source.content.startswith(b"%PDF-")
    assert client_b.get(f"/api/v1/documents/{id}/source").status_code == 404
    assert client_b.get("/api/v1/documents").json()["items"] == []


def test_foreign_page_is_private(client_b, owned_document):
    assert (
        client_b.get(f"/api/v1/documents/{owned_document['id']}/pages/2/image").status_code == 404
    )


def test_page_metadata_and_original_geometry(client, owned_document):
    id = owned_document["id"]
    page = client.get(f"/api/v1/documents/{id}/pages/2").json()
    assert page["page_no"] == 2 and page["digital_text_present"]
    assert (page["width"], page["height"]) == (612, 792)
    image = client.get(f"/api/v1/documents/{id}/pages/2/image?scale=1")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    assert image.headers["Cache-Control"] == "no-store"
    assert client.get(f"/api/v1/documents/{id}/pages/3").status_code == 422


def test_render_cache_and_rotated_pages(client, tmp_path, app, monkeypatch):
    from PIL import Image

    actual_render = app.state.documents.pdf.render
    renders = []

    def tracked_render(*args):
        renders.append(args)
        return actual_render(*args)

    monkeypatch.setattr(app.state.documents.pdf, "render", tracked_render)
    response = upload(client, rotated_cropped_report(tmp_path / "rotated.pdf"))
    id = response.json()["id"]
    first = client.get(f"/api/v1/documents/{id}/pages/1/image?scale=1")
    second = client.get(f"/api/v1/documents/{id}/pages/1/image?scale=1")
    assert first.content == second.content
    assert len(renders) == 1
    assert Image.open(io.BytesIO(first.content)).size == (260, 460)
    assert (
        len(list(app.state.settings.blob_root.rglob("*.png"))) == 0
    )  # opaque private keys, no public asset filenames


@pytest.mark.parametrize(
    "name,data,code",
    [
        ("report.txt", b"%PDF-bogus", "unsupported_file"),
        ("report.pdf", b"not a PDF", "invalid_pdf"),
        ("report.pdf", b"%PDF-1.7\nbroken", "invalid_pdf"),
    ],
)
def test_invalid_input_has_explicit_outcome(client, name, data, code):
    response = client.post(
        "/api/v1/documents",
        files={"file": (name, io.BytesIO(data), "application/pdf")},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422 and response.json()["code"] == code


def test_encryption_and_scanned_page_outcomes(client, tmp_path):
    encrypted = upload(client, blank_report(tmp_path / "encrypted.pdf", encrypted=True))
    assert encrypted.status_code == 422 and encrypted.json()["code"] == "encrypted_pdf"
    scanned = upload(client, blank_report(tmp_path / "no-text.pdf"))
    assert scanned.status_code == 201
    page = client.get(f"/api/v1/documents/{scanned.json()['id']}/pages/1").json()
    assert not page["digital_text_present"] and not page["extraction_supported"]
    assert page["unsupported_reason"]


def test_exact_page_bounds(client, tmp_path):
    assert upload(client, blank_report(tmp_path / "500.pdf", pages=500)).status_code == 201
    response = upload(client, blank_report(tmp_path / "501.pdf", pages=501))
    assert response.status_code == 422 and response.json()["code"] == "page_limit"


def test_exact_byte_bounds_service(client, owner, app, tmp_path):
    from table_review.accounts.types import Actor
    from table_review.documents.service import DocumentError

    limit = 50 * 1024 * 1024
    path = padded_report(tmp_path / "50MiB.pdf", limit)
    with path.open("rb") as stream:
        result = app.state.documents.upload(Actor(id=owner.id), stream, path.name, str(uuid4()))
    assert result.byte_count == limit
    path = padded_report(tmp_path / "too-big.pdf", limit + 1)
    with path.open("rb") as stream, pytest.raises(DocumentError) as caught:
        app.state.documents.upload(Actor(id=owner.id), stream, path.name, str(uuid4()))
    assert caught.value.code == "byte_limit"


def test_duplicate_upload_keys_are_payload_bound_and_account_scoped(client, client_b, tmp_path):
    source = two_page_report(tmp_path / "report.pdf")
    key = str(uuid4())
    first = upload(client, source, key=key)
    repeat = upload(client, source, key=key)
    assert first.json()["id"] == repeat.json()["id"]
    changed = upload(client, source, key=key, filename="different.pdf")
    assert changed.status_code == 409
    other = upload(client_b, source, key=key)
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]


def test_status_and_page_polling_do_not_extend_retention(client, owned_document, session_factory):
    from table_review.storage.models import DocumentRow

    id = UUID(owned_document["id"])
    old = datetime.now(UTC) - timedelta(days=2)
    with session_factory.begin() as session:
        session.get(DocumentRow, id).last_foreground_at = old
    for suffix in ["", "/inspect", "/pages/2", "/pages/2/image"]:
        assert client.get(f"/api/v1/documents/{id}{suffix}").status_code == 200
    with session_factory() as session:
        assert session.get(DocumentRow, id).last_foreground_at == old
    assert client.post(f"/api/v1/documents/{id}/open").status_code == 200
    with session_factory() as session:
        assert session.get(DocumentRow, id).last_foreground_at > old


def test_request_body_is_bounded_before_multipart_spooling(app, owner):
    from fastapi.testclient import TestClient

    from table_review.app import create_app

    small_app = create_app(app.state.settings.model_copy(update={"max_pdf_bytes": 1024}))
    with TestClient(small_app) as client:
        client.headers["Origin"] = "http://testserver"
        login = client.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-test-password-123"},
        )
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(
            "/api/v1/documents",
            files={"file": ("report.pdf", io.BytesIO(b"%PDF-" + b"x" * 70000), "application/pdf")},
            headers={"Idempotency-Key": str(uuid4())},
        )
    assert response.status_code == 413 and response.json()["code"] == "byte_limit"


def test_separate_reports_do_not_share_deletable_image_artifacts(client, tmp_path, session_factory):
    from sqlalchemy import select

    from table_review.storage.models import PageArtifactRow

    path = two_page_report(tmp_path / "report.pdf")
    first = upload(client, path).json()["id"]
    second = upload(client, path).json()["id"]
    for id in (first, second):
        assert client.get(f"/api/v1/documents/{id}/pages/2/image").status_code == 200
    with session_factory() as session:
        keys = session.scalars(
            select(PageArtifactRow.blob_key).where(
                PageArtifactRow.document_id.in_([UUID(first), UUID(second)])
            )
        ).all()
    assert len(set(keys)) == 2


def test_source_hash_mismatch_is_visible_before_download(client, owned_document, owner, app):
    document = app.state.repository.get_document(owner.id, UUID(owned_document["id"]))
    with app.state.blobs.open(owner.id, document.blob_key) as source:
        from pathlib import Path

        path = Path(source.name)
    path.write_bytes(b"tampered source")
    response = client.get(f"/api/v1/documents/{document.id}/source")
    assert response.status_code == 503 and response.json()["code"] == "source_changed"
