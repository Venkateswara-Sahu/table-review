# ruff: noqa: F811
# Imported pytest fixtures are intentionally reused as parameter names.
import io
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import text

from table_review.app import create_app
from table_review.lifecycle.backup import BackupError, create_backup, purge_backups, restore_backup
from table_review.lifecycle.journal import JournalUnavailable, LifecycleJournal
from table_review.settings import Settings
from table_review.storage.db import create_session_factory
from tests.fixtures.pdf_factory import two_page_report
from tests.integration.test_exports import finish_requested_export_via_worker, request
from tests.integration.test_review_api import approve, check_all, owned_table  # noqa: F401


@pytest.fixture
def app(session_factory, tmp_path):
    url = session_factory.kw["bind"].url.render_as_string(hide_password=False)
    schema = "test_" + uuid4().hex
    admin = create_session_factory(url).kw["bind"]
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    factory = create_session_factory(url, schema=schema)
    with factory.kw["bind"].begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    factory.kw["bind"].dispose()
    key = tmp_path / "private.key"
    key.write_bytes(Fernet.generate_key())
    settings = Settings(
        database_url=url,
        database_schema=schema,
        blob_root=tmp_path / "source-blobs",
        journal_path=tmp_path / "independent" / "journal.sqlite3",
        initialize_journal=True,
        backup_key_path=key,
        backup_root=tmp_path / "backups",
        secure_cookies=False,
        public_origin="http://testserver",
    )
    instance = create_app(settings)
    yield instance
    instance.state.session_factory.kw["bind"].dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def target(app, tmp_path):
    settings = app.state.settings.model_copy(
        update={
            "database_schema": "test_" + uuid4().hex,
            "blob_root": tmp_path / "restored-blobs",
            "initialize_journal": False,
        }
    )
    yield settings
    admin = create_session_factory(settings.database_url).kw["bind"]
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{settings.database_schema}" CASCADE'))
    admin.dispose()


def test_real_restore_reconciles_current_journal_and_reproduces_retained_export(
    app, client, owned_table, target, tmp_path
):
    from table_review.exports.types import ExportInput
    from table_review.exports.workbook import write_workbook
    from table_review.jobs.models import TaskRow
    from table_review.jobs.worker import run_once
    from table_review.storage.models import DocumentRow

    actor, doc, table = owned_table
    assert check_all(client, table).status_code == 200
    approval = approve(client, table.current_revision_id).json()
    recorded_timeline = app.state.reviews.timeline(actor, table.current_revision_id)
    recoverable = app.state.repository.create_table(
        actor.id,
        doc.id,
        app.state.repository.get_revision(actor.id, table.id, table.current_revision_id).content,
        extraction_manifest={"origin": "backup-removal-fixture"},
    )
    app.state.tables.set_removed(actor, recoverable.id, recoverable.current_revision_id, 0, True)
    export = request(client, table).json()
    completed_export, expected_bytes = finish_requested_export_via_worker(app, client, export["id"])
    app.state.documents.page(actor, doc.id, 2, 1.5)
    pdf = two_page_report(tmp_path / "deleted.pdf")
    doomed = app.state.documents.upload(
        actor, io.BytesIO(pdf.read_bytes()), "deleted.pdf", uuid4().hex
    )
    assert run_once(app.state.queue, "restore-fixture")
    revoked = app.state.accounts.create("revoked-fixture", "revoked-test-password-123")
    grant = app.state.accounts.sign_in(revoked.username, "revoked-test-password-123")
    backup = app.state.settings.backup_root / (uuid4().hex + ".trbackup")
    manifest = create_backup(app.state.settings, backup, datetime.now(UTC))
    assert manifest.expires_at == manifest.created_at + timedelta(days=7)
    assert manifest.blob_count >= 4
    assert b"revoked-fixture" not in backup.read_bytes()
    app.state.deletion.request(actor, doomed.id, datetime.now(UTC))
    app.state.accounts.revoke(revoked.id)
    started = time.monotonic()
    result = restore_backup(backup, app.state.journal, target)
    assert result.ready and result.deleted_documents == 1 and result.revoked_accounts == 1
    restored = create_app(target)
    try:
        from table_review.storage.repository import NotFound

        assert recoverable.id in {
            item.id for item in restored.state.tables.list(actor, doc.id, removed=True)
        }
        with pytest.raises(NotFound):
            restored.state.tables.get(actor, recoverable.id)
        recovered = restored.state.tables.set_removed(
            actor, recoverable.id, recoverable.current_revision_id, 1, False
        )
        assert recovered.removed_at is None and recovered.removal_version == 2
        assert (
            restored.state.repository.get_revision(actor.id, table.id, table.current_revision_id).id
            == table.current_revision_id
        )
        assert restored.state.reviews.current_approval(
            actor, table.current_revision_id
        ).id.hex == approval["id"].replace("-", "")
        history = restored.state.reviews.checked_history(actor, table.current_revision_id, 0)
        assert len(history.items) == 1 and history.items[0].checked
        assert history.items[0].actor_id == actor.id
        assert (
            restored.state.reviews.timeline(actor, table.current_revision_id) == recorded_timeline
        )
        assert restored.state.exports.download(actor, export["id"]).read_bytes() == expected_bytes
        with restored.state.session_factory() as session:
            captured = session.get(TaskRow, export["id"]).input["snapshot"]
            deleted = session.get(DocumentRow, doomed.id)
            assert deleted.deleted_at and deleted.active_files_removed_at
            assert not restored.state.blobs._path(actor.id, doomed.blob_key).exists()
        regenerated = write_workbook(
            ExportInput.model_validate(captured), tmp_path / "regenerated.xlsx"
        )
        assert regenerated.path.read_bytes() == expected_bytes
        with pytest.raises(PermissionError):
            restored.state.accounts.resolve_session(grant.token)
    finally:
        restored.state.session_factory.kw["bind"].dispose()
    evidence = {
        "restore_seconds": round(time.monotonic() - started, 3),
        "result": result.model_dump(mode="json"),
        "export_sha256": completed_export["sha256"],
        "revision_preserved": True,
        "export_reproduced": True,
    }
    evidence_dir = Path("artifacts/test-evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "restore-drill.json").write_text(
        __import__("json").dumps(evidence, indent=2), encoding="utf-8"
    )


