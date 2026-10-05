from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse

from table_review.accounts.api import require_actor
from table_review.accounts.types import Actor
from table_review.api_types import APIModel
from table_review.exports.types import ExportFormat, ExportRecord

router = APIRouter(prefix="/api/v1")


class ExportRequest(APIModel):
    revision_id: UUID
    format: ExportFormat


class ExportList(APIModel):
    items: tuple[ExportRecord, ...]


@router.get("/tables/{table_id}/exports", response_model=ExportList)
def list_exports(table_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return {"items": request.app.state.exports.list(actor, table_id)}


@router.post("/tables/{table_id}/exports", response_model=ExportRecord, status_code=202)
def request_export(
    table_id: UUID,
    body: ExportRequest,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    return request.app.state.exports.request(
        actor, table_id, body.revision_id, body.format, idempotency_key
    )


@router.get("/exports/{export_id}", response_model=ExportRecord)
def get_export(export_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.exports.get(actor, export_id)


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
):
    record = request.app.state.exports.get(actor, export_id)
    path = request.app.state.exports.download(actor, export_id)
    workbook = record.format == "workbook"
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if workbook
        else "application/zip",
        filename=f"table-{record.manifest['review_status']}-{record.revision_id}.{'xlsx' if workbook else 'zip'}",
    )
