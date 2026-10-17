import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psutil
import pytest

from tests.fixtures.pdf_factory import two_page_report


@pytest.fixture(autouse=True)
def stop_test_tasks(session_factory):
    from sqlalchemy import update

    from table_review.jobs.models import TaskRow

    def stop():
        with session_factory.begin() as session:
            session.execute(
                update(TaskRow)
                .where(TaskRow.state.in_(("queued", "running", "cancelled")))
                .values(state="cancelled", cancellation_requested=True, lease_token=None)
            )

    stop()
    yield
    stop()


@pytest.fixture
def queue(repository):
    from table_review.jobs.queue import TaskQueue
    from table_review.settings import Settings

    return TaskQueue(repository, Settings())


@pytest.fixture
def document(repository, owner_ids):
    return repository.create_document(
        owner_ids[0],
        filename="report.pdf",
        source_sha256="a" * 64,
        byte_count=100,
        page_count=2,
        blob_key=uuid4().hex,
    )


def enqueue(queue, document, owner_ids, **kwargs):
    return queue.enqueue(
        owner_ids[0], document.id, "extract", {"pages": [2]}, uuid4().hex, **kwargs
    )


def successful_outcome():
    from table_review.jobs.types import TaskOutcome

    return TaskOutcome(status="succeeded", result={"message": "finished"})


def extracted_outcome(*, status="success", literal="100"):
    from table_review.documents.types import PageBox, SourceReference
    from table_review.extraction.types import ExtractedTable, ExtractionResult
    from table_review.jobs.types import TaskOutcome
    from tests.fixtures.tables import simple_table

    reference = SourceReference(
        page=2,
        precision="table",
        box=PageBox(
            page=2,
            left=10,
            top=10,
            right=110,
            bottom=110,
            page_width=612,
            page_height=792,
        ),
    )
    content = simple_table(1, 1)
    content = content.model_copy(
        update={
            "source_reference": reference,
            "cells": (content.cells[0].model_copy(update={"raw_text": literal, "text": literal}),),
        }
    )
    result = ExtractionResult(
        tables=()
        if status == "failure"
        else (
            ExtractedTable(
                id="extractor-index-0",
                page_no=2,
                region=reference.box,
                content=content,
                source_tokens=(),
            ),
        ),
        page_info=(),
        warnings=() if status == "success" else ("Conversion failed",),
        manifest={
            "source_sha256": "a" * 64,
            "conversion_outcome": status,
            "page_outcomes": [{"original_page": 2, "status": status}],
        },
    )
    return TaskOutcome(status="succeeded", extraction=result.model_dump(mode="json"))


def test_failed_conversion_persists_a_run_without_publishing_tables(queue, document, owner_ids):
    task = enqueue(queue, document, owner_ids)
    lease = queue.claim(datetime.now(UTC))
    assert queue.complete(lease, extracted_outcome(status="failure"))
    saved = queue.get(owner_ids[0], task.id)
    assert saved.state == "failed" and saved.failure_code == "parse_failure"
    run = queue.runs(owner_ids[0], document.id)[0]
    assert run["table_ids"] == []
    assert run["manifest"]["page_outcomes"][0]["status"] == "failure"
    assert saved.result["run_id"] == run["id"]


def test_extraction_publication_retains_nonnegative_persistence_timing(queue, document, owner_ids):
    enqueue(queue, document, owner_ids)
    assert queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    run = queue.runs(owner_ids[0], document.id)[0]
    timing = run["manifest"]["publication_timing"]
    assert timing["seconds"] >= 0 and timing["table_count"] == 1
    assert timing["semantics"] == "publication_work_before_final_flush_and_commit"