def test_restore_requires_current_journal_before_touching_target(app, target, tmp_path):
    with pytest.raises(JournalUnavailable):
        restore_backup(
            tmp_path / "not-even-opened.trbackup",
            LifecycleJournal(tmp_path / "missing.sqlite3"),
            target,
        )
    assert not target.blob_root.exists()


def test_backup_refuses_live_processing_attempt(app, owned_table, tmp_path):
    app.state.queue.claim(datetime.now(UTC))
    with pytest.raises(BackupError, match="active"):
        create_backup(
            app.state.settings,
            app.state.settings.backup_root / "active.trbackup",
            datetime.now(UTC),
        )


@pytest.mark.parametrize("kind", ["source", "page"])
def test_backup_rejects_corrupted_referenced_blobs(app, owned_table, tmp_path, kind):
    actor, doc, _ = owned_table
    path = (
        app.state.documents.source_path(actor, doc.id)
        if kind == "source"
        else app.state.documents.page(actor, doc.id, 2, 1.5).image_path
    )
    path.write_bytes(b"tampered")
    with pytest.raises(BackupError, match="integrity"):
        create_backup(
            app.state.settings,
            app.state.settings.backup_root / "corrupt.trbackup",
            datetime.now(UTC),
        )


def test_tampered_backup_and_wrong_key_do_not_create_target(app, target, tmp_path):
    backup = app.state.settings.backup_root / "tampered.trbackup"
    create_backup(app.state.settings, backup, datetime.now(UTC))
    valid = backup.read_bytes()
    backup.write_bytes(valid[:-10] + b"malformed!")
    with pytest.raises(BackupError):
        restore_backup(backup, app.state.journal, target)
    assert not target.blob_root.exists()
    backup.write_bytes(valid)
    wrong_key = tmp_path / "wrong.key"
    wrong_key.write_bytes(Fernet.generate_key())
    with pytest.raises(BackupError):
        restore_backup(
            backup, app.state.journal, target.model_copy(update={"backup_key_path": wrong_key})
        )
    assert not target.blob_root.exists()


def test_restore_refuses_existing_schema_and_blob_root(app, tmp_path):
    backup = app.state.settings.backup_root / "valid.trbackup"
    create_backup(app.state.settings, backup, datetime.now(UTC))
    with pytest.raises(BackupError):
        restore_backup(backup, app.state.journal, app.state.settings)


def test_encrypted_backups_expire_and_are_physically_removed(app):
    now = datetime.now(UTC)
    old = app.state.settings.backup_root / (uuid4().hex + ".trbackup")
    fresh = app.state.settings.backup_root / (uuid4().hex + ".trbackup")
    create_backup(app.state.settings, old, now - timedelta(days=8))
    create_backup(app.state.settings, fresh, now)
    assert purge_backups(app.state.settings, now) == 1
    assert not old.exists() and fresh.exists()


def test_failed_reconciliation_keeps_restored_service_inaccessible(
    app, target, tmp_path, monkeypatch
):
    from table_review.lifecycle.deletion import CleanupSummary, DeletionService

    backup = app.state.settings.backup_root / "pending.trbackup"
    create_backup(app.state.settings, backup, datetime.now(UTC))
    monkeypatch.setattr(DeletionService, "sweep", lambda *args: CleanupSummary(failure_count=1))
    with pytest.raises(BackupError):
        restore_backup(backup, app.state.journal, target)
    with pytest.raises(JournalUnavailable):
        create_app(target)


