import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from table_review.accounts.types import Actor
from table_review.api_types import public_manifest
from table_review.documents.service import DocumentError
from table_review.exports.types import ExportInput, ExportRecord, export_manifest
from table_review.jobs.models import TaskRow
from table_review.jobs.queue import TaskConflict, _owner_lock
from table_review.jobs.runner import code_identity, run_operation
from table_review.jobs.types import TaskOutcome
from table_review.storage.models import EditEventRow, RevisionRow
from table_review.storage.repository import NotFound
from table_review.tables.types import TableContent


class ExportService:
    def __init__(self, repository, reviews, queue, blobs):
        self.repository, self.reviews, self.queue, self.blobs = repository, reviews, queue, blobs
        queue.handlers["export"] = self.generate
        queue.publishers["export"] = self.publish

    @staticmethod
    def _view(task):
        return ExportRecord(
            id=task.id,
            table_id=task.input["table_id"],
            revision_id=task.input["revision_id"],
            format=task.input["format"],
            state=task.state,
            failure_code=task.failure_code,
            sha256=task.result.get("sha256"),
            manifest=task.result.get("manifest")
            or export_manifest(
                ExportInput.model_validate(task.input["snapshot"]), task.input["format"]
            ),
        )

    def request(self, actor, table_id, revision_id, format, idempotency_key):
        if format not in ("workbook", "csv_package") or not 1 <= len(idempotency_key) <= 128:
            raise ValueError("Invalid export request")
        identity = {"table_id": str(table_id), "revision_id": str(revision_id), "format": format}
        with self.repository.session_factory.begin() as session:
            _owner_lock(session, actor.id)
            table = self.repository._table(session, actor.id, table_id)
            doc = self.repository._lock_document(session, actor.id, table.document_id)
            session.expire(table)
            prior = session.scalar(
                select(TaskRow).where(
                    TaskRow.owner_id == actor.id, TaskRow.idempotency_key == idempotency_key
                )
            )
            if prior:
                if prior.kind != "export" or any(
                    prior.input.get(k) != v for k, v in identity.items()
                ):
                    raise TaskConflict("Export retry differs from saved request")
                return self._view(prior)
            _, revision, state = self.reviews._context(session, actor, revision_id, current=True)
            if revision.table_id != table_id:
                raise NotFound("Revision not found")
            review = self.reviews._view(session, table, state)
            ancestors, pointer = set(), revision
            while pointer:
                ancestors.add(pointer.id)
                pointer = (
                    session.get(RevisionRow, pointer.parent_revision_id)
                    if pointer.parent_revision_id
                    else None
                )
            events = tuple(
                {
                    "id": str(e.id),
                    "base_revision_id": str(e.base_revision_id),
                    "result_revision_id": str(e.result_revision_id),
                    "actor_id": str(e.owner_id),
                    "created_at": e.created_at.isoformat(),
                    "operation": e.operation,
                    "affected_cell_ids": e.affected_cell_ids,
                }
                for e in session.scalars(
                    select(EditEventRow)
                    .where(
                        EditEventRow.owner_id == actor.id,
                        EditEventRow.table_id == table_id,
                        EditEventRow.result_revision_id.in_(ancestors),
                    )
                    .order_by(EditEventRow.created_at, EditEventRow.id)
                )
            )
            data = ExportInput(
                content=TableContent.model_validate(revision.content),
                revision_id=revision.id,
                review_snapshot=review.current_approval,
                captured_review=review,
                source_manifest={
                    "filename": doc.filename,
                    "document_id": str(doc.id),
                    "source_sha256": doc.source_sha256,
                    "extraction": public_manifest(revision.extraction_manifest),
                },
                generation_manifest={**code_identity(), "export_version": "1"},
                edit_events=events,
                issues=tuple(state.issues),
            )
            task = self.queue.enqueue_in_session(
                session,
                actor.id,
                doc.id,
                "export",
                {**identity, "snapshot": data.model_dump(mode="json")},
                idempotency_key,
            )
            doc.last_foreground_at = datetime.now(UTC)
            return self._view(task)

    def get(self, actor, export_id):
        task = self.queue.get(actor.id, export_id)
        if task.kind != "export":
            raise NotFound("Export not found")
        return self._view(task)

    def list(self, actor, table_id):
        with self.repository.session_factory() as session:
            self.repository._table(session, actor.id, table_id)
            return tuple(
                self._view(row)
                for row in session.scalars(
                    select(TaskRow)
                    .where(
                        TaskRow.owner_id == actor.id,
                        TaskRow.kind == "export",
                        TaskRow.input["table_id"].as_string() == str(table_id),
                    )
                    .order_by(TaskRow.created_at.desc())
                    .limit(50)
                )
            )

    def generate(self, task, lease, work_dir, limits):
        self.queue.documents.source_path(Actor(id=task.owner_id), task.document_id)
        result = run_operation(
            {
                "kind": "export",
                "snapshot": task.input["snapshot"],
                "format": task.input["format"],
                "target": str(work_dir / "artifact"),
            },
            limits,
            work_dir=work_dir,
            cancelled=lambda: not self.queue.active(lease),
            heartbeat=lambda: self.queue.heartbeat(lease, datetime.now(UTC)),
        )
        return TaskOutcome(status="succeeded", result=result)

    def publish(self, session, task, result):
        # Queue calls this inside the active lease and locked undeleted document transaction.
        path = Path(result["path"]).resolve()
        attempts = (self.queue.settings.blob_root / ".attempts").resolve()
        if not path.is_relative_to(attempts) or path.name != "artifact" or path.is_symlink():
            raise ValueError("Invalid private export path")
        key = uuid4().hex
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != result["sha256"]:
                raise ValueError("Export hash differs from generated artifact")
            source.seek(0)
            blob = self.blobs.put(task.owner_id, key, source)
        return {
            "blob_key": key,
            "sha256": blob.sha256,
            "byte_count": blob.byte_count,
            "manifest": result["manifest"],
        }

    def download(self, actor, export_id):
        task = self.queue.get(actor.id, export_id)
        if task.kind != "export":
            raise NotFound("Export not found")
        if task.state != "succeeded":
            raise DocumentError("export_not_ready", "The export is not ready", 409)
        try:
            with self.blobs.open(actor.id, task.result["blob_key"]) as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != task.result["sha256"]:
                    raise DocumentError(
                        "export_integrity", "The export failed its integrity check", 503
                    )
            return self.blobs._path(actor.id, task.result["blob_key"])
        except OSError as error:
            raise DocumentError("export_unavailable", "The export is unavailable", 503) from error
