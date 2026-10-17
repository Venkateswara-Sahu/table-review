from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update

from table_review.jobs.models import TaskRow
from table_review.jobs.types import TaskOutcome
from table_review.storage.models import AccountRow, DocumentRow, SessionRow
from tests.fixtures.clock import Clock
from tests.integration.test_review_api import owned_table  # noqa: F401


@pytest.fixture(autouse=True)
def clean_task_slots(app):
    with app.state.session_factory.begin() as session:
        session.execute(
            update(TaskRow)
            .where(TaskRow.state.in_(("queued", "running", "cancelled")))
            .values(state="cancelled", cancellation_requested=True, lease_token=None)
        )


def test_delete_revokes_all_owned_resources_and_is_idempotent(app, client, client_b, owned_table):  # noqa: F811
    _, doc, table = owned_table
    assert client.get(f"/api/v1/documents/{doc.id}/pages/2/image").status_code == 200
    export = client.post(
        f"/api/v1/tables/{table.id}/exports",
        json={"revision_id": str(table.current_revision_id), "format": "workbook"},
        headers={"Idempotency-Key": uuid4().hex},
    ).json()
    assert client_b.delete(f"/api/v1/documents/{doc.id}").status_code == 404
    deleted = client.delete(f"/api/v1/documents/{doc.id}")
    assert deleted.status_code == 200
    assert client.delete(f"/api/v1/documents/{doc.id}").json() == deleted.json()
    for route in (
        f"/documents/{doc.id}",
        f"/documents/{doc.id}/source",
        f"/documents/{doc.id}/pages/2/image",
        f"/documents/{doc.id}/tasks",
        f"/tables/{table.id}",
        f"/tables/{table.id}/history",
        f"/revisions/{table.current_revision_id}/review",
        f"/revisions/{table.current_revision_id}/timeline",
        f"/exports/{export['id']}/download",
    ):
        assert client.get("/api/v1" + route).status_code == 404, route
    assert str(doc.id) not in {d["id"] for d in client.get("/api/v1/documents").json()["items"]}


def test_document_cleanup_purges_recorded_timeline(app, client, owned_table):  # noqa: F811
    from sqlalchemy import func, select

    from table_review.reviews.models import TimelineEventRow
    from tests.integration.test_review_api import check_all

    actor, doc, table = owned_table
    check_all(client, table)
    query = (
        select(func.count())
        .select_from(TimelineEventRow)
        .where(TimelineEventRow.table_id == table.id)
    )
    with app.state.session_factory() as session:
        assert session.scalar(query) > 0
    clock = Clock()
    app.state.deletion.request(actor, doc.id, clock.now())
    assert client.get(f"/api/v1/revisions/{table.current_revision_id}/timeline").status_code == 404
    app.state.deletion.sweep(clock.now())
    with app.state.session_factory() as session:
        assert session.scalar(query) == 0


@pytest.mark.parametrize("kind", ["prepare", "extract", "export"])
def test_delete_cancels_active_attempt_and_waits_for_stop_before_cleanup(app, owned_table, kind):  # noqa: F811
    actor, doc, table = owned_table
    clock = Clock()
    if kind != "prepare":
        with app.state.session_factory.begin() as session:
            session.execute(
                update(TaskRow)
                .where(TaskRow.document_id == doc.id)
                .values(state="cancelled", cancellation_requested=True, lease_token=None)
            )
        if kind == "extract":
            app.state.queue.enqueue(actor.id, doc.id, kind, {"pages": [2]}, uuid4().hex)
        else:
            app.state.exports.request(
                actor, table.id, table.current_revision_id, "workbook", uuid4().hex
            )
    lease = app.state.queue.claim(clock.now())
    receipt = app.state.deletion.request(actor, doc.id, clock.now())
    assert receipt.cleanup_deadline == clock.now() + timedelta(hours=24)
    with app.state.session_factory() as session:
        task = session.get(TaskRow, lease.task_id)
        assert task.state == "cancelled" and task.cancellation_requested
    assert not app.state.queue.complete(lease, TaskOutcome(status="succeeded"), now=clock.now())
    waiting = app.state.deletion.sweep(clock.now())
    assert waiting.waiting_for_attempts == 1
    assert app.state.blobs._path(actor.id, doc.blob_key).exists()
    app.state.queue.acknowledge_stopped(lease)
    clock.advance(minutes=1)
    cleaned = app.state.deletion.sweep(clock.now())
    assert cleaned.documents_cleaned == 1
    assert not app.state.blobs._path(actor.id, doc.blob_key).exists()
    assert app.state.deletion.sweep(clock.now()).documents_cleaned == 0


