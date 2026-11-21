from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse
from pydantic import Field

from table_review.accounts.api import require_actor
from table_review.accounts.types import Actor
from table_review.api_types import APIModel
from table_review.exports.types import ExportFormat, ExportRecord, ReportExportRecord
from table_review.storage.repository import NotFound

router = APIRouter(prefix="/api/v1")


class ExportRequest(APIModel):
    revision_id: UUID
    format: ExportFormat
    include_formulas: bool = True


class ExportList(APIModel):
    items: tuple[ExportRecord, ...]


class ReportExportRequest(APIModel):
    table_ids: tuple[UUID, ...] | None = Field(default=None, max_length=200)
    include_formulas: bool = True


class ReportExportList(APIModel):
    items: tuple[ReportExportRecord, ...]


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
        actor,
        table_id,
        body.revision_id,
        body.format,
        idempotency_key,
        include_formulas=body.include_formulas,
    )


@router.get("/documents/{document_id}/exports", response_model=ReportExportList)
def list_report_exports(
    document_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
):
    return {"items": request.app.state.exports.list_reports(actor, document_id)}


@router.get("/documents/{document_id}/exports/{export_id}", response_model=ReportExportRecord)
def get_report_export(
    document_id: UUID,
    export_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.exports.get_report(actor, export_id, document_id=document_id)


@router.post("/documents/{document_id}/exports", response_model=ReportExportRecord, status_code=202)
def request_report_export(
    document_id: UUID,
    body: ReportExportRequest,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    return request.app.state.exports.request_report(
        actor,
        document_id,
        body.table_ids,
        body.include_formulas,
        idempotency_key,
    )


@router.get("/exports/{export_id}", response_model=ExportRecord)
def get_export(export_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.exports.get(actor, export_id)


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
):
    task = request.app.state.queue.get(actor.id, export_id)
    if task.kind != "export":
        raise NotFound("Export not found")
    path = request.app.state.exports.download(actor, export_id)
    if task.input.get("format") == "report_workbook":
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"report-{task.document_id}.xlsx",
        )
    record = request.app.state.exports.get(actor, export_id)
    workbook = record.format == "workbook"
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if workbook
        else "application/zip",
        filename=f"table-{record.manifest['review_status']}-{record.revision_id}.{'xlsx' if workbook else 'zip'}",
    )
