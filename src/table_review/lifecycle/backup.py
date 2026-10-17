"""Bounded authenticated backups, quiesced SQL snapshots and isolated reconciliation."""

import hashlib
import io
import json
import os
import re
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import DateTime, Uuid, select, text

from table_review.documents.types import ImmutableModel
from table_review.jobs.models import TaskRow
from table_review.lifecycle.deletion import DeletionService
from table_review.lifecycle.journal import LifecycleJournal
from table_review.storage.blobs import PrivateBlobStore
from table_review.storage.db import create_session_factory
from table_review.storage.models import AccountRow, Base, DocumentRow, PageArtifactRow, SessionRow
from table_review.storage.repository import Repository


class BackupError(RuntimeError):
    pass


class BackupManifest(ImmutableModel):
    version: str = "1"
    created_at: datetime
    expires_at: datetime
    source_schema: str
    source_blob_root: str
    schema_revision: str
    blob_count: int
    member_sha256: dict[str, str]
    journal_checkpoint: dict[str, str | int]
    encryption: str = "Fernet (AES-128-CBC, HMAC-SHA256)"


class RestoreResult(ImmutableModel):
    ready: bool
    deleted_documents: int
    revoked_accounts: int
    reset_accounts: int
    recovered_blobs: int
    recovery_seconds: float


def _utc(now):
    if now.tzinfo is None:
        raise ValueError("Backup time must include its timezone")
    return now.astimezone(UTC)


def _cipher(settings):
    path = settings.backup_key_path
    if path is None or path.is_symlink() or not path.is_file():
        raise BackupError("Separately preserved backup key is required")
    if path.resolve().is_relative_to(settings.blob_root.resolve()) or path.resolve().is_relative_to(
        settings.backup_root.resolve()
    ):
        raise BackupError("Backup key must stay outside private blobs and backup artifacts")
    try:
        return Fernet(path.read_bytes().strip())
    except (ValueError, OSError) as error:
        raise BackupError("Backup key is unavailable or invalid") from error


def _json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
    ).encode("utf-8")


def _tables():
    from table_review.lifecycle import models  # noqa: F401
    from table_review.reviews import models as review_models  # noqa: F401

    return {name: table for name, table in sorted(Base.metadata.tables.items())}


def _references(session):
    references = {}
    for doc in session.scalars(
        select(DocumentRow).where(DocumentRow.active_files_removed_at.is_(None))
    ):
        references[(doc.owner_id, doc.blob_key)] = doc.source_sha256
    for page in session.scalars(select(PageArtifactRow)):
        if not page.sha256:
            raise BackupError("Blob integrity requires regenerating legacy page caches")
        references[(page.owner_id, page.blob_key)] = page.sha256
    for task in session.scalars(select(TaskRow)):
        if "blob_key" in task.result:
            references[(task.owner_id, task.result["blob_key"])] = task.result["sha256"]
    return references