def test_restored_reset_epoch_denies_old_password_until_operator_resets_it(
    app, target, tmp_path, monkeypatch
):
    from table_review.accounts.service import AuthenticationError

    account = app.state.accounts.create("reset-fixture", "old-reset-password-123")
    old = app.state.accounts.sign_in(account.username, "old-reset-password-123")
    backup = app.state.settings.backup_root / "reset.trbackup"
    create_backup(app.state.settings, backup, datetime.now(UTC))
    app.state.accounts.reset_password(account.id, "new-reset-password-123")
    result = restore_backup(backup, app.state.journal, target)
    assert result.reset_accounts == 1
    restored = create_app(target)
    try:
        with pytest.raises(AuthenticationError):
            restored.state.accounts.resolve_session(old.token)
        with pytest.raises(AuthenticationError):
            restored.state.accounts.sign_in(account.username, "old-reset-password-123")
        from table_review.accounts.admin import main

        monkeypatch.setenv("TABLE_REVIEW_DATABASE_URL", target.database_url)
        monkeypatch.setenv("TABLE_REVIEW_DATABASE_SCHEMA", target.database_schema)
        monkeypatch.setenv("TABLE_REVIEW_JOURNAL_PATH", str(target.lifecycle_journal_path))
        monkeypatch.setattr("sys.argv", ["admin", "reset", account.username])
        monkeypatch.setattr("getpass.getpass", lambda prompt: "operator-reset-password-123")
        main()
        assert (
            restored.state.accounts.sign_in(
                account.username, "operator-reset-password-123"
            ).actor.id
            == account.id
        )
    finally:
        restored.state.session_factory.kw["bind"].dispose()


def test_new_backup_does_not_extend_retention_of_deleted_content(app, owned_table, tmp_path):
    from table_review.lifecycle.backup import _read_backup

    actor, doc, _ = owned_table
    now = datetime.now(UTC)
    app.state.deletion.request(actor, doc.id, now)
    backup = app.state.settings.backup_root / "after-delete.trbackup"
    create_backup(app.state.settings, backup, now)
    archive, manifest = _read_backup(backup, app.state.settings, now)
    try:
        assert manifest.blob_count == 0
        database = json.loads(archive.read("database.json"))
        assert all(row["filename"] != "review.pdf" for row in database["documents"])
        for table in (
            "table_revisions",
            "review_tables",
            "edit_events",
            "cell_review_events",
            "review_snapshots",
            "page_artifacts",
            "tasks",
        ):
            assert database[table] == []
    finally:
        archive.close()


def test_snapshot_serializes_edit_and_preserves_revision_ancestry(
    app, client, owned_table, target, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    from table_review.lifecycle import backup as module
    from table_review.tables.grid import SetText

    actor, _doc, table = owned_table
    locked, release = Event(), Event()
    original = module._references

    def paused(session):
        result = original(session)
        locked.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(module, "_references", paused)
    backup = app.state.settings.backup_root / "concurrent.trbackup"
    with ThreadPoolExecutor(2) as pool:
        snapshot = pool.submit(create_backup, app.state.settings, backup, datetime.now(UTC))
        assert locked.wait(5)
        editing = pool.submit(
            app.state.tables.edit,
            actor,
            table.id,
            table.current_revision_id,
            SetText(kind="set_text", cell_id="r1c1", text="after backup"),
        )
        try:
            with pytest.raises(TimeoutError):
                editing.result(timeout=0.3)
        finally:
            release.set()
        snapshot.result(timeout=5)
        changed = editing.result(timeout=5)
    monkeypatch.setattr(module, "_references", original)
    restore_backup(backup, app.state.journal, target)
    restored = create_app(target)
    try:
        assert (
            restored.state.repository.get_table(actor.id, table.id).current_revision_id
            == table.current_revision_id
        )
        assert app.state.repository.get_table(actor.id, table.id).current_revision_id == changed.id
    finally:
        restored.state.session_factory.kw["bind"].dispose()


def test_worker_maintenance_removes_expired_encrypted_backups(app):
    from table_review.jobs.worker import maintain

    now = datetime.now(UTC)
    expired = app.state.settings.backup_root / (uuid4().hex + ".trbackup")
    create_backup(app.state.settings, expired, now - timedelta(days=8))
    result = maintain(app.state.queue, now)
    assert not expired.exists()
    assert result.expired_backups_removed == 1


def test_restore_rejects_journal_from_another_deployment_before_target_creation(
    app, target, tmp_path
):
    backup = app.state.settings.backup_root / "identity.trbackup"
    create_backup(app.state.settings, backup, datetime.now(UTC))
    unrelated = LifecycleJournal(tmp_path / "unrelated.sqlite3", create=True)
    unrelated_settings = target.model_copy(update={"journal_path": unrelated.path})
    with pytest.raises(BackupError):
        restore_backup(backup, unrelated, unrelated_settings)
    assert not target.blob_root.exists()


def test_backup_destination_must_be_inside_expiry_managed_directory(app, tmp_path):
    with pytest.raises(BackupError):
        create_backup(app.state.settings, tmp_path / "unmanaged.trbackup", datetime.now(UTC))