def test_rerun_comparison_captures_original_and_edited_revision_without_overwrite(
    queue,
    document,
    owner_ids,
    repository,
):
    from table_review.storage.models import EditEventInput

    first = enqueue(queue, document, owner_ids)
    assert queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    first_run = queue.get(owner_ids[0], first.id).result
    table_id = __import__("uuid").UUID(first_run["table_ids"][0])
    table = repository.get_table(owner_ids[0], table_id)
    revision = repository.get_revision(owner_ids[0], table.id, table.current_revision_id)
    content = revision.content.model_copy(
        update={"cells": (revision.content.cells[0].model_copy(update={"text": "corrected-100"}),)}
    )
    edited = repository.save_revision(
        owner_ids[0],
        table.id,
        revision.id,
        content,
        EditEventInput(idempotency_key=uuid4(), operation={"kind": "test-correction"}),
    )
    second = enqueue(queue, document, owner_ids)
    assert queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome(literal="101"))
    result = queue.get(owner_ids[0], second.id).result
    comparison = queue.runs(owner_ids[0], document.id)[0]["manifest"]["comparison"]
    assert comparison["baseline_run_id"] == first_run["run_id"]
    match = comparison["matches"][0]
    assert match["original_revision_id"] == str(revision.id)
    assert match["reviewed_revision_id"] == str(edited.id)
    assert match["original_diff"]["changed_cells"][0]["before"]["text"] == "100"
    assert match["reviewed_diff"]["changed_cells"][0]["before"]["text"] == "corrected-100"
    assert result["table_ids"] != first_run["table_ids"]
    assert repository.get_table(owner_ids[0], table.id).current_revision_id == edited.id


def test_different_request_scope_is_not_reported_as_removed_tables(queue, document, owner_ids):
    enqueue(queue, document, owner_ids)
    queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    queue.enqueue(owner_ids[0], document.id, "extract", {"pages": [1]}, uuid4().hex)
    queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    comparison = queue.runs(owner_ids[0], document.id)[0]["manifest"]["comparison"]
    assert comparison["status"] == "no_comparable_baseline"


def test_baseline_lookup_does_not_hydrate_unrelated_run_payloads(queue, document, owner_ids):
    from sqlalchemy import event

    from table_review.jobs.models import ExtractionRunRow

    enqueue(queue, document, owner_ids)
    queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    for _ in range(25):
        queue.enqueue(owner_ids[0], document.id, "extract", {"pages": [1]}, uuid4().hex)
        queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    loaded = []

    def on_loaded(session, instance):
        if isinstance(instance, ExtractionRunRow):
            loaded.append(instance.id)

    event.listen(queue.session_factory, "loaded_as_persistent", on_loaded)
    try:
        enqueue(queue, document, owner_ids)
        queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    finally:
        event.remove(queue.session_factory, "loaded_as_persistent", on_loaded)
    assert len(loaded) <= 1


