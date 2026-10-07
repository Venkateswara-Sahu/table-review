"""Observable source/edit/review/export contracts against real PostgreSQL and files."""

import io
import zipfile
from uuid import uuid4
from xml.etree import ElementTree as ET

from sqlalchemy import update

from table_review.jobs.models import TaskRow
from table_review.jobs.worker import run_once
from tests.integration.test_review_api import owned_table  # noqa: F401


def test_oversized_json_is_rejected_without_saving_or_echoing_content(app, owner, owned_table):  # noqa: F811
    from fastapi.testclient import TestClient

    from table_review.app import create_app

    _, _, table = owned_table
    bounded = create_app(app.state.settings.model_copy(update={"max_json_bytes": 512}))
    with TestClient(bounded) as client:
        client.headers["Origin"] = "http://testserver"
        login = client.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-test-password-123"},
        )
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        before = client.get(f"/api/v1/tables/{table.id}").json()["revision"]["id"]
        response = client.post(
            f"/api/v1/tables/{table.id}/edits",
            json={
                "base_revision_id": before,
                "operation": {
                    "kind": "set_text",
                    "cell_id": "r1c1",
                    "text": "private large text" * 100,
                },
            },
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert response.status_code == 413 and response.json()["code"] == "request_limit"
        assert "private large text" not in response.text
        assert client.get(f"/api/v1/tables/{table.id}").json()["revision"]["id"] == before


def test_checked_history_records_actual_changes_and_is_owner_scoped(
    app,
    client,
    client_b,
    owned_table,  # noqa: F811
):
    _, _, table = owned_table
    revision = table.current_revision_id
    route = f"/api/v1/revisions/{revision}/checked"
    history = f"/api/v1/revisions/{revision}/checked-history"
    for value in (True, True, False):
        assert client.post(route, json={"cell_ids": ["r1c1"], "checked": value}).status_code == 200
    response = client.get(history)
    assert response.status_code == 200
    events = response.json()["items"]
    assert len(events) == 2 and [event["checked"] for event in events] == [True, False]
    assert all(
        event["cell_ids"] == ["r1c1"] and event["actor_id"] and event["created_at"]
        for event in events
    )
    assert client_b.get(history).status_code == 404


def test_manual_recovery_reviewed_export_reopens_exact_revision(app, client, client_b, owned_table):  # noqa: F811
    _, document, _ = owned_table
    with app.state.session_factory.begin() as session:
        session.execute(
            update(TaskRow)
            .where(TaskRow.document_id != document.id, TaskRow.state.in_(("queued", "running")))
            .values(state="cancelled", lease_token=None)
        )
    assert run_once(app.state.queue, "acceptance-prepare")
    created = client.post(
        f"/api/v1/documents/{document.id}/tables",
        json={
            "rows": 2,
            "columns": 2,
            "region": {
                "page": 2,
                "left": 60,
                "top": 124,
                "right": 480,
                "bottom": 252,
                "page_width": 612,
                "page_height": 792,
            },
        },
    )
    assert created.status_code == 201
    table_id = created.json()["id"]
    view = client.get(f"/api/v1/tables/{table_id}").json()
    cell_id = view["revision"]["content"]["cells"][-1]["id"]
    edited = client.post(
        f"/api/v1/tables/{table_id}/edits",
        json={
            "base_revision_id": view["revision"]["id"],
            "operation": {"kind": "set_text", "cell_id": cell_id, "text": "(1,234.50)"},
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert edited.status_code == 200
    saved = edited.json()
    ids = [cell["id"] for cell in saved["content"]["cells"]]
    assert (
        client.post(
            f"/api/v1/revisions/{saved['id']}/checked", json={"cell_ids": ids, "checked": True}
        ).status_code
        == 200
    )
    approval = client.post(
        f"/api/v1/revisions/{saved['id']}/approval", json={"allow_warnings": True}
    ).json()
    response = client.post(
        f"/api/v1/tables/{table_id}/exports",
        json={"revision_id": saved["id"], "format": "workbook"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202
    export_id = response.json()["id"]
    # The task queue is shared within this isolated schema; unrelated fixture jobs
    # are cancelled before this workflow executes its own export.
    with app.state.session_factory.begin() as session:
        session.execute(
            update(TaskRow)
            .where(TaskRow.id != export_id, TaskRow.state.in_(("queued", "running")))
            .values(state="cancelled", lease_token=None)
        )
    assert run_once(app.state.queue, "acceptance-export")
    exported = client.get(f"/api/v1/exports/{export_id}").json()
    assert exported["state"] == "succeeded"
    assert exported["manifest"]["content_checksum"] == saved["checksum"]
    assert exported["manifest"]["approval_id"] == approval["id"]
    assert exported["manifest"]["review_status"] == approval["label"]
    downloaded = client.get(f"/api/v1/exports/{export_id}/download")
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        assert "(1,234.50)" in "".join(root.itertext())
    reopened = client.get(f"/api/v1/tables/{table_id}").json()
    assert reopened["revision"]["id"] == saved["id"]
    assert reopened["revision"]["checksum"] == saved["checksum"]
    for suffix in ("", "/download"):
        assert client_b.get(f"/api/v1/exports/{export_id}{suffix}").status_code == 404
