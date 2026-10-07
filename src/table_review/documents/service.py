import hashlib
import math
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from pypdf.errors import PdfReadError
from pypdfium2 import PdfiumError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from table_review.accounts.types import Actor
from table_review.documents.pdf import inspect_pdf, page_info, render_page
from table_review.documents.types import PageBox, PdfInfo, RenderedPage
from table_review.settings import Settings
from table_review.storage.blobs import PrivateBlobStore
from table_review.storage.models import DocumentRecord, PageArtifactRow
from table_review.storage.repository import Repository
from table_review.storage.scratch import upload_scratch


class DocumentError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class DocumentService:
    def __init__(self, repository: Repository, blobs: PrivateBlobStore, settings: Settings):
        self.repository, self.blobs, self.settings = repository, blobs, settings
        self.preparation_queue = None
        self.staging = blobs.root / ".staging"
        self.staging.mkdir(exist_ok=True, mode=0o700)

    def upload(
        self, actor: Actor, stream: BinaryIO, filename: str, idempotency_key: str
    ) -> DocumentRecord:
        if not filename.lower().endswith(".pdf") or not 1 <= len(filename) <= 1024:
            raise DocumentError("unsupported_file", "Choose a PDF file")
        if not idempotency_key or len(idempotency_key) > 128:
            raise DocumentError("invalid_upload_key", "Supply a valid upload retry key")
        with upload_scratch(self.staging) as temporary:
            staged = Path(temporary) / "source.pdf"
            count = 0
            with staged.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    count += len(chunk)
                    if count > self.settings.max_pdf_bytes:
                        raise DocumentError(
                            "byte_limit", "PDF exceeds the configured file size limit", 413
                        )
                    output.write(chunk)
            with staged.open("rb") as source:
                if source.read(5) != b"%PDF-":
                    raise DocumentError("invalid_pdf", "This file does not contain a supported PDF")
                source.seek(0)
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            info = self._inspect(staged)
            key = uuid4().hex
            with staged.open("rb") as source:
                self.blobs.put(actor.id, key, source)
            try:
                result = self.repository.create_uploaded_document(
                    actor.id,
                    key=idempotency_key,
                    filename=filename,
                    source_sha256=digest,
                    byte_count=count,
                    page_count=info.page_count,
                    blob_key=key,
                    preparation_queue=self.preparation_queue,
                )
            except BaseException:
                self.blobs.delete(actor.id, key)
                raise
            if result.blob_key != key:
                self.blobs.delete(actor.id, key)
            return result

    def _inspect(self, path: Path) -> PdfInfo:
        try:
            info = inspect_pdf(path)
        except PdfReadError as error:
            raise DocumentError("invalid_pdf", "The PDF could not be read") from error
        except ValueError as error:
            if "500 page" in str(error):
                raise DocumentError(
                    "page_limit", "PDF exceeds the configured page limit"
                ) from error
            if "50 MiB" in str(error):
                raise DocumentError(
                    "byte_limit", "PDF exceeds the configured file limit", 413
                ) from error
            raise DocumentError("invalid_pdf", "The PDF could not be read") from error
        if info.encrypted:
            raise DocumentError(
                "encrypted_pdf", "Encrypted PDFs are unsupported; upload an unencrypted copy"
            )
        if not info.page_count:
            raise DocumentError("invalid_pdf", "PDF contains no pages")
        if info.page_count > self.settings.max_pdf_pages:
            raise DocumentError("page_limit", "PDF exceeds the configured page limit")
        return info

    def source_path(self, actor: Actor, document_id: UUID) -> Path:
        document = self.repository.get_document(actor.id, document_id)
        try:
            with self.blobs.open(actor.id, document.blob_key) as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != document.source_sha256:
                    raise DocumentError(
                        "source_changed", "Stored source no longer matches the saved report", 503
                    )
                return Path(source.name)
        except FileNotFoundError as error:
            raise DocumentError(
                "source_unavailable", "Stored source is unavailable", 503
            ) from error

    def inspect(self, actor: Actor, document_id: UUID) -> PdfInfo:
        return self._inspect(self.source_path(actor, document_id))

    def info(self, actor: Actor, document_id: UUID, page_no: int):
        document = self.repository.get_document(actor.id, document_id)
        if not 1 <= page_no <= document.page_count:
            raise DocumentError("invalid_page", "Select an original page number in this report")
        try:
            return page_info(self.source_path(actor, document_id), page_no)
        except PdfiumError as error:
            raise DocumentError("page_unavailable", "This PDF page could not be read") from error

    def page(self, actor: Actor, document_id: UUID, page_no: int, scale: float) -> RenderedPage:
        if not math.isfinite(scale) or not 0 < scale <= 4:
            raise DocumentError(
                "invalid_scale", "Use a page render scale greater than zero and at most four"
            )
        info = self.info(actor, document_id, page_no)
        if (
            math.ceil(info.width * scale) * math.ceil(info.height * scale)
            > self.settings.max_render_pixels
        ):
            raise DocumentError(
                "pixel_limit", "Page image exceeds the render budget; use a smaller scale"
            )
        document = self.repository.get_document(actor.id, document_id)
        with self.repository.session_factory() as session:
            cached = session.scalar(
                select(PageArtifactRow).where(
                    PageArtifactRow.owner_id == actor.id,
                    PageArtifactRow.document_id == document_id,
                    PageArtifactRow.page_no == page_no,
                    PageArtifactRow.scale == scale,
                    PageArtifactRow.source_sha256 == document.source_sha256,
                )
            )
            if cached and cached.sha256:
                try:
                    with self.blobs.open(actor.id, cached.blob_key) as image:
                        if hashlib.file_digest(image, "sha256").hexdigest() != cached.sha256:
                            raise DocumentError(
                                "page_integrity", "The page failed its integrity check", 503
                            )
                        return RenderedPage(
                            image_path=Path(image.name),
                            page_box=PageBox(
                                page=page_no,
                                left=0,
                                top=0,
                                right=cached.width,
                                bottom=cached.height,
                                page_width=cached.width,
                                page_height=cached.height,
                            ),
                            pixel_width=cached.pixel_width,
                            pixel_height=cached.pixel_height,
                        )
                except FileNotFoundError:
                    pass
        try:
            rendered = render_page(self.source_path(actor, document_id), page_no, scale)
        except (ValueError, PdfiumError) as error:
            raise DocumentError(
                "page_unavailable", "This page could not be rendered at the selected scale"
            ) from error
        key = f"{document.id.hex}/{uuid4().hex}"
        try:
            with self.repository.session_factory.begin() as session:
                self.repository._lock_document(session, actor.id, document_id)
                with rendered.image_path.open("rb") as image:
                    blob = self.blobs.put(actor.id, key, image)
                session.execute(
                    insert(PageArtifactRow)
                    .values(
                        id=uuid4(),
                        owner_id=actor.id,
                        document_id=document_id,
                        page_no=page_no,
                        scale=scale,
                        source_sha256=document.source_sha256,
                        blob_key=key,
                        sha256=blob.sha256,
                        width=info.width,
                        height=info.height,
                        pixel_width=rendered.pixel_width,
                        pixel_height=rendered.pixel_height,
                    )
                    .on_conflict_do_update(
                        constraint="uq_page_render",
                        set_={"blob_key": key, "sha256": blob.sha256},
                    )
                )
        finally:
            rendered.image_path.unlink(missing_ok=True)
        with self.blobs.open(actor.id, key) as image:
            return rendered.model_copy(update={"image_path": Path(image.name)})

    def foreground_open(self, actor: Actor, document_id: UUID) -> DocumentRecord:
        return self.repository.foreground_open(actor.id, document_id)
