import re
import shutil
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select

from table_review.accounts.types import Actor
from table_review.documents.types import ImmutableModel
from table_review.jobs.models import AttemptRow, ExtractionRunRow, TaskRow
from table_review.lifecycle.journal import LifecycleEvent
from table_review.lifecycle.models import mirror_event
from table_review.reviews.models import (
    CellReviewEventRow,
    IssueDecisionRow,
    ReviewSnapshotRow,
    ReviewStateRow,
)
from table_review.storage.models import (
    DocumentRow,
    EditEventRow,
    PageArtifactRow,
    RevisionRow,
    TableRow,
)
from table_review.storage.repository import NotFound
from table_review.storage.scratch import exclusive_lock


class DeletionReceipt(ImmutableModel):
    document_id: UUID
    deleted_at: datetime
    cleanup_deadline: datetime
    backup_expiry_deadline: datetime


class CleanupSummary(ImmutableModel):
    documents_expired: int = 0
    documents_cleaned: int = 0
    waiting_for_attempts: int = 0
    failure_count: int = 0
    temporary_artifacts_removed: int = 0
    orphan_blobs_removed: int = 0
    expired_backups_removed: int = 0
    overdue_count: int = 0


class DeletionService:
    def __init__(self, repository, blobs, journal, settings):
        self.repository, self.blobs, self.journal, self.settings = (
            repository,
            blobs,
            journal,
            settings,
        )

    def _mirror(self, session, entry):
        mirror_event(session, entry)

    def _request(self, session, actor, document_id, now, reason="manual"):
        doc = session.scalar(
            select(DocumentRow)
            .where(DocumentRow.id == document_id, DocumentRow.owner_id == actor.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if doc is None:
            raise NotFound("Document not found")
        entry = self.journal.deletion_entry(actor.id, document_id)
        if entry is None:
            entry = self.journal.append(
                LifecycleEvent(
                    id=uuid5(NAMESPACE_URL, f"delete-document:{actor.id}:{document_id}"),
                    owner_id=actor.id,
                    target_id=document_id,
                    kind="delete_document",
                    at=doc.deleted_at or now,
                    reason=reason,
                )
            )
        self._mirror(session, entry)
        doc.deleted_at = entry.event.at
        for task in session.scalars(
            select(TaskRow)
            .where(
                TaskRow.owner_id == actor.id,
                TaskRow.document_id == document_id,
                TaskRow.state.in_(("queued", "running")),
            )
            .with_for_update()
        ):
            task.state, task.cancellation_requested = "cancelled", True
            if task.lease_token:
                attempt = session.scalar(
                    select(AttemptRow).where(AttemptRow.token == task.lease_token)
                )
                if attempt:
                    attempt.finished_at, attempt.outcome = now, "cancelled"
        return DeletionReceipt(
            document_id=doc.id,
            deleted_at=doc.deleted_at,
            cleanup_deadline=doc.deleted_at + timedelta(hours=24),
            backup_expiry_deadline=doc.deleted_at + timedelta(days=7),
        )

    def request(self, actor: Actor, document_id: UUID, now: datetime) -> DeletionReceipt:
        if now.tzinfo is None:
            raise ValueError("Deletion time must include its timezone")
        with self.repository.session_factory.begin() as session:
            return self._request(session, actor, document_id, now.astimezone(UTC))

    def _purge_records(self, session, doc):
        revisions = (
            select(RevisionRow.id)
            .join(TableRow, TableRow.id == RevisionRow.table_id)
            .where(TableRow.document_id == doc.id, TableRow.owner_id == doc.owner_id)
        )
        tables = select(TableRow.id).where(
            TableRow.document_id == doc.id, TableRow.owner_id == doc.owner_id
        )
        tasks = select(TaskRow.id).where(
            TaskRow.document_id == doc.id, TaskRow.owner_id == doc.owner_id
        )
        # Current revision FK is deferred. Remove dependent review/events before revisions/tables.
        session.execute(delete(ReviewStateRow).where(ReviewStateRow.revision_id.in_(revisions)))
        session.execute(
            delete(ReviewSnapshotRow).where(ReviewSnapshotRow.revision_id.in_(revisions))
        )
        session.execute(delete(IssueDecisionRow).where(IssueDecisionRow.revision_id.in_(revisions)))
        session.execute(
            delete(CellReviewEventRow).where(CellReviewEventRow.revision_id.in_(revisions))
        )
        session.execute(delete(EditEventRow).where(EditEventRow.table_id.in_(tables)))
        # Self-referencing revision parents are removed in one statement.
        session.execute(delete(RevisionRow).where(RevisionRow.id.in_(revisions)))
        session.execute(delete(TableRow).where(TableRow.id.in_(tables)))
        session.execute(delete(ExtractionRunRow).where(ExtractionRunRow.task_id.in_(tasks)))
        session.execute(delete(AttemptRow).where(AttemptRow.task_id.in_(tasks)))
        session.execute(delete(TaskRow).where(TaskRow.id.in_(tasks)))
        session.execute(
            delete(PageArtifactRow).where(
                PageArtifactRow.document_id == doc.id, PageArtifactRow.owner_id == doc.owner_id
            )
        )
        doc.filename = "Deleted report"

    def _clean_document(self, session, doc, now):
        # Upgrade legacy DB tombstones before removing their content.
        self._request(session, Actor(id=doc.owner_id), doc.id, now)
        tasks = session.scalars(
            select(TaskRow)
            .where(TaskRow.document_id == doc.id, TaskRow.owner_id == doc.owner_id)
            .with_for_update()
        ).all()
        if any(task.lease_token is not None and task.lease_expires_at > now for task in tasks):
            return "waiting"
        keys = {doc.blob_key}
        keys.update(
            row.blob_key
            for row in session.scalars(
                select(PageArtifactRow).where(
                    PageArtifactRow.document_id == doc.id, PageArtifactRow.owner_id == doc.owner_id
                )
            )
        )
        keys.update(task.result["blob_key"] for task in tasks if "blob_key" in task.result)
        doc.cleanup_attempts += 1
        try:
            for key in keys:
                self.blobs.delete(doc.owner_id, key)
        except (OSError, ValueError):
            doc.cleanup_failure_code = "storage_cleanup_failed"
            return "failed"
        self._purge_records(session, doc)
        doc.active_files_removed_at, doc.cleanup_failure_code = now, None
        return "cleaned"

    def _clean_attempts(self, now):
        root = (self.blobs.root / ".attempts").resolve()
        if not root.is_relative_to(self.blobs.root) or not root.is_dir():
            return 0
        with self.repository.session_factory() as session:
            active = {
                task.lease_token.hex
                for task in session.scalars(
                    select(TaskRow).where(
                        TaskRow.lease_token.is_not(None), TaskRow.lease_expires_at > now
                    )
                )
            }
        removed = 0
        for path in root.iterdir():
            token = path.name[:32]
            if not re.fullmatch(r"[0-9a-f]{32}-.*", path.name) or token in active:
                continue
            resolved = path.resolve()
            if path.is_symlink() or not resolved.is_relative_to(root) or not path.is_dir():
                continue
            if (
                now.timestamp() - path.stat().st_mtime
                < self.settings.task_timeout_seconds + self.settings.lease_seconds
            ):
                continue
            # Validated absolute attempt directory, never a computed workspace/root target.
            shutil.rmtree(resolved)
            removed += 1
        return removed

    def _clean_orphans(self, now):
        with self.repository.session_factory() as session:
            # Publication may precede its DB commit. A live attempt protects that window.
            if session.scalar(
                select(TaskRow.id)
                .where(TaskRow.lease_token.is_not(None), TaskRow.lease_expires_at > now)
                .limit(1)
            ):
                return 0
            referenced = {
                (row.owner_id, row.blob_key) for row in session.scalars(select(DocumentRow))
            }
            referenced.update(
                (row.owner_id, row.blob_key) for row in session.scalars(select(PageArtifactRow))
            )
            referenced.update(
                (row.owner_id, row.result["blob_key"])
                for row in session.scalars(select(TaskRow))
                if "blob_key" in row.result
            )
            removed = 0
            for owner_path in self.blobs.root.iterdir():
                if (
                    not re.fullmatch(r"[0-9a-f]{32}", owner_path.name)
                    or owner_path.is_symlink()
                    or not owner_path.is_dir()
                ):
                    continue
                owner = UUID(hex=owner_path.name)
                for path in owner_path.rglob("*"):
                    if path.is_symlink() or not path.is_file():
                        continue
                    key = path.relative_to(owner_path).as_posix()
                    if not re.fullmatch(r"[0-9a-f]{32}(?:/[0-9a-f]{32}){0,3}", key):
                        continue
                    if (
                        (owner, key) in referenced
                        or now.timestamp() - path.stat().st_mtime
                        < self.settings.task_timeout_seconds + self.settings.lease_seconds
                    ):
                        continue
                    self.blobs.delete(owner, key)
                    removed += 1
            return removed

    def _clean_upload_scratch(self, now):
        cutoff = now.timestamp() - self.settings.task_timeout_seconds - self.settings.lease_seconds
        removed = 0
        staging = self.blobs.root / ".staging"
        if staging.is_dir() and not staging.is_symlink():
            for path in staging.iterdir():
                resolved = path.resolve()
                if (
                    not re.fullmatch(r"upload-[0-9a-f]{32}-.+", path.name)
                    or path.is_symlink()
                    or not resolved.is_relative_to(staging.resolve())
                    or not path.is_dir()
                    or path.stat().st_mtime > cutoff
                ):
                    continue
                marker = path / ".active"
                if marker.is_symlink():
                    continue
                if marker.is_file():
                    try:
                        with marker.open("r+b") as stream, exclusive_lock(stream):
                            pass
                    except BlockingIOError:
                        continue
                # Exact old opaque directory is confined and its active lock released.
                shutil.rmtree(resolved)
                removed += 1
        for owner in self.blobs.root.iterdir():
            if (
                not re.fullmatch(r"[0-9a-f]{32}", owner.name)
                or owner.is_symlink()
                or not owner.is_dir()
            ):
                continue
            for path in owner.rglob("*.tmp"):
                if (
                    path.is_symlink()
                    or not path.resolve().is_relative_to(owner.resolve())
                    or not re.fullmatch(r"[0-9a-f]{32}\.tmp", path.name)
                    or path.stat().st_mtime > cutoff
                ):
                    continue
                try:
                    with path.open("r+b") as stream, exclusive_lock(stream):
                        pass
                except BlockingIOError:
                    continue
                path.unlink()
                removed += 1
        return removed

    def sweep(self, now: datetime) -> CleanupSummary:
        if now.tzinfo is None:
            raise ValueError("Cleanup time must include its timezone")
        now = now.astimezone(UTC)
        self.journal.verify()
        expired, cleaned, waiting, failed, overdue = 0, 0, 0, 0, 0
        # Reconcile failed DB commits without revoking the independent journal entry.
        for entry in self.journal.events_since(datetime.min.replace(tzinfo=UTC)):
            if entry.event.kind == "delete_document":
                with self.repository.session_factory.begin() as session:
                    doc = session.get(DocumentRow, entry.event.target_id)
                    if doc and doc.owner_id == entry.event.owner_id and doc.deleted_at is None:
                        self._request(
                            session, Actor(id=doc.owner_id), doc.id, now, entry.event.reason
                        )
        with self.repository.session_factory.begin() as session:
            candidates = session.scalars(
                select(DocumentRow)
                .where(
                    DocumentRow.deleted_at.is_(None),
                    DocumentRow.last_foreground_at
                    <= now - timedelta(days=self.settings.retention_days),
                )
                .with_for_update(skip_locked=True)
            ).all()
            for doc in candidates:
                self._request(session, Actor(id=doc.owner_id), doc.id, now, "retention")
                expired += 1
        with self.repository.session_factory() as session:
            identities = session.scalars(
                select(DocumentRow.id).where(
                    DocumentRow.deleted_at.is_not(None),
                    DocumentRow.active_files_removed_at.is_(None),
                )
            ).all()
        for document_id in identities:
            with self.repository.session_factory.begin() as session:
                doc = session.scalar(
                    select(DocumentRow)
                    .where(
                        DocumentRow.id == document_id, DocumentRow.active_files_removed_at.is_(None)
                    )
                    .with_for_update(skip_locked=True)
                )
                if doc is None:
                    continue
                result = self._clean_document(session, doc, now)
                cleaned += result == "cleaned"
                waiting += result == "waiting"
                failed += result == "failed"
                overdue += result != "cleaned" and doc.deleted_at + timedelta(hours=24) < now
        try:
            temporary = self._clean_attempts(now) + self._clean_upload_scratch(now)
        except OSError:
            temporary, failed = 0, failed + 1
        try:
            orphans = self._clean_orphans(now)
        except (OSError, ValueError):
            orphans, failed = 0, failed + 1
        return CleanupSummary(
            documents_expired=expired,
            documents_cleaned=cleaned,
            waiting_for_attempts=waiting,
            failure_count=failed,
            temporary_artifacts_removed=temporary,
            orphan_blobs_removed=orphans,
            overdue_count=overdue,
        )