def test_attempt_and_orphan_cleanup_preserves_live_inputs(app, owned_table):  # noqa: F811
    import io
    import os

    actor, doc, _ = owned_table
    clock = Clock()
    lease = app.state.queue.claim(clock.now())
    root = app.state.blobs.root / ".attempts"
    root.mkdir(exist_ok=True)
    live = root / (lease.token.hex + "-live")
    stale = root / (uuid4().hex + "-stale")
    for path in (live, stale):
        path.mkdir()
        (path / "input.json").write_text("fixture", encoding="utf-8")
        old = clock.now().timestamp() - 800
        os.utime(path, (old, old))
    orphan = app.state.blobs.put(actor.id, uuid4().hex, io.BytesIO(b"unpublished fixture"))
    orphan_path = app.state.blobs._path(actor.id, orphan.key)
    os.utime(orphan_path, (old, old))
    result = app.state.deletion.sweep(clock.now())
    assert result.temporary_artifacts_removed == 1
    assert live.is_dir() and not stale.exists()
    # A live lease conservatively protects all publication staging; clean on the next idle sweep.
    assert orphan_path.exists()
    app.state.queue.cancel(actor.id, lease.task_id)
    app.state.queue.acknowledge_stopped(lease)
    result = app.state.deletion.sweep(clock.now())
    assert result.orphan_blobs_removed == 1
    assert not orphan_path.exists()
    assert app.state.blobs._path(actor.id, doc.blob_key).exists()


def test_crashed_upload_scratch_is_removed_but_locked_input_survives(app, owned_table):  # noqa: F811
    import os

    from table_review.storage.scratch import exclusive_lock

    actor, _, _ = owned_table
    clock = Clock()
    old = clock.now().timestamp() - 800
    staged = app.state.documents.staging / ("upload-" + uuid4().hex + "-crashed")
    staged.mkdir()
    (staged / "source.pdf").write_bytes(b"private crashed upload")
    marker = staged / ".active"
    marker.write_bytes(b"")
    temporary = app.state.blobs.root / actor.id.hex / (uuid4().hex + ".tmp")
    temporary.write_bytes(b"private interrupted publication")
    for path in (staged, temporary):
        os.utime(path, (old, old))
    with marker.open("r+b") as stream, exclusive_lock(stream):
        result = app.state.deletion.sweep(clock.now())
        assert staged.is_dir() and not temporary.exists()
        assert result.temporary_artifacts_removed >= 1
    result = app.state.deletion.sweep(clock.now())
    assert not staged.exists() and result.temporary_artifacts_removed >= 1


def test_missing_current_journal_denies_access_and_is_not_recreated(app, client):
    journal_path = app.state.journal.path
    journal_path.unlink()
    response = client.get("/api/v1/session")
    assert response.status_code == 503
    assert response.json()["code"] == "lifecycle_unavailable"
    assert not journal_path.exists()


def test_expiry_uses_foreground_time_and_polling_does_not_save_expired_work(
    app,
    client,
    owned_table,  # noqa: F811
):
    _, doc, _ = owned_table
    clock = Clock()
    with app.state.session_factory.begin() as session:
        session.get(DocumentRow, doc.id).last_foreground_at = clock.now() - timedelta(days=31)
    assert client.get(f"/api/v1/documents/{doc.id}").status_code == 200
    assert client.get(f"/api/v1/documents/{doc.id}/tasks").status_code == 200
    app.state.deletion.sweep(clock.now())
    assert client.get(f"/api/v1/documents/{doc.id}").status_code == 404


def test_foreground_open_extends_retention(app, client, owned_table):  # noqa: F811
    _, doc, _ = owned_table
    clock = Clock()
    with app.state.session_factory.begin() as session:
        session.get(DocumentRow, doc.id).last_foreground_at = clock.now() - timedelta(days=29)
    assert client.post(f"/api/v1/documents/{doc.id}/open").status_code == 200
    clock.advance(days=2)
    app.state.deletion.sweep(clock.now())
    assert client.get(f"/api/v1/documents/{doc.id}").status_code == 200


def test_cleanup_failure_is_recorded_and_retried_without_source_details(
    app,
    owned_table,  # noqa: F811
    monkeypatch,
):
    actor, doc, _ = owned_table
    clock = Clock()
    app.state.deletion.request(actor, doc.id, clock.now())
    original = app.state.blobs.delete
    monkeypatch.setattr(
        app.state.blobs,
        "delete",
        lambda *args: (_ for _ in ()).throw(OSError("secret report text")),
    )
    failed = app.state.deletion.sweep(clock.now())
    assert failed.failure_count == 1
    assert "secret" not in failed.model_dump_json()
    monkeypatch.setattr(app.state.blobs, "delete", original)
    assert app.state.deletion.sweep(clock.now()).documents_cleaned == 1