def create_backup(settings, destination: Path, now: datetime) -> BackupManifest:
    now = _utc(now)
    if settings.restore_pending_path.exists():
        raise BackupError("Recovery must finish before creating a backup of that target")
    journal = LifecycleJournal(settings.lifecycle_journal_path)
    journal.verify()
    cipher = _cipher(settings)
    destination = Path(destination).absolute()
    if (
        destination.resolve().parent != settings.backup_root.resolve()
        or destination.suffix != ".trbackup"
    ):
        raise BackupError(
            "Backup destination must be a trbackup file in the expiry-managed directory"
        )
    if (
        destination.exists()
        or destination.is_symlink()
        or destination.resolve().is_relative_to(settings.blob_root.resolve())
    ):
        raise BackupError("Backup requires a new destination outside private blobs")
    if destination == journal.path or destination == settings.backup_key_path.absolute():
        raise BackupError("Backup cannot replace its independent journal or key")
    factory = create_session_factory(settings.database_url, schema=settings.database_schema)
    store = PrivateBlobStore(settings.blob_root)
    archive = io.BytesIO()
    try:
        reconciled = DeletionService(Repository(factory), store, journal, settings).sweep(now)
        if reconciled.failure_count or reconciled.waiting_for_attempts:
            raise BackupError("Backup requires completed deletion cleanup")
        with factory.begin() as session:
            tables = _tables()
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            names = ",".join('"' + name + '"' for name in tables)
            session.execute(text(f"LOCK TABLE {names} IN SHARE MODE"))
            if session.scalar(
                select(TaskRow.id)
                .where(TaskRow.lease_token.is_not(None), TaskRow.lease_expires_at > now)
                .limit(1)
            ):
                raise BackupError("Backup refused while an active processing attempt exists")
            rows = {
                name: [dict(row) for row in session.execute(select(table)).mappings()]
                for name, table in tables.items()
            }
            hashes = {}
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                database = _json(rows)
                zipped.writestr("database.json", database)
                hashes["database.json"] = hashlib.sha256(database).hexdigest()
                references = _references(session)
                total = len(database)
                for (owner, key), expected in sorted(
                    references.items(), key=lambda item: (item[0][0].hex, item[0][1])
                ):
                    path = store._path(owner, key)
                    total += path.stat().st_size
                    if total > settings.max_backup_bytes:
                        raise BackupError("Backup exceeds the configured bounded archive size")
                    with path.open("rb") as source:
                        if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                            raise BackupError("Referenced blob integrity check failed")
                    member = f"blobs/{owner.hex}/{key}"
                    zipped.write(path, member)
                    hashes[member] = expected
                manifest = BackupManifest(
                    created_at=now,
                    expires_at=now + timedelta(days=7),
                    source_schema=session.scalar(text("SELECT current_schema()")),
                    source_blob_root=str(store.root),
                    schema_revision=session.scalar(text("SELECT version_num FROM alembic_version")),
                    blob_count=len(references),
                    member_sha256=hashes,
                    journal_checkpoint=journal.checkpoint(),
                )
                zipped.writestr("manifest.json", _json(manifest.model_dump(mode="json")))
            payload = archive.getvalue()
            if len(payload) > settings.max_backup_bytes:
                raise BackupError("Backup exceeds the configured bounded archive size")
            encrypted = cipher.encrypt_at_time(payload, int(now.timestamp()))
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_name(uuid4().hex + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return manifest
    except BackupError:
        raise
    except Exception as error:
        raise BackupError("Backup integrity/storage check failed or service is busy") from error
    finally:
        factory.kw["bind"].dispose()
        archive.close()


def _read_backup(backup, settings, now):
    path = Path(backup)
    if path.is_symlink() or path.stat().st_size > settings.max_backup_bytes * 2:
        raise BackupError("Backup exceeds the configured bounded archive size")
    try:
        token = path.read_bytes()
        payload = _cipher(settings).decrypt_at_time(
            token, ttl=7 * 86400, current_time=int(now.timestamp())
        )
        archive = zipfile.ZipFile(io.BytesIO(payload))
        infos = archive.infolist()
        if (
            len({info.filename for info in infos}) != len(infos)
            or sum(info.file_size for info in infos) > settings.max_backup_bytes
        ):
            raise BackupError("Backup archive member bounds are invalid")
        manifest = BackupManifest.model_validate_json(archive.read("manifest.json"))
        if (
            manifest.version != "1"
            or now >= manifest.expires_at
            or manifest.expires_at != manifest.created_at + timedelta(days=7)
        ):
            raise BackupError("Backup is expired or incompatible")
        if set(archive.namelist()) != {"manifest.json", *manifest.member_sha256}:
            raise BackupError("Backup member manifest differs from archive")
        for name, digest in manifest.member_sha256.items():
            if name != "database.json" and not re.fullmatch(
                r"blobs/[0-9a-f]{32}/[0-9a-f]{32}(?:/[0-9a-f]{32}){0,3}", name
            ):
                raise BackupError("Backup contains an invalid private blob identity")
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise BackupError("Backup member integrity check failed")
        return archive, manifest
    except (InvalidToken, ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        raise BackupError("Backup authentication, expiry or integrity check failed") from error


def _decode(table, row):
    result = dict(row)
    for column in table.columns:
        value = result[column.name]
        if value is not None and isinstance(column.type, Uuid):
            result[column.name] = UUID(value)
        elif value is not None and isinstance(column.type, DateTime):
            result[column.name] = datetime.fromisoformat(value)
    return result


def restore_backup(backup, current_journal, target_settings) -> RestoreResult:
    started, now = time.monotonic(), datetime.now(UTC)
    current_journal.verify()
    settings = target_settings
    schema = settings.database_schema
    if not schema or not re.fullmatch(r"test_[0-9a-f]{32}", schema):
        raise BackupError("Restore requires an explicitly isolated new schema")
    root = settings.blob_root.resolve()
    if (
        root.exists()
        or settings.restore_pending_path.exists()
        or current_journal.path.resolve().is_relative_to(root)
    ):
        raise BackupError("Restore requires a new private blob root outside the journal")
    if (
        settings.lifecycle_journal_path.resolve() != current_journal.path.resolve()
        or settings.initialize_journal
    ):
        raise BackupError("Restore must preserve the supplied current journal")
    archive, manifest = _read_backup(backup, settings, now)
    checkpoint = current_journal.checkpoint()
    saved = manifest.journal_checkpoint
    entries = current_journal.events_since(datetime.min.replace(tzinfo=UTC))
    saved_digest = (
        "0" * 64
        if saved["sequence"] == 0
        else next((entry.sha256 for entry in entries if entry.sequence == saved["sequence"]), None)
    )
    if (
        checkpoint["deployment_id"] != saved["deployment_id"]
        or checkpoint["sequence"] < saved["sequence"]
        or saved_digest != saved["sha256"]
    ):
        archive.close()
        raise BackupError("Current journal does not preserve the backup deployment checkpoint")
    if schema == manifest.source_schema or root == Path(manifest.source_blob_root).resolve():
        archive.close()
        raise BackupError("Restore target cannot be the source workspace")
    admin = create_session_factory(settings.database_url).kw["bind"]
    factory = None
    try:
        with admin.begin() as connection:
            if connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
                {"schema": schema},
            ):
                raise BackupError("Restore schema already exists")
            settings.restore_pending_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(settings.restore_pending_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as marker:
                marker.write("Reconciliation incomplete; keep this target offline.\n")
                marker.flush()
                os.fsync(marker.fileno())
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        factory = create_session_factory(settings.database_url, schema=schema)
        with factory.kw["bind"].begin() as connection:
            config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            if (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                != manifest.schema_revision
            ):
                raise BackupError("Backup schema revision is incompatible")
        tables = _tables()
        data = json.loads(archive.read("database.json"))
        if set(data) != set(tables):
            raise BackupError("Backup database table manifest is incompatible")
        with factory.begin() as session:
            session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            order = (
                "accounts",
                "documents",
                "upload_requests",
                "review_tables",
                "table_revisions",
                "edit_events",
                "tasks",
                "task_attempts",
                "extraction_runs",
                "page_artifacts",
                "review_snapshots",
                "review_states",
                "issue_decision_events",
                "cell_review_events",
                "review_timeline_events",
                "account_sessions",
                "sign_in_throttles",
                "lifecycle_events",
            )
            if set(order) != set(tables):
                raise BackupError("Restore table ordering requires an explicit schema update")
            for name in order:
                rows = data[name]
                if name == "table_revisions":
                    ordered, pending, inserted = [], list(rows), set()
                    while pending:
                        ready = [
                            row
                            for row in pending
                            if row["parent_revision_id"] is None
                            or row["parent_revision_id"] in inserted
                        ]
                        if not ready:
                            raise BackupError("Revision ancestry is incomplete or cyclic")
                        ordered.extend(ready)
                        inserted.update(row["id"] for row in ready)
                        pending = [row for row in pending if row["id"] not in inserted]
                    rows = ordered
                for row in rows:
                    session.execute(tables[name].insert().values(**_decode(tables[name], row)))
        store = PrivateBlobStore(root)
        recovered = 0
        for member in manifest.member_sha256:
            if member.startswith("blobs/"):
                _, owner, key = member.split("/", 2)
                store.put(UUID(hex=owner), key, io.BytesIO(archive.read(member)))
                recovered += 1
        revoked, reset = 0, 0
        events = current_journal.events_since(datetime.min.replace(tzinfo=UTC))
        latest = {
            entry.event.owner_id: entry.event
            for entry in events
            if entry.event.kind in ("revoke_account", "reset_password")
        }
        with factory.begin() as session:
            for owner, event in latest.items():
                account = session.get(AccountRow, owner)
                if account:
                    if event.kind == "revoke_account":
                        account.revoked_at = event.at
                        revoked += 1
                    else:
                        reset += 1
                    for grant in session.scalars(
                        select(SessionRow).where(SessionRow.owner_id == owner)
                    ):
                        grant.revoked_at = now
        cleaned = DeletionService(Repository(factory), store, current_journal, settings).sweep(now)
        if cleaned.failure_count or cleaned.waiting_for_attempts:
            raise BackupError("Restore reconciliation did not finish; service must remain offline")
        with factory() as session:
            for (owner, key), expected in _references(session).items():
                with store.open(owner, key) as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                        raise BackupError("Restored blob integrity check failed")
        current_journal.verify()
        settings.restore_pending_path.unlink()
        return RestoreResult(
            ready=True,
            deleted_documents=cleaned.documents_cleaned,
            revoked_accounts=revoked,
            reset_accounts=reset,
            recovered_blobs=recovered,
            recovery_seconds=round(time.monotonic() - started, 3),
        )
    except BackupError:
        raise
    except Exception as error:
        raise BackupError("Isolated restore failed; target must remain offline") from error
    finally:
        archive.close()
        if factory:
            factory.kw["bind"].dispose()
        admin.dispose()


def purge_backups(settings, now):
    now, removed = _utc(now), 0
    root = settings.backup_root.resolve()
    if not root.is_dir():
        return 0
    cipher = _cipher(settings)
    for path in root.glob("*.trbackup"):
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            continue
        if path.stat().st_size > settings.max_backup_bytes * 2:
            raise BackupError("Backup expiry requires manual inspection of an oversized artifact")
        try:
            created = cipher.extract_timestamp(path.read_bytes())
        except InvalidToken as error:
            raise BackupError(
                "Backup expiry authentication failed; manual inspection required"
            ) from error
        if now.timestamp() >= created + 7 * 86400:
            path.unlink()
            removed += 1
    return removed