def test_run_summaries_defer_detail_payloads_and_detail_route_enforces_ownership(
    app,
    owner,
    client,
    client_b,
):
    doc = app.state.repository.create_document(
        owner.id,
        filename="run.pdf",
        source_sha256="a" * 64,
        byte_count=100,
        page_count=2,
        blob_key=uuid4().hex,
    )
    queue = app.state.queue
    task = queue.enqueue(owner.id, doc.id, "extract", {"pages": [2]}, uuid4().hex)
    queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    run_id = queue.get(owner.id, task.id).result["run_id"]
    summary = client.get(f"/api/v1/documents/{doc.id}/runs?include_details=false&limit=20").json()[
        "items"
    ][0]
    assert "comparison" not in summary["manifest"]
    detail = client.get(f"/api/v1/documents/{doc.id}/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["manifest"]["page_outcomes"][0]["status"] == "success"
    assert client_b.get(f"/api/v1/documents/{doc.id}/runs/{run_id}").status_code == 404
    assert client.get(f"/api/v1/documents/{uuid4()}/runs/{run_id}").status_code == 404


def test_rerun_retains_existing_approval_but_new_tables_start_unchecked(app, owner):
    from uuid import UUID

    from table_review.accounts.types import Actor

    actor = Actor(id=owner.id)
    doc = app.state.repository.create_document(
        owner.id,
        filename="approval.pdf",
        source_sha256="a" * 64,
        byte_count=100,
        page_count=2,
        blob_key=uuid4().hex,
    )
    queue = app.state.queue
    task = queue.enqueue(owner.id, doc.id, "extract", {"pages": [2]}, uuid4().hex)
    queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    table_id = UUID(queue.get(owner.id, task.id).result["table_ids"][0])
    table = app.state.repository.get_table(owner.id, table_id)
    app.state.reviews.mark_checked(actor, table.current_revision_id, ("r0c0",), True)
    approval = app.state.reviews.approve(actor, table.current_revision_id, allow_warnings=True)
    rerun = queue.enqueue(owner.id, doc.id, "extract", {"pages": [2]}, uuid4().hex)
    queue.complete(queue.claim(datetime.now(UTC)), extracted_outcome())
    new_id = UUID(queue.get(owner.id, rerun.id).result["table_ids"][0])
    new_table = app.state.repository.get_table(owner.id, new_id)
    assert (
        app.state.reviews.state(actor, table.current_revision_id).current_approval.id == approval.id
    )
    new_state = app.state.reviews.state(actor, new_table.current_revision_id)
    assert new_state.checked_cell_ids == () and new_state.current_approval is None


def test_installed_code_identity_changes_when_source_changes(tmp_path):
    from table_review.jobs.runner import code_identity

    root = tmp_path / "installed-code"
    root.mkdir()
    (root / "sample.py").write_text("VERSION = 1\n")
    first = code_identity(root)
    (root / "sample.py").write_text("VERSION = 2\n")
    second = code_identity(root)
    assert first["code_revision"] != second["code_revision"]
    assert first["code_revision_kind"] == "installed_python_source_sha256"
    assert first["environment"]["python"]


def test_single_worker_claim_and_account_capacity(queue, document, owner_ids):
    from table_review.jobs.queue import QueueFull

    tasks = [enqueue(queue, document, owner_ids) for _ in range(3)]
    with pytest.raises(QueueFull):
        enqueue(queue, document, owner_ids)
    now = datetime.now(UTC)
    with ThreadPoolExecutor(2) as pool:
        leases = list(pool.map(lambda _: queue.claim(now), range(2)))
    lease = next(x for x in leases if x)
    assert sum(x is not None for x in leases) == 1
    assert lease.task_id == tasks[0].id
    assert lease.attempt_no == 1
    assert (lease.expires_at - now).total_seconds() == 60
    assert queue.complete(lease, successful_outcome(), now=now)
    assert queue.claim(now).task_id == tasks[1].id


def test_stale_attempt_cannot_publish(queue, document, owner_ids):
    task = enqueue(queue, document, owner_ids)
    now = datetime.now(UTC)
    expired = queue.claim(now)
    replacement = queue.claim(now + timedelta(seconds=61))
    assert replacement.task_id == task.id and replacement.attempt_no == 2
    assert replacement.token != expired.token
    assert not queue.complete(expired, successful_outcome(), now=now + timedelta(seconds=62))
    assert queue.complete(replacement, successful_outcome(), now=now + timedelta(seconds=62))
    assert queue.get(owner_ids[0], task.id).attempt_count == 2


def test_heartbeat_extends_only_active_lease(queue, document, owner_ids):
    from table_review.jobs.queue import StaleLease

    enqueue(queue, document, owner_ids)
    now = datetime.now(UTC)
    lease = queue.claim(now)
    queue.heartbeat(lease, now + timedelta(seconds=15))
    assert queue.claim(now + timedelta(seconds=61)) is None
    with pytest.raises(StaleLease):
        queue.heartbeat(lease, now + timedelta(seconds=76))
    assert queue.claim(now + timedelta(seconds=76)).attempt_no == 2


def test_expiry_exhausts_attempt_budget(queue, document, owner_ids):
    task = enqueue(queue, document, owner_ids)
    now = datetime.now(UTC)
    queue.claim(now)
    queue.claim(now + timedelta(seconds=61))
    assert queue.claim(now + timedelta(seconds=122)) is None
    record = queue.get(owner_ids[0], task.id)
    assert record.state == "failed" and record.failure_code == "infrastructure_interruption"
    assert record.attempt_count == 2


def test_timeout_budget_is_shared_between_attempts(queue, document, owner_ids):
    from table_review.jobs.types import TaskOutcome

    task = enqueue(queue, document, owner_ids)
    now = datetime.now(UTC)
    lease = queue.claim(now)
    # A long attempt stays leased through periodic heartbeats.
    for seconds in range(15, 451, 15):
        queue.heartbeat(lease, now + timedelta(seconds=seconds))
    queue.complete(
        lease,
        TaskOutcome(status="failed", failure_code="storage_failure"),
        now=now + timedelta(seconds=450),
    )
    second = queue.claim(now + timedelta(seconds=451))
    assert second.task_id == task.id
    assert queue.remaining_seconds(second, now + timedelta(seconds=451)) == 150


def test_cancellation_keeps_execution_slot_until_child_acknowledges(queue, document, owner_ids):
    enqueue(queue, document, owner_ids)
    enqueue(queue, document, owner_ids)
    now = datetime.now(UTC)
    lease = queue.claim(now)
    queue.cancel(owner_ids[0], lease.task_id)
    assert queue.claim(now + timedelta(seconds=1)) is None
    queue.acknowledge_stopped(lease)
    assert queue.claim(now + timedelta(seconds=1)) is not None


@pytest.mark.parametrize(
    "code", ["parse_failure", "unsupported_page", "timeout", "resource_exhaustion"]
)
def test_deterministic_failures_are_not_retried(queue, document, owner_ids, code):
    from table_review.jobs.types import TaskOutcome

    task = enqueue(queue, document, owner_ids)
    lease = queue.claim(datetime.now(UTC))
    assert queue.complete(lease, TaskOutcome(status="failed", failure_code=code))
    assert queue.claim(datetime.now(UTC)) is None
    assert queue.get(owner_ids[0], task.id).state == "failed"


def test_retryable_failure_and_cancel_fence(queue, document, owner_ids):
    from table_review.jobs.types import TaskOutcome
    from table_review.storage.repository import NotFound

    task = enqueue(queue, document, owner_ids)
    lease = queue.claim(datetime.now(UTC))
    assert queue.complete(lease, TaskOutcome(status="failed", failure_code="storage_failure"))
    second = queue.claim(datetime.now(UTC))
    assert second.attempt_no == 2
    with pytest.raises(NotFound):
        queue.cancel(owner_ids[1], task.id)
    queue.cancel(owner_ids[0], task.id)
    assert not queue.complete(second, successful_outcome())
    assert queue.get(owner_ids[0], task.id).state == "cancelled"


def test_idempotency_is_payload_bound(queue, document, owner_ids):
    from table_review.jobs.queue import TaskConflict

    key = uuid4().hex
    first = queue.enqueue(owner_ids[0], document.id, "extract", {"pages": [2]}, key)
    assert queue.enqueue(owner_ids[0], document.id, "extract", {"pages": [2]}, key).id == first.id
    with pytest.raises(TaskConflict):
        queue.enqueue(owner_ids[0], document.id, "extract", {"pages": [1]}, key)


def test_timeout_kills_child_and_descendant(tmp_path):
    from table_review.jobs.runner import ProcessingFailure, run_child
    from table_review.jobs.types import ProcessingLimits

    pidfile = tmp_path / "pids.json"
    with pytest.raises(ProcessingFailure, match="timeout"):
        run_child(
            [sys.executable, "-m", "tests.fixtures.slow_child", str(pidfile)],
            ProcessingLimits(timeout_seconds=1.5),
            work_dir=tmp_path,
        )
    import json

    pids = json.loads(pidfile.read_text())
    assert all(not psutil.pid_exists(pid) for pid in pids)


def test_cancel_signals_real_child(queue, document, owner_ids, tmp_path):
    import json

    from table_review.jobs.runner import ProcessingFailure, run_child
    from table_review.jobs.types import ProcessingLimits

    task = enqueue(queue, document, owner_ids)
    lease = queue.claim(datetime.now(UTC))
    pidfile = tmp_path / "cancel-pids.json"
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            run_child,
            [sys.executable, "-m", "tests.fixtures.slow_child", str(pidfile)],
            ProcessingLimits(timeout_seconds=8),
            work_dir=tmp_path,
            cancelled=lambda: not queue.active(lease),
        )
        until = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < until:
            time.sleep(0.02)
        assert pidfile.exists()
        queue.cancel(owner_ids[0], task.id)
        with pytest.raises(ProcessingFailure, match="cancelled"):
            future.result(timeout=5)
    assert all(not psutil.pid_exists(pid) for pid in json.loads(pidfile.read_text()))


