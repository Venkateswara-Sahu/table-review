from datetime import UTC
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from table_review.storage.db import SessionFactory
from table_review.storage.models import (
    DocumentRecord,
    DocumentRow,
    EditEventInput,
    EditEventRow,
    RevisionRecord,
    RevisionRow,
    TableRecord,
    TableRow,
    UploadRequestRow,
)
from table_review.tables.grid import validate_grid
from table_review.tables.types import TableContent, content_checksum


class NotFound(LookupError):
    """Missing and foreign-owned resources share this outcome."""


class RevisionConflict(RuntimeError):
    def __init__(self, current_revision_id: UUID | None = None):
        super().__init__("The table has changed; reload before saving")
        self.current_revision_id = current_revision_id


class UploadConflict(RuntimeError):
    pass


def _record(cls, row):
    return cls.model_validate({field: getattr(row, field) for field in cls.model_fields})


class Repository:
    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    def _document(self, session, owner_id, document_id):
        row = session.scalar(
            select(DocumentRow).where(
                DocumentRow.id == document_id,
                DocumentRow.owner_id == owner_id,
                DocumentRow.deleted_at.is_(None),
            )
        )
        if row is None:
            raise NotFound("Document not found")
        return row

    def _table(self, session, owner_id, table_id):
        row = session.scalar(
            select(TableRow)
            .join(DocumentRow, TableRow.document_id == DocumentRow.id)
            .where(
                TableRow.id == table_id,
                TableRow.owner_id == owner_id,
                DocumentRow.owner_id == owner_id,
                DocumentRow.deleted_at.is_(None),
            )
        )
        if row is None:
            raise NotFound("Table not found")
        return row

    def create_document(
        self,
        owner_id: UUID,
        *,
        filename: str,
        source_sha256: str,
        byte_count: int,
        page_count: int,
        blob_key: str,
    ) -> DocumentRecord:
        with self.session_factory.begin() as session:
            row = DocumentRow(
                owner_id=owner_id,
                filename=filename,
                source_sha256=source_sha256,
                byte_count=byte_count,
                page_count=page_count,
                blob_key=blob_key,
            )
            session.add(row)
            session.flush()
            return _record(DocumentRecord, row)

    def get_document(self, owner_id: UUID, document_id: UUID) -> DocumentRecord:
        with self.session_factory() as session:
            return _record(DocumentRecord, self._document(session, owner_id, document_id))

    def list_documents(self, owner_id: UUID) -> tuple[DocumentRecord, ...]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(DocumentRow)
                .where(DocumentRow.owner_id == owner_id, DocumentRow.deleted_at.is_(None))
                .order_by(DocumentRow.created_at.desc())
                .limit(100)
            ).all()
            return tuple(_record(DocumentRecord, row) for row in rows)

    def foreground_open(self, owner_id: UUID, document_id: UUID) -> DocumentRecord:
        from datetime import datetime

        with self.session_factory.begin() as session:
            row = self._document(session, owner_id, document_id)
            row.last_foreground_at = datetime.now(UTC)
            session.flush()
            return _record(DocumentRecord, row)

    def _upload_duplicate(self, session, owner_id, key, filename, source_sha256):
        request = session.scalar(
            select(UploadRequestRow).where(
                UploadRequestRow.owner_id == owner_id, UploadRequestRow.key == key
            )
        )
        if request is None:
            return None
        document = self._document(session, owner_id, request.document_id)
        if document.filename != filename or document.source_sha256 != source_sha256:
            raise UploadConflict("Upload key was already used for different content")
        return _record(DocumentRecord, document)

    def create_uploaded_document(
        self,
        owner_id: UUID,
        *,
        key: str,
        filename: str,
        source_sha256: str,
        byte_count: int,
        page_count: int,
        blob_key: str,
        preparation_queue=None,
    ) -> DocumentRecord:
        try:
            with self.session_factory.begin() as session:
                prior = self._upload_duplicate(session, owner_id, key, filename, source_sha256)
                if prior:
                    return prior
                row = DocumentRow(
                    owner_id=owner_id,
                    filename=filename,
                    source_sha256=source_sha256,
                    byte_count=byte_count,
                    page_count=page_count,
                    blob_key=blob_key,
                )
                session.add(row)
                session.flush()
                session.add(UploadRequestRow(owner_id=owner_id, key=key, document_id=row.id))
                if preparation_queue is not None:
                    row.preparation_state = "queued"
                    preparation_queue.enqueue_in_session(
                        session, owner_id, row.id, "prepare", {}, "prepare-" + row.id.hex
                    )
                session.flush()
                return _record(DocumentRecord, row)
        except IntegrityError:
            with self.session_factory() as session:
                prior = self._upload_duplicate(session, owner_id, key, filename, source_sha256)
                if prior:
                    return prior
            raise

    def create_table(
        self,
        owner_id: UUID,
        document_id: UUID,
        content: TableContent,
        *,
        extraction_manifest: dict,
        origin: str = "extracted",
    ) -> TableRecord:
        validate_grid(content)
        if origin not in ("extracted", "manual"):
            raise ValueError("Invalid table origin")
        with self.session_factory.begin() as session:
            self._document(session, owner_id, document_id)
            table_id, revision_id = uuid4(), uuid4()
            row = TableRow(
                id=table_id,
                owner_id=owner_id,
                document_id=document_id,
                origin=origin,
                current_revision_id=revision_id,
                original_revision_id=revision_id,
            )
            session.add(row)
            session.flush()
            session.add(
                RevisionRow(
                    id=revision_id,
                    owner_id=owner_id,
                    table_id=table_id,
                    parent_revision_id=None,
                    content=content.model_dump(mode="json"),
                    checksum=content_checksum(content),
                    extraction_manifest=extraction_manifest,
                )
            )
            session.flush()
            return _record(TableRecord, row)

    def get_table(self, owner_id: UUID, table_id: UUID) -> TableRecord:
        with self.session_factory() as session:
            return _record(TableRecord, self._table(session, owner_id, table_id))

    def get_revision(self, owner_id: UUID, table_id: UUID, revision_id: UUID) -> RevisionRecord:
        with self.session_factory() as session:
            self._table(session, owner_id, table_id)
            row = session.scalar(
                select(RevisionRow).where(
                    RevisionRow.id == revision_id,
                    RevisionRow.table_id == table_id,
                    RevisionRow.owner_id == owner_id,
                )
            )
            if row is None:
                raise NotFound("Revision not found")
            return _record(RevisionRecord, row)

    def _duplicate(self, session, owner_id, table_id, expected_revision_id, checksum, event):
        prior = session.scalar(
            select(EditEventRow).where(
                EditEventRow.owner_id == owner_id,
                EditEventRow.idempotency_key == event.idempotency_key,
            )
        )
        if prior is None:
            return None
        revision = session.get(RevisionRow, prior.result_revision_id)
        if (
            prior.table_id != table_id
            or prior.base_revision_id != expected_revision_id
            or revision.checksum != checksum
            or prior.operation != event.operation
            or prior.affected_cell_ids != list(event.affected_cell_ids)
            or prior.structural_change != event.structural_change
        ):
            raise RevisionConflict()
        return _record(RevisionRecord, revision)

    def save_revision(
        self,
        owner_id: UUID,
        table_id: UUID,
        expected_revision_id: UUID,
        content: TableContent,
        event: EditEventInput,
        *,
        on_saved=None,
    ) -> RevisionRecord:
        validate_grid(content)
        checksum = content_checksum(content)
        try:
            with self.session_factory.begin() as session:
                table = self._table(session, owner_id, table_id)
                prior = self._duplicate(
                    session, owner_id, table_id, expected_revision_id, checksum, event
                )
                if prior:
                    return prior
                if table.current_revision_id != expected_revision_id:
                    raise RevisionConflict(table.current_revision_id)
                new_id = uuid4()
                changed = session.execute(
                    update(TableRow)
                    .where(
                        TableRow.id == table_id,
                        TableRow.owner_id == owner_id,
                        TableRow.current_revision_id == expected_revision_id,
                    )
                    .values(current_revision_id=new_id)
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    prior = self._duplicate(
                        session, owner_id, table_id, expected_revision_id, checksum, event
                    )
                    if prior:
                        return prior
                    session.expire(table)
                    raise RevisionConflict(table.current_revision_id)
                old_revision = session.get(RevisionRow, expected_revision_id)
                row = RevisionRow(
                    id=new_id,
                    owner_id=owner_id,
                    table_id=table_id,
                    parent_revision_id=expected_revision_id,
                    content=content.model_dump(mode="json"),
                    checksum=checksum,
                    extraction_manifest=old_revision.extraction_manifest,
                )
                session.add(row)
                session.flush()
                session.add(
                    EditEventRow(
                        owner_id=owner_id,
                        table_id=table_id,
                        base_revision_id=expected_revision_id,
                        result_revision_id=new_id,
                        idempotency_key=event.idempotency_key,
                        operation=event.operation,
                        affected_cell_ids=list(event.affected_cell_ids),
                        structural_change=event.structural_change,
                    )
                )
                session.flush()
                if on_saved:
                    on_saved(session, old_revision, row, event)
                    session.flush()
                return _record(RevisionRecord, row)
        except IntegrityError:
            with self.session_factory() as session:
                self._table(session, owner_id, table_id)
                prior = self._duplicate(
                    session, owner_id, table_id, expected_revision_id, checksum, event
                )
                if prior:
                    return prior
            raise

    def revision_count(self, table_id: UUID) -> int:
        """Internal operator/test read; never expose this unscoped method as a route."""
        with self.session_factory() as session:
            return session.scalar(
                select(func.count())
                .select_from(RevisionRow)
                .where(RevisionRow.table_id == table_id)
            )