@pytest.mark.parametrize("kind", ["revoke", "reset"])
def test_current_journal_denies_restored_old_account_epoch(app, client, owner, kind):
    with app.state.session_factory() as session:
        account = session.get(AccountRow, owner.id)
        old_epoch, old_hash = account.auth_epoch, account.password_hash
    if kind == "revoke":
        app.state.accounts.revoke(owner.id)
    else:
        app.state.accounts.reset_password(owner.id, "new-test-password-123")
    # Simulate stale DB state only. The separately preserved current journal is unchanged.
    with app.state.session_factory.begin() as session:
        account = session.get(AccountRow, owner.id)
        account.auth_epoch, account.password_hash, account.revoked_at = old_epoch, old_hash, None
        session.execute(
            update(SessionRow).where(SessionRow.owner_id == owner.id).values(revoked_at=None)
        )
    assert client.get("/api/v1/session").status_code == 401
    assert (
        client.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-test-password-123"},
        ).status_code
        == 401
    )


def test_independent_journal_survives_restart_and_at_least_eight_days(app, owned_table):  # noqa: F811
    from table_review.lifecycle.journal import LifecycleJournal

    actor, doc, _ = owned_table
    clock = Clock()
    app.state.deletion.request(actor, doc.id, clock.now())
    reopened = LifecycleJournal(app.state.journal.path)
    assert reopened.is_deleted(actor.id, doc.id)
    clock.advance(days=9)
    app.state.deletion.sweep(clock.now())
    assert len(reopened.events_since(clock.now() - timedelta(days=10))) >= 1
    assert not reopened.path.resolve().is_relative_to(app.state.blobs.root)


def test_late_render_cannot_publish_a_blob_after_deletion(app, owned_table, monkeypatch):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    from table_review.storage.repository import NotFound

    actor, doc, _ = owned_table
    clock = Clock()
    rendered, finish = Event(), Event()
    original = app.state.documents.pdf.render

    @contextmanager
    def paused_render(*args):
        with original(*args) as result:
            rendered.set()
            assert finish.wait(5)
            yield result

    monkeypatch.setattr(app.state.documents.pdf, "render", paused_render)
    with ThreadPoolExecutor(1) as pool:
        rendering = pool.submit(app.state.documents.page, actor, doc.id, 2, 1.5)
        assert rendered.wait(5)
        app.state.deletion.request(actor, doc.id, clock.now())
        app.state.deletion.sweep(clock.now())
        finish.set()
        with pytest.raises(NotFound):
            rendering.result(timeout=5)
    assert not list((app.state.blobs.root / actor.id.hex).rglob("*.tmp"))
    assert not [path for path in (app.state.blobs.root / actor.id.hex).rglob("*") if path.is_file()]


def test_failed_db_deletion_still_denies_access_from_independent_journal(
    app,
    client,
    owned_table,  # noqa: F811
    monkeypatch,
):
    actor, doc, _ = owned_table

    def fail(*args):
        raise RuntimeError("injected database mirror failure")

    monkeypatch.setattr(app.state.deletion, "_mirror", fail)
    with pytest.raises(RuntimeError, match="injected"):
        app.state.deletion.request(actor, doc.id, Clock().now())
    assert client.get(f"/api/v1/documents/{doc.id}").status_code == 404
    assert str(doc.id) not in {d["id"] for d in client.get("/api/v1/documents").json()["items"]}


def test_current_journal_detects_index_tampering(app, owned_table):  # noqa: F811
    import sqlite3

    from table_review.lifecycle.journal import JournalUnavailable

    actor, doc, _ = owned_table
    app.state.deletion.request(actor, doc.id, Clock().now())
    with sqlite3.connect(app.state.journal.path) as connection:
        connection.execute("DROP TRIGGER immutable_update")
        connection.execute("UPDATE events SET target_id=?", (str(uuid4()),))
    with pytest.raises(JournalUnavailable):
        app.state.journal.verify()


def test_worker_maintenance_runs_when_idle(app, owned_table):  # noqa: F811
    from table_review.jobs.worker import maintain

    actor, doc, _ = owned_table
    clock = Clock()
    app.state.deletion.request(actor, doc.id, clock.now())
    result = maintain(app.state.queue, clock.now())
    assert result.documents_cleaned >= 1
    with app.state.session_factory() as session:
        assert session.get(DocumentRow, doc.id).active_files_removed_at == clock.now()
    assert not app.state.blobs._path(actor.id, doc.blob_key).exists()
