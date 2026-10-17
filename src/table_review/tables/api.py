from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import Field

from table_review.accounts.api import require_actor
from table_review.accounts.types import Actor
from table_review.api_types import EditHistory, RevisionView, TableList, TableView
from table_review.documents.types import ImmutableModel, PageBox
from table_review.storage.models import TableRecord
from table_review.tables.adoption import AdoptInput, AdoptionPreview
from table_review.tables.context_types import CellContext
from table_review.tables.grid import EditOperation
from table_review.tables.subtotal_types import SubtotalResults

router = APIRouter(prefix="/api/v1")


@router.get("/tables/{table_id}/adoption-preview", response_model=AdoptionPreview)
def adoption_preview(
    table_id: UUID,
    candidate_table_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return request.app.state.adoption.preview(actor, table_id, candidate_table_id, offset)


@router.post("/tables/{table_id}/adopt", response_model=RevisionView)
def adopt(
    table_id: UUID,
    payload: AdoptInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    idempotency_key: Annotated[UUID, Header()],
):
    return request.app.state.adoption.adopt(actor, table_id, payload, idempotency_key)


@router.get("/tables/{table_id}/revisions/{revision_id}/subtotals", response_model=SubtotalResults)
def subtotals(
    table_id: UUID,
    revision_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.tables.subtotals(actor, table_id, revision_id)


class EditInput(ImmutableModel):
    base_revision_id: UUID
    operation: EditOperation


class RestoreInput(ImmutableModel):
    base_revision_id: UUID
    historical_revision_id: UUID


class ManualInput(ImmutableModel):
    region: PageBox
    rows: int = Field(ge=1, le=500)
    columns: int = Field(ge=1, le=60)


class RemovalInput(ImmutableModel):
    base_revision_id: UUID
    expected_version: int = Field(ge=0)
    removed: bool


@router.get("/documents/{document_id}/tables", response_model=TableList)
def tables(
    document_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    page_no: int | None = None,
    removed: bool = False,
):
    return {"items": request.app.state.tables.list(actor, document_id, page_no, removed=removed)}


@router.post("/tables/{table_id}/removal", response_model=TableRecord)
def removal(
    table_id: UUID,
    payload: RemovalInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.tables.set_removed(
        actor, table_id, payload.base_revision_id, payload.expected_version, payload.removed
    )


@router.post("/documents/{document_id}/tables", status_code=201, response_model=TableRecord)
def manual(
    document_id: UUID,
    payload: ManualInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.tables.create_manual(
        actor, document_id, payload.region, payload.rows, payload.columns
    )


@router.get("/tables/{table_id}", response_model=TableView)
def table(table_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.tables.get(actor, table_id)


@router.get("/tables/{table_id}/revisions/{revision_id}", response_model=RevisionView)
def revision(
    table_id: UUID,
    revision_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.repository.get_revision(actor.id, table_id, revision_id)


@router.get("/tables/{table_id}/history", response_model=EditHistory)
def history(table_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return {"items": request.app.state.tables.history(actor, table_id)}


@router.get(
    "/tables/{table_id}/revisions/{revision_id}/context/{cell_id}", response_model=CellContext
)
def cell_context(
    table_id: UUID,
    revision_id: UUID,
    cell_id: str,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.tables.context(actor, table_id, revision_id, cell_id)


@router.post("/tables/{table_id}/edits", response_model=RevisionView)
def edit(
    table_id: UUID,
    payload: EditInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    idempotency_key: Annotated[UUID, Header()],
):
    return request.app.state.tables.edit(
        actor,
        table_id,
        payload.base_revision_id,
        payload.operation,
        idempotency_key=idempotency_key,
    )


@router.post("/tables/{table_id}/restore", response_model=RevisionView)
def restore(
    table_id: UUID,
    payload: RestoreInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    idempotency_key: Annotated[UUID, Header()],
):
    return request.app.state.tables.restore(
        actor,
        table_id,
        payload.base_revision_id,
        payload.historical_revision_id,
        idempotency_key=idempotency_key,
    )