def test_private_run_api_and_selected_page_limits(client, client_b, app, tmp_path):
    path = two_page_report(tmp_path / "run.pdf")
    doc = client.post(
        "/api/v1/documents",
        files={"file": ("run.pdf", path.read_bytes())},
        headers={"Idempotency-Key": uuid4().hex},
    ).json()
    for pages in ([], [0], [3], [1, 1], [1, 2, 3, 4, 5, 6]):
        response = client.post(
            f"/api/v1/documents/{doc['id']}/runs",
            json={"pages": pages},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert response.status_code == 422
    response = client.post(
        f"/api/v1/documents/{doc['id']}/runs",
        json={"pages": [2]},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202
    task = response.json()
    assert "source_path" not in str(task) and "blob_key" not in str(task)
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 200
    assert client_b.get(f"/api/v1/tasks/{task['id']}").status_code == 404
    assert client_b.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 404
    assert client_b.get(f"/api/v1/documents/{doc['id']}/runs").status_code == 404


@pytest.mark.extraction
def test_worker_real_extraction_rerun_retains_existing_tables(app, owner, tmp_path):
    from table_review.accounts.types import Actor
    from table_review.jobs.worker import run_once

    actor = Actor(id=owner.id)
    path = two_page_report(tmp_path / "extract.pdf")
    doc = app.state.documents.upload(
        actor, io.BytesIO(path.read_bytes()), "extract.pdf", uuid4().hex
    )
    queue = app.state.queue
    # Preparation uses the same service, through a leased child operation.
    assert run_once(queue, "test-worker")
    assert app.state.repository.get_document(owner.id, doc.id).preparation_state == "ready"
    for _ in range(2):
        task = queue.enqueue(owner.id, doc.id, "extract", {"pages": [2]}, uuid4().hex)
        assert run_once(queue, "test-worker")
        saved = queue.get(owner.id, task.id)
        assert saved.state == "succeeded", saved.failure_code
        assert saved.result["table_ids"]
    runs = queue.runs(owner.id, doc.id)
    assert len(runs) == 2 and runs[0]["id"] != runs[1]["id"]
    assert len({x for run in runs for x in run["table_ids"]}) >= 2
    assert all(run["manifest"]["device"] == "cpu" for run in runs)
    for run in runs:
        timings = run["manifest"]["worker_timing"]
        assert timings["task_age_at_claim_seconds"] >= 0
        assert timings["task_age_semantics"] == "includes_prior_attempts_when_retried"
        for name in ("source_validation", "bounded_operation"):
            assert timings["stages"][name]["seconds"] >= 0
        assert "publication_operation" not in timings["stages"]


@pytest.mark.extraction
def test_worker_death_kills_descendants_and_restart_publishes_once(app, owner, tmp_path):
    import json
    import subprocess

    from sqlalchemy import update

    from table_review.accounts.types import Actor
    from table_review.jobs.models import TaskRow

    path = two_page_report(tmp_path / "restart.pdf")
    doc = app.state.documents.upload(
        Actor(id=owner.id), io.BytesIO(path.read_bytes()), "restart.pdf", uuid4().hex
    )
    queue = app.state.queue
    for task in queue.list(owner.id, doc.id):
        queue.cancel(owner.id, task.id)
    task = queue.enqueue(owner.id, doc.id, "extract", {"pages": [2]}, uuid4().hex)
    config = tmp_path / "worker-config.json"
    config.write_text(app.state.settings.model_dump_json())
    pidfile = tmp_path / "worker-pids.json"
    command = [sys.executable, "-m", "tests.fixtures.restart_worker", str(config), str(pidfile)]
    worker = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        until = time.monotonic() + 8
        while not pidfile.exists() and worker.poll() is None and time.monotonic() < until:
            time.sleep(0.02)
        assert pidfile.exists(), "Recovery fixture did not start the leased child"
        pids = json.loads(pidfile.read_text())
        worker.kill()
        worker.wait(timeout=5)
        until = time.monotonic() + 5
        while any(psutil.pid_exists(pid) for pid in pids) and time.monotonic() < until:
            time.sleep(0.02)
        assert all(not psutil.pid_exists(pid) for pid in pids)
        with queue.session_factory.begin() as session:
            session.execute(
                update(TaskRow)
                .where(TaskRow.id == task.id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        restarted = subprocess.run(
            command + ["finish"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        assert restarted.returncode == 0
        record = queue.get(owner.id, task.id)
        assert record.state == "succeeded" and record.attempt_count == 2
        assert len(queue.runs(owner.id, doc.id)) == 1
        assert len(record.result["table_ids"]) == 1
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
