from datetime import UTC, datetime
from uuid import uuid4, uuid5

from sqlalchemy import or_, select

from table_review.accounts.types import Actor
from table_review.documents.service import DocumentError
from table_review.documents.types import SourceReference
from table_review.reviews.models import ReviewStateRow
from table_review.reviews.timeline import append_event
from table_review.storage.models import (
    EditEventInput,
    EditEventRow,
    RevisionRow,
    TableRecord,
    TableRow,
)
from table_review.storage.repository import RevisionConflict, _record
from table_review.tables.context import ContextIndex
from table_review.tables.grid import ConfirmContext, ConfirmSubtotal, apply_edit, validate_grid
from table_review.tables.subtotal_types import SubtotalResults
from table_review.tables.subtotals import evaluate_subtotals
from table_review.tables.types import CellContent, TableContent


class TableInputError(ValueError):
    pass


class TableService:
    def __init__(self, repository, documents, reviews):
        self.repository, self.documents, self.reviews = repository, documents, reviews

    def edit(self, actor: Actor, table_id, base_revision_id, operation, *, idempotency_key=None):
        base = self.repository.get_revision(actor.id, table_id, base_revision_id)
        key = idempotency_key or uuid4()
        prior = self.repository.get_edit_retry(
            actor.id, table_id, base_revision_id, operation.model_dump(mode="json"), key
        )
        if prior:
            return prior
        try:
            namespace = uuid5(actor.id, f"{table_id}/{base_revision_id}/{key}")
            changed = apply_edit(base.content, operation, cell_namespace=namespace)
        except ValueError as error:
            raise TableInputError(str(error)) from error

        def prepare_content(content, timestamp):
            if isinstance(operation, ConfirmSubtotal):
                return content.model_copy(
                    update={
                        "subtotal_groups": tuple(
                            group.model_copy(
                                update={"confirmed_by": actor.id, "confirmed_at": timestamp}
                            )
                            if group.id == operation.group_id
                            else group
                            for group in content.subtotal_groups
                        )
                    }
                )
            return content.model_copy(
                update={
                    "context_bindings": tuple(
                        binding.model_copy(
                            update={"confirmed_by": actor.id, "confirmed_at": timestamp}
                        )
                        if binding.cell_id in operation.cell_ids
                        and binding.id == operation.candidate_id
                        else binding
                        for binding in content.context_bindings
                    )
                }
            )

        event = EditEventInput(
            idempotency_key=key,
            operation=operation.model_dump(mode="json"),
            affected_cell_ids=changed.affected_cell_ids,
            structural_change=changed.structural_change,
        )
        return self.repository.save_revision(
            actor.id,
            table_id,
            base_revision_id,
            changed.content,
            event,
            on_saved=self._after_save,
            prepare_content=prepare_content
            if isinstance(operation, (ConfirmContext, ConfirmSubtotal))
            else None,
        )

    def _after_save(self, session, old_revision, new_revision, event):
        if event.operation["kind"] == "confirm_subtotal":
            state = session.get(ReviewStateRow, old_revision.id)
            ids = {term["cell_id"] for term in event.operation["terms"]} | {
                event.operation["total_cell_id"]
            }
            if state and any(
                state.cell_reviews.get(identity, {}).get("verdict") == "unreadable"
                for identity in ids
            ):
                raise TableInputError(
                    "An input is marked unreadable; resolve its source review before confirming the subtotal"
                )
        self.reviews.after_revision(session, old_revision, new_revision, event)
        table = session.get(TableRow, new_revision.table_id)
        doc = self.repository._document(session, new_revision.owner_id, table.document_id)
        doc.last_foreground_at = datetime.now(UTC)

    def create_manual(self, actor, document_id, region, rows, columns):
        info = self.documents.info(actor, document_id, region.page)
        if (
            abs(region.page_width - info.width) > 0.01
            or abs(region.page_height - info.height) > 0.01
        ):
            raise DocumentError("invalid_region", "Selection differs from the displayed page")
        if region.right <= region.left or region.bottom <= region.top:
            raise DocumentError("invalid_region", "Select a rectangle with positive area")
        # Validate dimensions before allocating physical cell objects.
        try:
            validate_grid(TableContent(rows=rows, columns=columns, cells=()))
        except ValueError as error:
            raise TableInputError(str(error)) from error
        reference = SourceReference(page=region.page, precision="table", origin="user", box=region)
        content = TableContent(
            rows=rows,
            columns=columns,
            source_reference=reference,
            cells=tuple(
                CellContent(
                    id=str(uuid4()), row=row, col=col, raw_text="", text="", reference=reference
                )
                for row in range(rows)
                for col in range(columns)
            ),
        )
        return self.repository.create_table(
            actor.id,
            document_id,
            content,
            origin="manual",
            extraction_manifest={"origin": "manual", "checks": "checks_not_run"},
        )

    def restore(
        self, actor, table_id, base_revision_id, historical_revision_id, *, idempotency_key=None
    ):
        original = self.repository.get_revision(actor.id, table_id, historical_revision_id)
        self.repository.get_revision(actor.id, table_id, base_revision_id)
        event = EditEventInput(
            idempotency_key=idempotency_key or uuid4(),
            operation={"kind": "restore", "historical_revision_id": str(historical_revision_id)},
            affected_cell_ids=tuple(cell.id for cell in original.content.cells),
            structural_change=True,
        )

        return self.repository.save_revision(
            actor.id,
            table_id,
            base_revision_id,
            original.content.model_copy(update={"context_bindings": ()}),
            event,
            on_saved=self._after_save,
            prepare_manifest=lambda session, old_revision: original.extraction_manifest,
        )

    def context(self, actor, table_id, revision_id, cell_id):
        revision = self.repository.get_revision(actor.id, table_id, revision_id)
        try:
            return ContextIndex(revision.content).cell(cell_id)
        except ValueError as error:
            raise TableInputError(str(error)) from error

    def subtotals(self, actor, table_id, revision_id):
        revision = self.repository.get_revision(actor.id, table_id, revision_id)
        with self.repository.session_factory() as session:
            self.repository._table(session, actor.id, table_id)
            state = session.get(ReviewStateRow, revision_id)
            unreadable = (
                {
                    key
                    for key, value in state.cell_reviews.items()
                    if value.get("verdict") == "unreadable"
                }
                if state
                else set()
            )
            return SubtotalResults(
                items=evaluate_subtotals(revision.content, unreadable_ids=unreadable)
            )

    def set_removed(self, actor, table_id, base_revision_id, expected_version, removed):
        with self.repository.session_factory.begin() as session:
            table = self.repository._table(session, actor.id, table_id, include_removed=True)
            self.repository._lock_document(session, actor.id, table.document_id)
            table = self.repository._table(session, actor.id, table_id, include_removed=True)
            if table.current_revision_id != base_revision_id:
                raise RevisionConflict(table.current_revision_id)
            same_state = (table.removed_at is not None) == removed
            if table.removal_version != expected_version:
                if same_state and table.removal_version == expected_version + 1:
                    return _record(TableRecord, table)
                raise RevisionConflict(table.current_revision_id)
            if same_state:
                return _record(TableRecord, table)
            revision = session.get(RevisionRow, table.current_revision_id)
            state = session.get(ReviewStateRow, revision.id)
            before = {"removed": not removed, "removal_version": table.removal_version}
            invalidated = []
            approval = None
            if removed and state:
                before.update(
                    cell_reviews=state.cell_reviews,
                    checked_cell_ids=state.checked_cell_ids,
                    issue_decisions=state.issue_decisions,
                )
                invalidated = list(state.checked_cell_ids)
                approval = str(state.current_approval_id) if state.current_approval_id else None
                state.checked_cell_ids, state.cell_reviews, state.issue_decisions = [], {}, {}
                state.current_approval_id = None
                state.generation += 1
            table.removed_at = datetime.now(UTC) if removed else None
            table.removal_version += 1
            append_event(
                session,
                revision,
                "table_visibility",
                actor_id=actor.id,
                details={
                    "reason": "Table removed from active work; cell reviews, issue decisions and approval cleared"
                    if removed
                    else "Table restored to active work; renewed review is required",
                    "before": before,
                    "after": {"removed": removed, "removal_version": table.removal_version},
                    "invalidated_cell_ids": invalidated,
                    "invalidated_approval_id": approval,
                },
            )
            session.flush()
            return _record(TableRecord, table)

    def list(self, actor, document_id, page_no=None, *, removed=False):
        with self.repository.session_factory() as session:
            document = self.repository._document(session, actor.id, document_id)
            if page_no is not None and not 1 <= page_no <= document.page_count:
                raise TableInputError("Select an original page in this report")
            query = select(TableRow).where(
                TableRow.owner_id == actor.id,
                TableRow.document_id == document_id,
                TableRow.removed_at.is_not(None) if removed else TableRow.removed_at.is_(None),
            )
            if page_no is not None:
                query = query.join(
                    RevisionRow, RevisionRow.id == TableRow.current_revision_id
                ).where(
                    or_(
                        RevisionRow.content["source_reference"]["page"].as_integer() == page_no,
                        RevisionRow.content["cells"][0]["reference"]["page"].as_integer()
                        == page_no,
                    )
                )
            return tuple(
                _record(TableRecord, row)
                for row in session.scalars(query.order_by(TableRow.created_at.desc()).limit(500))
            )

    def get(self, actor, table_id):
        table = self.repository.get_table(actor.id, table_id)
        return {
            "table": table,
            "revision": self.repository.get_revision(actor.id, table_id, table.current_revision_id),
        }

    def history(self, actor, table_id):
        with self.repository.session_factory() as session:
            self.repository._table(session, actor.id, table_id)
            return tuple(
                {
                    "id": str(row.id),
                    "base_revision_id": str(row.base_revision_id),
                    "result_revision_id": str(row.result_revision_id),
                    "operation": row.operation,
                    "affected_cell_ids": row.affected_cell_ids,
                    "structural_change": row.structural_change,
                    "created_at": row.created_at,
                }
                for row in session.scalars(
                    select(EditEventRow)
                    .where(EditEventRow.owner_id == actor.id, EditEventRow.table_id == table_id)
                    .order_by(EditEventRow.created_at.desc(), EditEventRow.id)
                    .limit(1000)
                )
            )
