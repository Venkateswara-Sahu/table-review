import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.fixtures.pdf_factory import two_page_report


@pytest.mark.parametrize("kind", ["inspect", "render"])
def test_stuck_pdf_operation_is_killed_without_blocking_ordinary_api_requests(
    app, client, owner, tmp_path, monkeypatch, kind
):
    from table_review.accounts.types import Actor

    actor = Actor(id=owner.id)
    source = two_page_report(tmp_path / "source.pdf")
    doc = app.state.documents.upload(
        actor, io.BytesIO(source.read_bytes()), "source.pdf", uuid4().hex
    )
    boundary = app.state.documents.pdf
    boundary.timeout_seconds = 1.2
    marker = tmp_path / "started"
    executable = tmp_path / "stuck.py"
    executable.write_text(
        "import os,time\nfrom pathlib import Path\n"
        + "Path("
        + repr(str(marker))
        + ").write_text(str(os.getpid()))\n"
        + "time.sleep(60)\n",
        encoding="utf-8",
    )
    command = boundary.command

    def stuck_command(request, result):
        operation = json.loads(request.read_text())["kind"]
        if operation == kind:
            return [sys.executable, str(executable)]
        return command(request, result)

    monkeypatch.setattr(boundary, "command", stuck_command)
    path = (
        f"/api/v1/documents/{doc.id}/inspect"
        if kind == "inspect"
        else f"/api/v1/documents/{doc.id}/pages/2/image"
    )
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(client.get, path)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        assert client.get("/api/v1/documents").status_code == 200
        ready = client.get("/api/v1/health/ready")
        assert ready.json()["components"]["database"] and ready.json()["components"]["journal"]
        response = pending.result(timeout=5)
    assert response.status_code == 422 and response.json()["code"] == "pdf_timeout"
    import psutil

    assert not psutil.pid_exists(int(marker.read_text()))
    assert "source.pdf" not in response.text
    monkeypatch.setattr(boundary, "command", command)
    boundary.timeout_seconds = 30
    assert client.get(f"/api/v1/documents/{doc.id}/pages/2").status_code == 200
    assert client.get(f"/api/v1/documents/{doc.id}/pages/2/image").status_code == 200
