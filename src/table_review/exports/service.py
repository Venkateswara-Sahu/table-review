import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from table_review.accounts.types import Actor
from table_review.api_types import public_manifest
from table_review.documents.service import DocumentError
from table_review.exports.types import (
    ExportInput,
    ExportRecord,
    ReportExportInput,
    ReportExportRecord,
    ReportExportTableInput,
    export_manifest,
)
from table_review.jobs.models import TaskRow
from table_review.jobs.queue import TaskConflict, _owner_lock
from table_review.jobs.runner import code_identity, run_operation
from table_review.jobs.types import TaskOutcome
from table_review.reviews.history import bounded_events
from table_review.reviews.timeline import ancestor_ids, read_events
from table_review.reviews.types import CellReviewEvent
from table_review.storage.models import EditEventRow, TableRow
from table_review.storage.repository import NotFound
from table_review.tables.auto_subtotals import detect_subtotals
from table_review.tables.subtotals import evaluate_subtotals
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

    @staticmethod
    def _report_view(task) -> ReportExportRecord:
        snapshot = task.input["snapshot"]
        default_manifest = {
            "export_version": "1",
            "format": "report_workbook",
            "document_id": str(task.document_id),
            "filename": snapshot.get("filename"),
            "table_count": len(snapshot.get("tables", ())),
            "include_formulas": snapshot.get("include_formulas", True),
        }
        return ReportExportRecord(
            id=task.id,
            document_id=task.document_id,
            format="report_workbook",
            state=task.state,
            failure_code=task.failure_code,
            sha256=task.result.get("sha256"),
            manifest=task.result.get("manifest") or default_manifest,
        )

    def _build_export_input(
        self, session, actor, doc, table, revision, state, *, include_formulas: bool = True
    ) -> ExportInput:
        review = self.reviews._view(session, table, state)
        ancestors = ancestor_ids(session, revision)
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
                    EditEventRow.table_id == table.id,
                    EditEventRow.result_revision_id.in_(ancestors),
                )
                .order_by(EditEventRow.created_at, EditEventRow.id)
            )
        )
        timeline, _, through = read_events(
            session, revision, byte_limit=self.reviews.history_bytes_limit
        )
        content = TableContent.model_validate(revision.content)
        unreadable_ids = {
            key
            for key, value in state.cell_reviews.items()
            if value.get("verdict") == "unreadable"
        }
        return ExportInput(
            subtotal_results=tuple(
                result.model_dump(mode="json")
                for result in evaluate_subtotals(content, unreadable_ids=unreadable_ids)
            ),
            detected_subtotals=tuple(
                item.model_dump(mode="json")
                for item in detect_subtotals(content, unreadable_ids=unreadable_ids)
            ),
            include_formulas=include_formulas,
            timeline_events=tuple(event.model_dump(mode="json") for event in timeline),
            timeline_through_sequence=through,
            content=content,
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
            cell_review_events=tuple(
                CellReviewEvent.model_validate(
                    {
                        "id": event.id,
                        "revision_id": event.revision_id,
                        "actor_id": event.actor_id,
                        "created_at": event.created_at,
                        "cell_ids": event.cell_ids,
                        "checked": event.checked,
                        "cell_reviews": event.cell_reviews,
                    }
                ).model_dump(mode="json")
                for event in bounded_events(
                    session, revision.id, byte_limit=self.reviews.history_bytes_limit
                )[0]
            ),
            issues=tuple(issue.model_dump(mode="json") for issue in review.issues),
        )

    def request(
        self, actor, table_id, revision_id, format, idempotency_key, *, include_formulas=True
    ):
        if format not in ("workbook", "csv_package") or not 1 <= len(idempotency_key) <= 128:
            raise ValueError("Invalid export request")
        identity = {"table_id": str(table_id), "revision_id": str(revision_id), "format": format}
        with self.repository.session_factory.begin() as session:
            _owner_lock(session, actor.id)
            table = self.repository._table(session, actor.id, table_id)
            doc = self.repository._lock_document(session, actor.id, table.document_id)
            session.expire(table)
            if table.removed_at is not None:
                raise NotFound("Table not found")
            prior = session.scalar(
                select(TaskRow).where(
                    TaskRow.owner_id == actor.id, TaskRow.idempotency_key == idempotency_key
                )
            )
            if prior:
                if (
                    prior.kind != "export"
                    or any(prior.input.get(k) != v for k, v in identity.items())
                    or prior.input.get("include_formulas", True) != include_formulas
                ):
                    raise TaskConflict("Export retry differs from saved request")
                return self._view(prior)
            _, revision, state = self.reviews._context(session, actor, revision_id, current=True)
            if revision.table_id != table_id:
                raise NotFound("Revision not found")
            data = self._build_export_input(
                session, actor, doc, table, revision, state, include_formulas=include_formulas
            )
            task = self.queue.enqueue_in_session(
                session,
                actor.id,
                doc.id,
                "export",
                {
                    **identity,
                    "include_formulas": include_formulas,
                    "snapshot": data.model_dump(mode="json"),
                },
                idempotency_key,
            )
            doc.last_foreground_at = datetime.now(UTC)
            return self._view(task)

    def request_report(
        self,
        actor,
        document_id,
        table_ids: tuple[UUID, ...] | None,
        include_formulas: bool,
        idempotency_key: str,
    ) -> ReportExportRecord:
        if not 1 <= len(idempotency_key) <= 128:
            raise ValueError("Invalid export request")
        with self.repository.session_factory.begin() as session:
            _owner_lock(session, actor.id)
            doc = self.repository._lock_document(session, actor.id, document_id)
            prior = session.scalar(
                select(TaskRow).where(
                    TaskRow.owner_id == actor.id, TaskRow.idempotency_key == idempotency_key
                )
            )
            requested_ids = [str(tid) for tid in table_ids] if table_ids else None
            if prior:
                if (
                    prior.kind != "export"
                    or prior.document_id != document_id
                    or prior.input.get("format") != "report_workbook"
                    or prior.input.get("include_formulas", True) != include_formulas
                    or (
                        requested_ids is not None
                        and prior.input.get("requested_table_ids") != requested_ids
                    )
                ):
                    raise TaskConflict("Export retry differs from saved request")
                return self._report_view(prior)

            rows = session.scalars(
                select(TableRow)
                .where(
                    TableRow.owner_id == actor.id,
                    TableRow.document_id == document_id,
                    TableRow.removed_at.is_(None),
                )
                .order_by(TableRow.created_at, TableRow.id)
            ).all()
            by_id = {row.id: row for row in rows}
            if table_ids:
                if any(tid not in by_id for tid in table_ids):
                    raise NotFound("Table not found")
                selected_tables = [by_id[tid] for tid in table_ids]
            else:
                selected_tables = list(rows)

            if not selected_tables:
                raise DocumentError("no_tables", "Select at least one table to export", 422)

            entries: list[ReportExportTableInput] = []
            for idx, table in enumerate(selected_tables, 1):
                _, revision, state = self.reviews._context(
                    session, actor, table.current_revision_id, current=True
                )
                export_input = self._build_export_input(
                    session, actor, doc, table, revision, state, include_formulas=include_formulas
                )
                source_ref = export_input.content.source_reference
                page_no = (
                    source_ref.page
                    if source_ref is not None
                    else (
                        export_input.content.cells[0].reference.page
                        if export_input.content.cells
                        else 1
                    )
                )
                entries.append(
                    ReportExportTableInput(
                        table_id=table.id,
                        page_no=page_no,
                        label=export_input.content.title or f"Table {idx} (p.{page_no})",
                        export_input=export_input,
                    )
                )
            if not table_ids:
                entries.sort(key=lambda item: (item.page_no, str(item.table_id)))

            report_input = ReportExportInput(
                document_id=doc.id,
                filename=doc.filename,
                source_sha256=doc.source_sha256,
                tables=tuple(entries),
                include_formulas=include_formulas,
                generation_manifest={**code_identity(), "export_version": "1"},
            )
            task = self.queue.enqueue_in_session(
                session,
                actor.id,
                doc.id,
                "export",
                {
                    "document_id": str(doc.id),
                    "format": "report_workbook",
                    "requested_table_ids": requested_ids,
                    "table_ids": [str(item.table_id) for item in entries],
                    "include_formulas": include_formulas,
                    "snapshot": report_input.model_dump(mode="json"),
                },
                idempotency_key,
            )
            doc.last_foreground_at = datetime.now(UTC)
            return self._report_view(task)

    def get(self, actor, export_id):
        task = self.queue.get(actor.id, export_id)
        if task.kind != "export" or task.input.get("format") == "report_workbook":
            raise NotFound("Export not found")
        return self._view(task)

    def get_report(self, actor, export_id, document_id=None) -> ReportExportRecord:
        task = self.queue.get(actor.id, export_id)
        if task.kind != "export" or task.input.get("format") != "report_workbook":
            raise NotFound("Export not found")
        if document_id is not None and task.document_id != document_id:
            raise NotFound("Export not found")
        return self._report_view(task)

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

    def list_reports(self, actor, document_id) -> tuple[ReportExportRecord, ...]:
        with self.repository.session_factory() as session:
            self.repository._document(session, actor.id, document_id)
            return tuple(
                self._report_view(row)
                for row in session.scalars(
                    select(TaskRow)
                    .where(
                        TaskRow.owner_id == actor.id,
                        TaskRow.document_id == document_id,
                        TaskRow.kind == "export",
                        TaskRow.input["format"].as_string() == "report_workbook",
                    )
                    .order_by(TaskRow.created_at.desc())
                    .limit(50)
                )
            )

    def generate(self, task, lease, work_dir, limits):
        self.queue.documents.source_path(
            Actor(id=task.owner_id), task.document_id, verify_fresh=True
        )
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
