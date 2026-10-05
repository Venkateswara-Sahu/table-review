import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text

from table_review.extraction.types import ExtractionResult
from table_review.jobs.models import AttemptRow, ExtractionRunRow, TaskRow
from table_review.jobs.types import TaskLease, TaskRecord
from table_review.storage.models import DocumentRow, RevisionRow, TableRow
from table_review.storage.repository import NotFound, _record
from table_review.tables.grid import validate_grid
from table_review.tables.types import content_checksum


class QueueFull(RuntimeError):
    pass


class TaskConflict(RuntimeError):
    pass


class StaleLease(RuntimeError):
    pass


def _owner_lock(session, owner_id):
    key = int.from_bytes(hashlib.sha256(owner_id.bytes).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


class TaskQueue:
    def __init__(self, repository, settings, documents=None):
        self.repository, self.settings, self.documents = repository, settings, documents
        self.session_factory = repository.session_factory
        self.handlers = {}
        self.publishers = {}

    def enqueue_in_session(self, session, owner_id, document_id, kind, payload, key):
        if kind not in ("prepare", "extract", "export") or not 1 <= len(key) <= 128:
            raise ValueError("Invalid task request")
        _owner_lock(session, owner_id)
        self.repository._document(session, owner_id, document_id)
        prior = session.scalar(
            select(TaskRow).where(TaskRow.owner_id == owner_id, TaskRow.idempotency_key == key)
        )
        if prior:
            if prior.document_id != document_id or prior.kind != kind or prior.input != payload:
                raise TaskConflict("Task retry key differs from saved request")
            return _record(TaskRecord, prior)
        active = session.scalar(
            select(func.count())
            .select_from(TaskRow)
            .where(TaskRow.owner_id == owner_id, TaskRow.state.in_(("queued", "running")))
        )
        if active >= self.settings.max_user_runs:
            raise QueueFull("Finish or cancel a queued task before submitting another")
        row = TaskRow(
            owner_id=owner_id,
            document_id=document_id,
            kind=kind,
            input=payload,
            idempotency_key=key,
        )
        session.add(row)
        session.flush()
        return _record(TaskRecord, row)

    def enqueue(self, owner_id, document_id, kind, payload, key):
        with self.session_factory.begin() as session:
            return self.enqueue_in_session(session, owner_id, document_id, kind, payload, key)

    def get(self, owner_id, task_id):
        with self.session_factory() as session:
            row = session.scalar(
                select(TaskRow).where(TaskRow.id == task_id, TaskRow.owner_id == owner_id)
            )
            if row is None:
                raise NotFound("Task not found")
            self.repository._document(session, owner_id, row.document_id)
            return _record(TaskRecord, row)

    def list(self, owner_id, document_id):
        with self.session_factory() as session:
            self.repository._document(session, owner_id, document_id)
            return tuple(
                _record(TaskRecord, row)
                for row in session.scalars(
                    select(TaskRow)
                    .where(TaskRow.owner_id == owner_id, TaskRow.document_id == document_id)
                    .order_by(TaskRow.created_at.desc())
                    .limit(100)
                )
            )

    def claim(self, now, worker_id="worker"):
        with self.session_factory.begin() as session:
            # Deployment-wide lock: only one live execution may be claimed, even by two workers.
            session.execute(text("SELECT pg_advisory_xact_lock(714059021)"))
            expired = session.scalars(
                select(TaskRow)
                .where(TaskRow.state == "running", TaskRow.lease_expires_at <= now)
                .with_for_update()
            ).all()
            for row in expired:
                attempt = session.scalar(
                    select(AttemptRow).where(AttemptRow.token == row.lease_token)
                )
                attempt.finished_at, attempt.outcome = now, "infrastructure_interruption"
                row.state = "queued" if row.attempt_count < self.settings.max_attempts else "failed"
                row.failure_code = "infrastructure_interruption"
                row.lease_token = None
            session.flush()
            if session.scalar(
                select(func.count())
                .select_from(TaskRow)
                .where(
                    or_(
                        TaskRow.state == "running",
                        and_(
                            TaskRow.state == "cancelled",
                            TaskRow.lease_token.is_not(None),
                            TaskRow.lease_expires_at > now,
                        ),
                    )
                )
            ):
                return None
            row = session.scalar(
                select(TaskRow)
                .join(DocumentRow, DocumentRow.id == TaskRow.document_id)
                .where(
                    TaskRow.state == "queued",
                    DocumentRow.deleted_at.is_(None),
                    TaskRow.cancellation_requested.is_(False),
                )
                .order_by(TaskRow.created_at, TaskRow.id)
                .with_for_update(of=TaskRow, skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            row.attempt_count += 1
            row.lease_token = uuid4()
            row.state, row.worker_id, row.heartbeat_at = "running", worker_id, now
            row.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
            session.add(
                AttemptRow(
                    task_id=row.id,
                    attempt_no=row.attempt_count,
                    token=row.lease_token,
                    started_at=now,
                )
            )
            return TaskLease(
                task_id=row.id,
                token=row.lease_token,
                expires_at=row.lease_expires_at,
                attempt_no=row.attempt_count,
            )

    def _active_row(self, session, lease, now):
        row = session.scalar(select(TaskRow).where(TaskRow.id == lease.task_id).with_for_update())
        if (
            row is None
            or row.state != "running"
            or row.lease_token != lease.token
            or row.cancellation_requested
        ):
            return None
        if row.lease_expires_at <= now:
            return None
        doc = session.get(DocumentRow, row.document_id)
        if doc is None or doc.deleted_at is not None:
            return None
        return row

    def active(self, lease, now=None):
        with self.session_factory.begin() as session:
            return self._active_row(session, lease, now or datetime.now(UTC)) is not None

    def heartbeat(self, lease, now):
        with self.session_factory.begin() as session:
            row = self._active_row(session, lease, now)
            if row is None:
                raise StaleLease("Attempt is no longer active")
            row.heartbeat_at, row.lease_expires_at = (
                now,
                now + timedelta(seconds=self.settings.lease_seconds),
            )

    def cancel(self, owner_id, task_id):
        with self.session_factory.begin() as session:
            record = session.get(TaskRow, task_id)
            if record is None or record.owner_id != owner_id:
                raise NotFound("Task not found")
            self.repository._document(session, owner_id, record.document_id)
            row = session.scalar(select(TaskRow).where(TaskRow.id == task_id).with_for_update())
            if row.state in ("queued", "running"):
                row.cancellation_requested, row.state = True, "cancelled"
                if row.lease_token:
                    attempt = session.scalar(
                        select(AttemptRow).where(AttemptRow.token == row.lease_token)
                    )
                    attempt.finished_at, attempt.outcome = datetime.now(UTC), "cancelled"
            session.flush()
            return _record(TaskRecord, row)

    def acknowledge_stopped(self, lease):
        """Called only after the runner's finally block has terminated its process tree."""
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(TaskRow).where(TaskRow.id == lease.task_id).with_for_update()
            )
            if row and row.state == "cancelled" and row.lease_token == lease.token:
                row.lease_token = None

    def remaining_seconds(self, lease, now=None):
        now = now or datetime.now(UTC)
        with self.session_factory() as session:
            attempts = session.scalars(
                select(AttemptRow).where(AttemptRow.task_id == lease.task_id)
            ).all()
            used = sum(
                max(0, ((attempt.finished_at or now) - attempt.started_at).total_seconds())
                for attempt in attempts
            )
            return max(0, self.settings.task_timeout_seconds - used)

    def complete(self, lease, outcome, now=None):
        now = now or datetime.now(UTC)
        with self.session_factory.begin() as session:
            identity = session.get(TaskRow, lease.task_id)
            if identity is None:
                return False
            # Same ordering as deletion: lock document before task publication.
            doc = session.scalar(
                select(DocumentRow).where(DocumentRow.id == identity.document_id).with_for_update()
            )
            row = self._active_row(session, lease, now)
            if row is None or doc is None or doc.deleted_at is not None:
                return False
            result = outcome.result
            if outcome.status == "succeeded" and outcome.extraction is not None:
                result = self._publish_extraction(session, row, doc, outcome.extraction)
            if outcome.status == "succeeded" and row.kind in self.publishers:
                result = self.publishers[row.kind](session, row, result)
            if row.kind == "prepare":
                doc.preparation_state = "ready" if outcome.status == "succeeded" else "failed"
            attempt = session.scalar(select(AttemptRow).where(AttemptRow.token == lease.token))
            attempt.finished_at, attempt.outcome = now, outcome.failure_code or outcome.status
            retry = (
                outcome.status == "failed"
                and outcome.failure_code in ("infrastructure_interruption", "storage_failure")
                and row.attempt_count < self.settings.max_attempts
            )
            row.state = "queued" if retry else outcome.status
            row.failure_code, row.result, row.lease_token = outcome.failure_code, result, None
            return True

    def _publish_extraction(self, session, row, doc, payload):
        extracted = ExtractionResult.model_validate(payload)
        if extracted.manifest.get("source_sha256") != doc.source_sha256:
            raise ValueError("Extraction source hash differs from document")
        table_ids, tokens = [], {}
        run_id = uuid4()
        for table in extracted.tables:
            validate_grid(table.content)
            table_id, revision_id = uuid4(), uuid4()
            session.add(
                TableRow(
                    id=table_id,
                    owner_id=row.owner_id,
                    document_id=row.document_id,
                    origin="extracted",
                    current_revision_id=revision_id,
                    original_revision_id=revision_id,
                )
            )
            session.flush()
            session.add(
                RevisionRow(
                    id=revision_id,
                    owner_id=row.owner_id,
                    table_id=table_id,
                    content=table.content.model_dump(mode="json"),
                    checksum=content_checksum(table.content),
                    extraction_manifest={**extracted.manifest, "extraction_run_id": str(run_id)},
                )
            )
            table_ids.append(str(table_id))
            tokens[str(table_id)] = [token.model_dump(mode="json") for token in table.source_tokens]
        session.add(
            ExtractionRunRow(
                id=run_id,
                task_id=row.id,
                owner_id=row.owner_id,
                document_id=row.document_id,
                source_sha256=doc.source_sha256,
                manifest={**extracted.manifest, "request": row.input},
                page_info=[p.model_dump(mode="json") for p in extracted.page_info],
                warnings=list(extracted.warnings),
                table_ids=table_ids,
                source_tokens=tokens,
            )
        )
        session.flush()
        return {"run_id": str(run_id), "table_ids": table_ids, "warnings": list(extracted.warnings)}

    def runs(self, owner_id, document_id):
        with self.session_factory() as session:
            self.repository._document(session, owner_id, document_id)
            return tuple(
                {
                    "id": str(row.id),
                    "task_id": str(row.task_id),
                    "manifest": row.manifest,
                    "table_ids": row.table_ids,
                    "warnings": row.warnings,
                }
                for row in session.scalars(
                    select(ExtractionRunRow)
                    .where(
                        ExtractionRunRow.owner_id == owner_id,
                        ExtractionRunRow.document_id == document_id,
                    )
                    .order_by(ExtractionRunRow.created_at.desc())
                    .limit(100)
                )
            )
