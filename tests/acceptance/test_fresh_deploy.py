"""Real private-container checks; require the explicit isolated runtime fixture."""

import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests.fixtures.pdf_factory import two_page_report

pytestmark = pytest.mark.packaged


class Runtime:
    def __init__(self):
        self.state = json.loads(Path(".cache/runtime-state.json").read_text(encoding="utf-8"))
        assert re.fullmatch(r"table-review-acceptance-[a-f0-9]{32}", self.state["project"])
        self.url = self.state["url"]
        self.evidence = {}
        self.documents = []

    def container(self, service):
        return self.state["project"] + "-" + service + "-1"

    def docker(self, *args, input=None, timeout=120):
        result = subprocess.run(
            ["docker", *args],
            input=input,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise AssertionError(
                "Scoped Docker operation failed; no private stdout/stderr included"
            )
        return result.stdout

    def python(self, code, service="api", timeout=120):
        # Reports, database URLs and credentials never enter command arguments.
        wrapper = (
            "try:\n"
            + "\n".join("    " + line for line in code.splitlines())
            + '\nexcept Exception as error:\n    print("probe_failed:"+type(error).__name__)\n    raise SystemExit(1)\n'
        )
        return json.loads(
            self.docker(
                "exec", "-i", self.container(service), "python", "-", input=wrapper, timeout=timeout
            )
        )

    def login(self, username):
        client = httpx.Client(base_url=self.url, headers={"Origin": self.url}, timeout=5)
        r = client.post(
            "/api/v1/session",
            json={"username": username, "password": "runtime-fixture-password-123"},
        )
        assert r.status_code == 200
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        return client

    def upload(self, client, path, filename="runtime-fixture.pdf"):
        response = client.post(
            "/api/v1/documents",
            files={"file": (filename, path.read_bytes(), "application/pdf")},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert response.status_code == 201
        doc = response.json()
        self.documents.append((client, doc["id"]))
        self.wait(
            lambda: (
                client.get("/api/v1/documents/" + doc["id"]).json()["preparation_state"] == "ready"
            )
        )
        return doc

    def wait(self, predicate, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.25)
        raise AssertionError("The bounded runtime condition was not reached")

    def extract(self, client, doc, page=2):
        r = client.post(
            "/api/v1/documents/" + doc["id"] + "/runs",
            json={"pages": [page]},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert r.status_code == 202
        return r.json()

    def finish(self, client, task):
        self.wait(
            lambda: (
                client.get("/api/v1/tasks/" + task["id"]).json()["state"]
                in ("succeeded", "failed", "cancelled")
            )
        )
        result = client.get("/api/v1/tasks/" + task["id"]).json()
        assert result["state"] == "succeeded", result["failure_code"]
        return result


@pytest.fixture(scope="module")
def runtime():
    value = Runtime()
    suffix = uuid4().hex[:8]
    names = ["runtime-a-" + suffix, "runtime-b-" + suffix]
    value.python(
        """import json
from table_review.app import create_app
from table_review.settings import Settings
app=create_app(Settings.from_env())
for username in """
        + repr(names)
        + """:
    app.state.accounts.create(username,'runtime-fixture-password-123')
print(json.dumps({'created':True}))"""
    )
    value.owner = value.login(names[0])
    value.other = value.login(names[1])
    yield value
    for client, id in value.documents:
        client.delete("/api/v1/documents/" + id)
    for client in (value.owner, value.other):
        client.close()
    Path("artifacts/runtime").mkdir(parents=True, exist_ok=True)
    Path("artifacts/runtime/evidence.json").write_text(
        json.dumps(value.evidence, indent=2), encoding="utf-8"
    )


def test_fresh_packaged_app_is_ready_with_private_worker_boundary(runtime):
    response = httpx.get(runtime.url + "/api/v1/health/ready", timeout=5)
    assert response.status_code == 200 and all(response.json()["components"].values())
    inspect = json.loads(runtime.docker("inspect", runtime.container("worker")))[0]
    assert inspect["HostConfig"]["NetworkMode"] == "none"
    assert inspect["HostConfig"]["Memory"] == 6 * 1024**3
    assert inspect["Config"]["User"] == "10001:10001"
    assert inspect["HostConfig"]["ReadonlyRootfs"] is True
    assert inspect["HostConfig"]["CapDrop"] == ["ALL"]
    db = json.loads(runtime.docker("inspect", runtime.container("db")))[0]
    assert all(value is None for value in db["NetworkSettings"]["Ports"].values())
    assert runtime.state["fresh_deployment"] is True
    runtime.evidence["fresh_boundary"] = {
        "ready": response.json(),
        "worker_uid": 10001,
        "worker_network": "none",
        "hard_memory_bytes": 6 * 1024**3,
        "db_exposed": False,
    }
    assert httpx.get(runtime.url + "/", timeout=5).status_code == 200


def test_packaged_readiness_detects_absent_models_and_worker_has_no_outbound_route(runtime):
    result = runtime.python("""import json
from pathlib import Path
from table_review.app import create_app
from table_review.settings import Settings
from table_review.runtime import readiness
app=create_app(Settings.from_env().model_copy(update={'model_path':Path('/absent-models')}))
print(json.dumps(readiness(app)))
app.state.session_factory.kw['bind'].dispose()""")
    assert result["ready"] is False and result["components"]["models"] is False
    assert all(value for key, value in result["components"].items() if key != "models")
    network = runtime.python(
        """import json,socket
with socket.socket() as client:
    client.settimeout(1)
    unreachable=client.connect_ex(('1.1.1.1',443))!=0
print(json.dumps({'interfaces':[name for _,name in socket.if_nameindex()], 'outbound_denied':unreachable}))""",
        service="worker",
    )
    assert network["interfaces"] == ["lo"] and network["outbound_denied"] is True
    runtime.evidence["absent_models"] = result
    runtime.evidence["worker_network_probe"] = network


def test_api_remains_responsive_during_real_extraction_and_owner_access_is_scoped(
    runtime, tmp_path
):
    doc = runtime.upload(runtime.owner, two_page_report(tmp_path / "runtime.pdf"))
    task = runtime.extract(runtime.owner, doc)
    runtime.wait(
        lambda: runtime.owner.get("/api/v1/tasks/" + task["id"]).json()["state"] == "running"
    )
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: httpx.get(runtime.url + "/api/v1/health/ready", timeout=5), range(8))
        )
    assert all(r.status_code == 200 for r in responses)
    completed = runtime.finish(runtime.owner, task)
    table_id = completed["result"]["table_ids"][0]
    table = runtime.owner.get("/api/v1/tables/" + table_id).json()
    texts = [c["text"] for c in table["revision"]["content"]["cells"]]
    assert "1,234.50" in texts and "(123.45)" in texts
    for route in (
        "/api/v1/documents/" + doc["id"],
        "/api/v1/documents/" + doc["id"] + "/pages/2",
        "/api/v1/tasks/" + task["id"],
        "/api/v1/tables/" + table_id,
        "/api/v1/tables/" + table_id + "/revisions/" + table["revision"]["id"],
    ):
        assert runtime.other.get(route).status_code == 404
    manifest = table["revision"]["extraction_manifest"]
    assert manifest["bounded_runner"]["hard_memory_enforcement"] is True
    assert manifest["bounded_runner"]["container_memory_limit_bytes"] == 6 * 1024**3
    runtime.evidence["real_parser"] = {
        "elapsed_to_terminal_after_ready_probe": time.monotonic() - started,
        "ready_responses": len(responses),
        "original_page": 2,
        "table_count": len(completed["result"]["table_ids"]),
        "bounded_runner": manifest["bounded_runner"],
        "source_sha256": manifest["source_sha256"],
        "code_revision": manifest["code_revision"],
    }


def test_fourth_queued_request_is_rejected_and_cancellation_frees_the_bound(runtime, tmp_path):
    doc = runtime.upload(runtime.owner, two_page_report(tmp_path / "queued.pdf"))
    runtime.docker("stop", runtime.container("worker"))
    try:
        queued = [runtime.extract(runtime.owner, doc) for _ in range(3)]
        response = runtime.owner.post(
            "/api/v1/documents/" + doc["id"] + "/runs",
            json={"pages": [2]},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert response.status_code == 429 and response.json()["code"] == "queue_full"
        for task in queued:
            assert runtime.owner.post("/api/v1/tasks/" + task["id"] + "/cancel").status_code == 200
        runtime.evidence["queue_limit"] = {
            "accepted": 3,
            "fourth_status": 429,
            "all_cancelled": True,
        }
    finally:
        runtime.docker("start", runtime.container("worker"))
    runtime.wait(
        lambda: httpx.get(runtime.url + "/api/v1/health/ready", timeout=5).status_code == 200,
        timeout=30,
    )


def test_kill_restart_recovers_with_one_published_result_and_bounded_attempts(runtime, tmp_path):
    doc = runtime.upload(runtime.owner, two_page_report(tmp_path / "restart.pdf"))
    task = runtime.extract(runtime.owner, doc)
    runtime.wait(
        lambda: runtime.owner.get("/api/v1/tasks/" + task["id"]).json()["state"] == "running"
    )
    runtime.docker("kill", "--signal", "KILL", runtime.container("worker"))
    runtime.docker("start", runtime.container("worker"))
    completed = runtime.finish(runtime.owner, task)
    assert completed["attempt_count"] == 2
    assert len(completed["result"]["table_ids"]) == 1
    listed = runtime.owner.get("/api/v1/documents/" + doc["id"] + "/tables?page_no=2").json()[
        "items"
    ]
    assert len(listed) == 1
    runtime.evidence["kill_restart"] = {
        "attempt_count": completed["attempt_count"],
        "published_tables": len(listed),
        "recovered": True,
    }


def test_timeout_and_parent_death_terminate_even_escaped_descendants(runtime):
    code = r"""
import json,os,sys,tempfile,time,subprocess
from pathlib import Path
import psutil
from table_review.jobs.runner import run_child,ProcessingFailure
from table_review.jobs.types import ProcessingLimits

def live(pid):
    try:return psutil.Process(pid).status()!=psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:return False

with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
    root=Path(temporary)
    child="import json,subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'],start_new_session=True); Path(sys.argv[1]).write_text(json.dumps({'child':p.pid})); time.sleep(300)"
    timed=root/'timed.json'
    outcome=None
    try:run_child([sys.executable,'-c',child,str(timed)],ProcessingLimits(timeout_seconds=3),work_dir=root/'timeout')
    except ProcessingFailure as error:outcome=error.code
    pid=json.loads(timed.read_text())['child']
    timeout_live=live(pid)
    orphan=root/'orphan.json'
    launcher="from pathlib import Path; import sys; from table_review.jobs.runner import run_child; from table_review.jobs.types import ProcessingLimits; run_child([sys.executable,'-c',"+repr(child)+", "+repr(str(orphan))+"],ProcessingLimits(timeout_seconds=30),work_dir=Path("+repr(str(root/'parent'))+"))"
    parent=subprocess.Popen([sys.executable,'-c',launcher],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+10
    while not orphan.exists() and time.monotonic()<deadline:time.sleep(.05)
    ids=json.loads(orphan.read_text())
    descendants=[p.pid for p in psutil.Process(parent.pid).children(recursive=True)]
    parent.kill();parent.wait(5)
    deadline=time.monotonic()+8
    while any(live(pid) for pid in descendants) and time.monotonic()<deadline:time.sleep(.05)
    print(json.dumps({'timeout_code':outcome,'timeout_descendant_alive':timeout_live,'parent_death_descendants_alive':sum(live(pid) for pid in descendants),'parent_death_descendant_count':len(descendants)}))
"""
    result = runtime.python(code, service="worker", timeout=45)
    assert result["timeout_code"] == "timeout"
    assert result["timeout_descendant_alive"] is False
    assert result["parent_death_descendant_count"] >= 3
    assert result["parent_death_descendants_alive"] == 0
    runtime.evidence["process_isolation"] = result


def test_kernel_memory_limit_produces_resource_failure_with_responsive_api(runtime):
    code = r"""
import json,sys,tempfile,time
from pathlib import Path
from table_review.jobs.runner import run_child,ProcessingFailure,memory_contract,_oom_count
from table_review.jobs.types import ProcessingLimits
before=_oom_count()
with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
    code="blocks=[]\nfor _ in range(80): blocks.append(bytearray(100*1024**2))\n"
    outcome=None
    try:run_child([sys.executable,'-c',code],ProcessingLimits(timeout_seconds=45,memory_bytes=8*1024**3),work_dir=Path(temporary))
    except ProcessingFailure as error:outcome=error.code
print(json.dumps({'failure_code':outcome,'oom_kill_delta':_oom_count()-before,'memory_contract':memory_contract()}))
"""
    result = runtime.python(code, service="worker", timeout=65)
    assert result["failure_code"] == "resource_exhaustion"
    assert result["oom_kill_delta"] >= 1
    assert result["memory_contract"]["container_memory_limit_bytes"] == 6 * 1024**3
    response = httpx.get(runtime.url + "/api/v1/health/ready", timeout=5)
    assert response.status_code == 200
    runtime.evidence["kernel_memory_probe"] = result | {"ready_after": True}


def test_api_pdf_children_are_bounded_without_stopping_the_serving_process(runtime):
    marker = "/tmp/pdf-probe-" + uuid4().hex
    code = r"""
import json,sys,tempfile
from pathlib import Path
from table_review.documents.bounded import PdfOperations
from table_review.documents.errors import DocumentError
from table_review.settings import Settings

with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
    boundary=PdfOperations(Settings.from_env(),Path(temporary))
    boundary.timeout_seconds=4
    codes=[]
    commands=[
        "import time; time.sleep(60)",
        "import json,sys; from pathlib import Path; from table_review.documents.pdf_child import apply_memory_limit; apply_memory_limit(512*1024**2)\ntry: allocation=bytearray(1024**3)\nexcept MemoryError: Path(sys.argv[1]).write_text(json.dumps({'failure_code':'resource_exhaustion','status':422}))\nelse: raise SystemExit(9)",
    ]
    for command in commands:
        Path(MARKER).touch()
        boundary.command=lambda request,result: [sys.executable,'-c',command,str(result)]
        try:
            with boundary._operation('inspect',Path('/unused-fixture.pdf')):
                raise AssertionError('Expected bounded rejection')
        except DocumentError as error:
            codes.append(error.code)
    print(json.dumps({'codes':codes,'memory_bytes':boundary.settings.pdf_memory_bytes,'probe_process_survived':True}))
    Path(MARKER).unlink()
"""
    code = code.replace("MARKER", repr(marker))
    with ThreadPoolExecutor(max_workers=2) as pool:
        probe = pool.submit(runtime.python, code, timeout=20)
        runtime.wait(
            lambda: runtime.python(
                "import json; from pathlib import Path; print(json.dumps(Path("
                + repr(marker)
                + ").exists()))"
            ),
            timeout=5,
        )
        assert not probe.done()
        responses = [httpx.get(runtime.url + "/api/v1/health/ready", timeout=5) for _ in range(8)]
        result = probe.result()
    assert result["codes"] == ["pdf_timeout", "resource_exhaustion"]
    assert result["memory_bytes"] == 512 * 1024**2
    assert result["probe_process_survived"] is True
    assert all(response.status_code == 200 for response in responses)
    runtime.evidence["api_pdf_boundary"] = result | {
        "ready_responses": len(responses),
        "linux_limit": "RLIMIT_AS",
        "fixture": "injected sleeping and oversized allocation child commands",
        "fixture_timeout_seconds": 4,
        "api_kernel_oom_claimed": False,
    }


def test_packaged_restore_reproduces_reviewed_export_and_honors_later_deletion_revocation(
    runtime, tmp_path
):
    import hashlib

    doc = runtime.upload(runtime.owner, two_page_report(tmp_path / "retained.pdf"))
    extracted = runtime.finish(runtime.owner, runtime.extract(runtime.owner, doc))
    table_id = extracted["result"]["table_ids"][0]
    view = runtime.owner.get("/api/v1/tables/" + table_id).json()
    revision = view["revision"]
    response = runtime.owner.post(
        "/api/v1/revisions/" + revision["id"] + "/checked",
        json={"cell_ids": [c["id"] for c in revision["content"]["cells"]], "checked": True},
    )
    assert response.status_code == 200
    approved = runtime.owner.post(
        "/api/v1/revisions/" + revision["id"] + "/approval", json={"allow_warnings": True}
    )
    assert approved.status_code == 200
    response = runtime.owner.post(
        "/api/v1/tables/" + table_id + "/exports",
        json={"revision_id": revision["id"], "format": "workbook"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202
    export = response.json()
    runtime.wait(
        lambda: (
            runtime.owner.get("/api/v1/exports/" + export["id"]).json()["state"]
            in ("succeeded", "failed")
        )
    )
    final = runtime.owner.get("/api/v1/exports/" + export["id"]).json()
    assert final["state"] == "succeeded"
    original = runtime.owner.get("/api/v1/exports/" + export["id"] + "/download").content
    expected = hashlib.sha256(original).hexdigest()
    doomed = runtime.upload(runtime.owner, two_page_report(tmp_path / "doomed.pdf"))
    args = {
        "export_id": export["id"],
        "revision_id": revision["id"],
        "table_id": table_id,
        "doomed_id": doomed["id"],
        "expected_sha": expected,
        "approved_id": approved.json()["id"],
    }
    code = (
        """import json,hashlib,shutil,io,zipfile
from datetime import UTC,datetime
from pathlib import Path
from uuid import UUID,uuid4
from sqlalchemy import text
from table_review.app import create_app
from table_review.settings import Settings
from table_review.accounts.types import Actor
from table_review.lifecycle.backup import create_backup,restore_backup
from table_review.exports.types import ExportInput
from table_review.exports.workbook import write_workbook
from table_review.jobs.models import TaskRow
from table_review.storage.models import DocumentRow
args="""
        + repr(args)
        + """
settings=Settings.from_env()
app=create_app(settings)
with app.state.session_factory() as session:
    task=session.get(TaskRow,UUID(args['export_id']))
    owner_id=task.owner_id
    captured=task.input['snapshot']
actor=Actor(id=owner_id)
revoked=app.state.accounts.create('recovery-'+uuid4().hex[:8],'runtime-fixture-password-123')
grant=app.state.accounts.sign_in(revoked.username,'runtime-fixture-password-123')
backup=settings.backup_root/(uuid4().hex+'.trbackup')
create_backup(settings,backup,datetime.now(UTC))
app.state.deletion.request(actor,UUID(args['doomed_id']),datetime.now(UTC))
app.state.accounts.revoke(revoked.id)
target=settings.model_copy(update={'database_schema':'test_'+uuid4().hex,'blob_root':Path('/data/restore-'+uuid4().hex),'initialize_journal':False})
restored=None
try:
    result=restore_backup(backup,app.state.journal,target)
    restored=create_app(target)
    actual=restored.state.exports.download(actor,UUID(args['export_id'])).read_bytes()
    approval=restored.state.reviews.current_approval(actor,UUID(args['revision_id']))
    generated=write_workbook(ExportInput.model_validate(captured),target.blob_root/'reproduced.xlsx')
    with zipfile.ZipFile(io.BytesIO(actual)) as first, zipfile.ZipFile(generated.path) as second:
        differences={name:{'original':hashlib.sha256(first.read(name)).hexdigest(),'regenerated':hashlib.sha256(second.read(name)).hexdigest()} for name in first.namelist() if first.read(name)!=second.read(name)}
    with restored.state.session_factory() as session:
        deleted=session.get(DocumentRow,UUID(args['doomed_id']))
        removed=bool(deleted.deleted_at and deleted.active_files_removed_at)
    denied=False
    try:restored.state.accounts.resolve_session(grant.token)
    except PermissionError:denied=True
    print(json.dumps({'result':result.model_dump(mode='json'),'original_sha256':args['expected_sha'],'restored_sha256':hashlib.sha256(actual).hexdigest(),'regenerated_sha256':hashlib.sha256(generated.path.read_bytes()).hexdigest(),'approval_preserved':str(approval.id)==args['approved_id'],'deleted_files_removed':removed,'later_session_denied':denied,'differing_members':differences}))
finally:
    if restored:restored.state.session_factory.kw['bind'].dispose()
    with app.state.session_factory.kw['bind'].begin() as connection:
        connection.execute(text('DROP SCHEMA "'+target.database_schema+'" CASCADE'))
    assert target.blob_root.parent==Path('/data') and target.blob_root.name.startswith('restore-')
    shutil.rmtree(target.blob_root)
    backup.unlink(missing_ok=True)
"""
    )
    result = runtime.python(code, timeout=90)
    runtime.evidence["packaged_restore"] = result
    assert result["result"]["ready"] is True
    assert result["restored_sha256"] == result["regenerated_sha256"] == expected
    assert (
        result["approval_preserved"]
        and result["deleted_files_removed"]
        and result["later_session_denied"]
    )


def test_real_annual_report_moves_through_source_review_and_exact_export(runtime):
    import hashlib
    import io

    from openpyxl import load_workbook

    metadata = json.loads(Path("evaluation/corpus-metadata.json").read_text(encoding="utf-8"))
    report = next(row for row in metadata["reports"] if row["id"] == "ibm-2024")
    case = next(row for row in metadata["cases"] if row["report_id"] == report["id"])
    path = (Path("evaluation") / report["source_path"]).resolve()
    if not path.is_file():
        pytest.skip(
            "Local copyrighted annual-report source is required; synthetic runtime tests are separate"
        )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == report["sha256"]
    doc = runtime.upload(runtime.owner, path, filename="annual-report.pdf")
    completed = runtime.finish(
        runtime.owner, runtime.extract(runtime.owner, doc, page=case["page"])
    )
    table_id = completed["result"]["table_ids"][0]
    table = runtime.owner.get("/api/v1/tables/" + table_id).json()
    revision = table["revision"]
    assert all(cell["reference"]["page"] == case["page"] for cell in revision["content"]["cells"])
    assert revision["extraction_manifest"]["source_sha256"] == report["sha256"]
    edited = runtime.owner.post(
        "/api/v1/tables/" + table_id + "/edits",
        json={
            "base_revision_id": revision["id"],
            "operation": {
                "kind": "set_notes",
                "unit_notes": [
                    "Source scale must be compared with the original page; no conversion applied."
                ],
            },
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert edited.status_code == 200
    saved = edited.json()
    assert (
        runtime.owner.post(
            "/api/v1/revisions/" + saved["id"] + "/checked",
            json={"cell_ids": [cell["id"] for cell in saved["content"]["cells"]], "checked": True},
        ).status_code
        == 200
    )
    approved = runtime.owner.post(
        "/api/v1/revisions/" + saved["id"] + "/approval", json={"allow_warnings": True}
    )
    assert approved.status_code == 200
    requested = runtime.owner.post(
        "/api/v1/tables/" + table_id + "/exports",
        json={"revision_id": saved["id"], "format": "workbook"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert requested.status_code == 202
    export_id = requested.json()["id"]
    runtime.wait(
        lambda: (
            runtime.owner.get("/api/v1/exports/" + export_id).json()["state"]
            in ("succeeded", "failed")
        )
    )
    final = runtime.owner.get("/api/v1/exports/" + export_id).json()
    assert final["state"] == "succeeded"
    assert final["manifest"]["approval_id"] == approved.json()["id"]
    assert final["manifest"]["content_checksum"] == saved["checksum"]
    blob = runtime.owner.get("/api/v1/exports/" + export_id + "/download")
    assert blob.status_code == 200
    workbook = load_workbook(io.BytesIO(blob.content), read_only=True, data_only=False)
    try:
        assert workbook.sheetnames == ["Table", "Source", "Changes", "Manifest"]
        sources = list(workbook["Source"].iter_rows(min_row=2, values_only=True))
        assert len(sources) == len(saved["content"]["cells"])
        assert [(row[0], row[4] or "", row[5] or "") for row in sources] == [
            (cell["id"], cell["raw_text"], cell["text"]) for cell in saved["content"]["cells"]
        ]
    finally:
        workbook.close()
    reopened = runtime.owner.get("/api/v1/tables/" + table_id).json()
    assert reopened["revision"]["id"] == saved["id"]
    assert runtime.other.get("/api/v1/exports/" + export_id + "/download").status_code == 404
    runtime.evidence["real_annual_workflow"] = {
        "source_sha256": report["sha256"],
        "original_page": case["page"],
        "cell_count": len(sources),
        "revision_preserved": True,
        "raw_text_preserved": True,
        "export_sha256": hashlib.sha256(blob.content).hexdigest(),
        "automated_checking_only": True,
        "financial_correctness_audited": False,
    }
