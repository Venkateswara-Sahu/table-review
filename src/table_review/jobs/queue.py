import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text

from table_review.extraction.comparison import compare_tables, match_tables, request_scope
from table_review.extraction.types import ExtractionResult
from table_review.jobs.models import AttemptRow, ExtractionRunRow, TaskRow
from table_review.jobs.types import TaskLease, TaskRecord
from table_review.storage.models import DocumentRow, RevisionRow, TableRow
from table_review.storage.repository import NotFound, _record
from table_review.tables.grid import validate_grid
from table_review.tables.types import TableContent, content_checksum


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
        self.repository._lock_document(session, owner_id, document_id)
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
        if self.repository.journal and self.repository.journal.is_deleted(
            row.owner_id, row.document_id
        ):
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
                if result["conversion_outcome"] == "failure":
                    outcome = outcome.model_copy(
                        update={"status": "failed", "failure_code": "parse_failure"}
                    )
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
        import time

        started = time.monotonic()
        extracted = ExtractionResult.model_validate(payload)
        if extracted.manifest.get("source_sha256") != doc.source_sha256:
            raise ValueError("Extraction source hash differs from document")
        table_ids, tokens = [], {}
        contents = {}
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
            contents[str(table_id)] = table.content
            tokens[str(table_id)] = [token.model_dump(mode="json") for token in table.source_tokens]
        comparison = self._compare_extraction(session, row, contents)
        run = ExtractionRunRow(
            id=run_id,
            task_id=row.id,
            owner_id=row.owner_id,
            document_id=row.document_id,
            source_sha256=doc.source_sha256,
            manifest={
                **extracted.manifest,
                "request": row.input,
                "request_scope_sha256": request_scope(row.input),
                "comparison": comparison,
                "publication_timing": {
                    "seconds": time.monotonic() - started,
                    "table_count": len(table_ids),
                    "semantics": "publication_work_before_final_flush_and_commit",
                },
            },
            page_info=[p.model_dump(mode="json") for p in extracted.page_info],
            warnings=list(extracted.warnings),
            table_ids=table_ids,
            source_tokens=tokens,
        )
        session.add(run)
        session.flush()
        return {
            "run_id": str(run_id),
            "table_ids": table_ids,
            "warnings": list(extracted.warnings),
            "conversion_outcome": extracted.manifest.get("conversion_outcome", "legacy_unknown"),
        }

    def _compare_extraction(self, session, row, contents):
        scope = request_scope(row.input)
        # Project identities only: never hydrate old source-token or diff payloads while
        # the document is locked. Legacy lookup is explicitly limited to 20 recent runs.
        base = (
            select(
                ExtractionRunRow.id,
                ExtractionRunRow.table_ids,
                ExtractionRunRow.created_at,
                ExtractionRunRow.manifest["request"].label("request"),
            )
            .where(
                ExtractionRunRow.owner_id == row.owner_id,
                ExtractionRunRow.document_id == row.document_id,
                func.coalesce(
                    ExtractionRunRow.manifest["conversion_outcome"].astext, "legacy_unknown"
                )
                != "failure",
            )
            .order_by(ExtractionRunRow.created_at.desc(), ExtractionRunRow.id.desc())
        )
        previous = session.execute(
            base.where(ExtractionRunRow.manifest["request_scope_sha256"].astext == scope).limit(1)
        ).first()
        legacy_query = base.where(
            ExtractionRunRow.manifest["request_scope_sha256"].astext.is_(None)
        )
        if previous:
            legacy_query = legacy_query.where(ExtractionRunRow.created_at >= previous.created_at)
        legacy = next(
            (
                run
                for run in session.execute(legacy_query.limit(20))
                if run.request and request_scope(run.request) == scope
            ),
            None,
        )
        if legacy and (
            previous is None or (legacy.created_at, legacy.id) > (previous.created_at, previous.id)
        ):
            previous = legacy
        if previous is None:
            return {
                "status": "no_comparable_baseline",
                "review_transferred": False,
                "legacy_search_limit": 20,
            }
        originals, revisions = {}, {}
        for identity in previous.table_ids:
            table = session.get(TableRow, UUID(identity))
            original = session.get(RevisionRow, table.original_revision_id)
            reviewed = session.get(RevisionRow, table.current_revision_id)
            originals[identity] = TableContent.model_validate(original.content)
            revisions[identity] = (original, reviewed)
        comparison = match_tables(originals, contents)
        for match in comparison["matches"]:
            key, candidate = match["before_id"], contents[match["after_id"]]
            original, reviewed = revisions[key]
            match.update(
                {
                    "original_revision_id": str(original.id),
                    "reviewed_revision_id": str(reviewed.id),
                    "has_review_revisions": original.id != reviewed.id,
                    "original_diff": compare_tables(originals[key], candidate),
                    "reviewed_diff": compare_tables(
                        TableContent.model_validate(reviewed.content), candidate
                    ),
                }
            )
        return {
            **comparison,
            "status": "compared",
            "baseline_run_id": str(previous.id),
            "review_transferred": False,
            "snapshot_at": "rerun_publication",
            "replacement_performed": False,
        }

    def runs(self, owner_id, document_id, *, include_details=True, limit=100):
        with self.session_factory() as session:
            self.repository._document(session, owner_id, document_id)
            manifest = (
                ExtractionRunRow.manifest
                if include_details
                else func.jsonb_build_object(
                    "conversion_outcome",
                    ExtractionRunRow.manifest["conversion_outcome"],
                    "request",
                    ExtractionRunRow.manifest["request"],
                    "parser_executed",
                    ExtractionRunRow.manifest["parser_executed"],
                    "evidence_kind",
                    ExtractionRunRow.manifest["evidence_kind"],
                )
            )
            return tuple(
                {
                    "id": str(row.id),
                    "task_id": str(row.task_id),
                    "manifest": row.manifest,
                    "table_ids": row.table_ids,
                    "warnings": row.warnings,
                }
                for row in session.execute(
                    select(
                        ExtractionRunRow.id,
                        ExtractionRunRow.task_id,
                        manifest.label("manifest"),
                        ExtractionRunRow.table_ids,
                        ExtractionRunRow.warnings,
                    )
                    .where(
                        ExtractionRunRow.owner_id == owner_id,
                        ExtractionRunRow.document_id == document_id,
                    )
                    .order_by(ExtractionRunRow.created_at.desc())
                    .limit(min(100, max(1, limit)))
                )
            )

    def get_run(self, owner_id, document_id, run_id):
        with self.session_factory() as session:
            self.repository._document(session, owner_id, document_id)
            row = session.execute(
                select(
                    ExtractionRunRow.id,
                    ExtractionRunRow.task_id,
                    ExtractionRunRow.manifest,
                    ExtractionRunRow.table_ids,
                    ExtractionRunRow.warnings,
                ).where(
                    ExtractionRunRow.id == run_id,
                    ExtractionRunRow.owner_id == owner_id,
                    ExtractionRunRow.document_id == document_id,
                )
            ).first()
            if row is None:
                raise NotFound("Extraction run not found")
            return {
                "id": str(row.id),
                "task_id": str(row.task_id),
                "manifest": row.manifest,
                "table_ids": row.table_ids,
                "warnings": row.warnings,
            }
