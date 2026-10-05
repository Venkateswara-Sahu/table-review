from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import or_, select

from table_review.accounts.types import Actor
from table_review.documents.service import DocumentError
from table_review.documents.types import SourceReference
from table_review.storage.models import (
    EditEventInput,
    EditEventRow,
    RevisionRow,
    TableRecord,
    TableRow,
)
from table_review.storage.repository import _record
from table_review.tables.grid import apply_edit, validate_grid
from table_review.tables.types import CellContent, TableContent


class TableInputError(ValueError):
    pass


class TableService:
    def __init__(self, repository, documents, reviews):
        self.repository, self.documents, self.reviews = repository, documents, reviews

    def edit(self, actor: Actor, table_id, base_revision_id, operation, *, idempotency_key=None):
        base = self.repository.get_revision(actor.id, table_id, base_revision_id)
        try:
            changed = apply_edit(base.content, operation)
        except ValueError as error:
            raise TableInputError(str(error)) from error
        event = EditEventInput(
            idempotency_key=idempotency_key or uuid4(),
            operation=operation.model_dump(mode="json"),
            affected_cell_ids=changed.affected_cell_ids,
            structural_change=changed.structural_change,
        )
        return self.repository.save_revision(
            actor.id, table_id, base_revision_id, changed.content, event, on_saved=self._after_save
        )

    def _after_save(self, session, old_revision, new_revision, event):
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
            actor.id, table_id, base_revision_id, original.content, event, on_saved=self._after_save
        )

    def list(self, actor, document_id, page_no=None):
        with self.repository.session_factory() as session:
            document = self.repository._document(session, actor.id, document_id)
            if page_no is not None and not 1 <= page_no <= document.page_count:
                raise TableInputError("Select an original page in this report")
            query = select(TableRow).where(
                TableRow.owner_id == actor.id, TableRow.document_id == document_id
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
