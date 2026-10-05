from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, UploadFile
from fastapi.responses import FileResponse

from table_review.accounts.api import require_actor
from table_review.accounts.types import Actor

router = APIRouter(prefix="/api/v1/documents")


@router.post("", status_code=201)
def upload(
    request: Request,
    file: UploadFile,
    actor: Annotated[Actor, Depends(require_actor)],
    idempotency_key: Annotated[str, Header()],
):
    try:
        return request.app.state.documents.upload(
            actor, file.file, file.filename or "", idempotency_key
        )
    finally:
        file.file.close()


@router.get("")
def documents(request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return {"items": request.app.state.repository.list_documents(actor.id)}


@router.get("/{document_id}")
def document(document_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.repository.get_document(actor.id, document_id)


@router.post("/{document_id}/open")
def foreground_open(
    document_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
):
    return request.app.state.documents.foreground_open(actor, document_id)


@router.get("/{document_id}/inspect")
def inspect(document_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.documents.inspect(actor, document_id)


@router.get("/{document_id}/source")
def source(document_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    path = request.app.state.documents.source_path(actor, document_id)
    return FileResponse(path, media_type="application/pdf", filename=f"source-{document_id}.pdf")


@router.get("/{document_id}/pages/{page_no}")
def page_info(
    document_id: UUID,
    page_no: int,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    info = request.app.state.documents.info(actor, document_id, page_no)
    return {
        **info.model_dump(),
        "extraction_supported": info.digital_text_present,
        "unsupported_reason": None
        if info.digital_text_present
        else "This page has no usable digital text; automatic extraction is unsupported",
    }


@router.get("/{document_id}/pages/{page_no}/image")
def page_image(
    document_id: UUID,
    page_no: int,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    scale: float = 1.5,
):
    rendered = request.app.state.documents.page(actor, document_id, page_no, scale)
    return FileResponse(rendered.image_path, media_type="image/png")
