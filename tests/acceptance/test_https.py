"""Real TLS/proxy workflow, confined to the recorded isolated rehearsal."""

import hashlib
import io
import json
import ssl
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from openpyxl import load_workbook

from scripts.rehearse_https import isolated
from tests.acceptance.test_fresh_deploy import Runtime
from tests.fixtures.pdf_factory import two_page_report

pytestmark = pytest.mark.packaged


@pytest.fixture(scope="module")
def https_runtime():
    path = Path(".cache/https-rehearsal.json")
    if not path.is_file():
        pytest.skip("Run the isolated HTTPS rehearsal first")
    state = isolated(json.loads(path.read_text(encoding="utf-8")))
    assert state["status"] == "verified" and state["certificate_verification"]
    value = Runtime()
    assert value.state["project"] == state["project"]
    value.url = state["url"]
    value.tls = ssl.create_default_context(cafile=state["ca_path"])
    suffix = uuid4().hex[:8]
    names = ["https-owner-" + suffix, "https-other-" + suffix]
    value.python(
        "from table_review.app import create_app\nfrom table_review.settings import Settings\n"
        "import json\napp=create_app(Settings.from_env())\nfor name in " + repr(names) + ":\n"
        "    app.state.accounts.create(name,'runtime-fixture-password-123')\n"
        "assert app.state.settings.deployment_mode=='hosted'\n"
        "print(json.dumps({'created':True}))"
    )
    clients, cookie_headers = [], []
    for name in names:
        client = httpx.Client(
            base_url=value.url,
            verify=value.tls,
            trust_env=False,
            timeout=10,
            headers={"Origin": value.url},
        )
        response = client.post(
            "/api/v1/session",
            json={
                "username": name,
                "password": "runtime-fixture-password-123",
            },
        )
        assert response.status_code == 200
        cookie_headers.append(response.headers.get_list("set-cookie"))
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        clients.append(client)
    value.owner, value.other = clients
    value.cookie_headers = cookie_headers
    value.evidence = {"tls_verified": True, "trust_root_installed": False}
    try:
        yield value
    finally:
        for client, document in value.documents:
            client.delete("/api/v1/documents/" + document)
        for client in clients:
            client.delete("/api/v1/session")
            client.close()
        value.python(
            "from table_review.app import create_app\nfrom table_review.settings import Settings\n"
            "from sqlalchemy import select\nfrom table_review.storage.models import AccountRow\n"
            "import json\napp=create_app(Settings.from_env())\n"
            "with app.state.session_factory() as session:\n"
            "    ids=list(session.scalars(select(AccountRow.id).where(AccountRow.username.in_("
            + repr(names)
            + "))))\n"
            "for identity in ids: app.state.accounts.revoke(identity)\n"
            "print(json.dumps({'revoked':len(ids)}))"
        )
        destination = Path("artifacts/https/evidence.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(value.evidence, indent=2), encoding="utf-8")


def test_tls_cookie_origin_csrf_and_request_bounds(https_runtime):
    runtime = https_runtime
    # No OS trust change: the fresh internal CA is accepted only by this test client.
    with pytest.raises(httpx.ConnectError):
        httpx.get(runtime.url + "/api/v1/health/ready", trust_env=False, timeout=5)
    response = runtime.owner.get("/")
    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert "alt-svc" not in response.headers  # No unmapped UDP/internal-port advertisement.
    for headers in runtime.cookie_headers:
        session = next(h for h in headers if h.startswith("tr_session="))
        csrf = next(h for h in headers if h.startswith("tr_csrf="))
        assert "Secure" in session and "HttpOnly" in session and "SameSite=strict" in session
        assert "Secure" in csrf and "SameSite=strict" in csrf and "HttpOnly" not in csrf
    csrf = runtime.owner.headers.pop("X-CSRF-Token")
    rejected = runtime.owner.delete("/api/v1/session")
    assert rejected.status_code == 403 and rejected.json()["code"] == "csrf_rejected"
    runtime.owner.headers["X-CSRF-Token"] = csrf
    rejected = runtime.owner.delete("/api/v1/session", headers={"Origin": "https://other.invalid"})
    assert rejected.status_code == 403 and rejected.json()["code"] == "origin_rejected"
    oversized = runtime.owner.post(
        "/api/v1/tables/" + str(uuid4()) + "/edits",
        content=b"x" * (2 * 1024**2 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    runtime.evidence["authentication_boundary"] = {
        "secure_session_cookie": True,
        "csrf_cookie_secure": True,
        "wrong_origin_rejected": True,
        "missing_csrf_rejected": True,
        "oversized_json_rejected": True,
        "hsts_present": True,
    }


def test_https_source_extraction_review_export_and_signout(https_runtime, tmp_path):
    runtime = https_runtime
    source = two_page_report(tmp_path / "https-report.pdf")
    document = runtime.upload(runtime.owner, source)
    completed = runtime.finish(runtime.owner, runtime.extract(runtime.owner, document))
    table_id = completed["result"]["table_ids"][0]
    table = runtime.owner.get("/api/v1/tables/" + table_id).json()
    revision = table["revision"]
    preview = runtime.owner.get(f"/api/v1/documents/{document['id']}/pages/2/image?scale=1")
    assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")
    original = runtime.owner.get(f"/api/v1/documents/{document['id']}/source")
    assert (
        original.status_code == 200
        and hashlib.sha256(original.content).digest()
        == hashlib.sha256(source.read_bytes()).digest()
    )
    assert runtime.other.get("/api/v1/tables/" + table_id).status_code == 404
    for blank, verdict in ((False, "confirmed_value"), (True, "confirmed_blank")):
        cells = [c["id"] for c in revision["content"]["cells"] if (not c["text"].strip()) == blank]
        if cells:
            reviewed = runtime.owner.post(
                "/api/v1/revisions/" + revision["id"] + "/cell-review",
                json={"cell_ids": cells, "verdict": verdict, "reason": "Automated HTTPS fixture"},
            )
            assert reviewed.status_code == 200
    approved = runtime.owner.post(
        "/api/v1/revisions/" + revision["id"] + "/approval",
        json={"allow_warnings": True},
    )
    assert approved.status_code == 200
    response = runtime.owner.post(
        "/api/v1/tables/" + table_id + "/exports",
        json={"revision_id": revision["id"], "format": "workbook"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202
    export_id = response.json()["id"]
    runtime.wait(
        lambda: (
            runtime.owner.get("/api/v1/exports/" + export_id).json()["state"]
            in ("succeeded", "failed")
        )
    )
    exported = runtime.owner.get("/api/v1/exports/" + export_id).json()
    assert exported["state"] == "succeeded"
    download = runtime.owner.get("/api/v1/exports/" + export_id + "/download")
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == exported["sha256"]
    assert exported["manifest"]["revision_id"] == revision["id"]
    workbook = load_workbook(io.BytesIO(download.content), read_only=True)
    assert workbook.sheetnames == ["Table", "Source", "Changes", "Manifest", "Context", "Timeline"]
    workbook.close()
    assert runtime.other.get("/api/v1/exports/" + export_id + "/download").status_code == 404
    deletion = runtime.owner.delete("/api/v1/documents/" + document["id"])
    assert deletion.status_code == 200
    assert deletion.json()["document_id"] == document["id"]
    assert runtime.owner.delete("/api/v1/session").status_code == 204
    assert runtime.owner.get("/api/v1/documents").status_code == 401
    runtime.evidence["source_to_export"] = {
        "source_preview": True,
        "original_bytes_unchanged": True,
        "real_queued_extraction": True,
        "owner_scoping": True,
        "approval": True,
        "captured_revision": True,
        "workbook_sha256": exported["sha256"],
        "report_deleted": True,
        "signed_out_session_rejected": True,
        "automated_fixture_not_financial_source_audit": True,
    }
