from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field, model_validator

from table_review.accounts.api import require_actor
from table_review.accounts.types import Actor
from table_review.documents.service import DocumentError
from table_review.documents.types import ImmutableModel, PageBox

router = APIRouter(prefix="/api/v1")


class RunInput(ImmutableModel):
    pages: tuple[int, ...] = Field(min_length=1, max_length=5)
    region: PageBox | None = None

    @model_validator(mode="after")
    def selected(self):
        if min(self.pages) < 1 or len(set(self.pages)) != len(self.pages):
            raise ValueError("Select distinct positive page numbers")
        if self.region and self.pages != (self.region.page,):
            raise ValueError("A selected rectangle must belong to the single selected page")
        return self


@router.post("/documents/{document_id}/runs", status_code=202)
def submit(
    document_id: UUID,
    payload: RunInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    doc = request.app.state.repository.get_document(actor.id, document_id)
    if max(payload.pages) > doc.page_count:
        raise DocumentError("invalid_page", "Select original pages in this report")
    if payload.region:
        info = request.app.state.documents.info(actor, document_id, payload.region.page)
        if (
            abs(info.width - payload.region.page_width) > 0.01
            or abs(info.height - payload.region.page_height) > 0.01
        ):
            raise DocumentError("invalid_region", "The selection differs from the displayed page")
    return request.app.state.queue.enqueue(
        actor.id, document_id, "extract", payload.model_dump(mode="json"), idempotency_key
    )


@router.get("/documents/{document_id}/tasks")
def tasks(document_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return {"items": request.app.state.queue.list(actor.id, document_id)}


@router.get("/documents/{document_id}/runs")
def runs(document_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return {"items": request.app.state.queue.runs(actor.id, document_id)}


@router.get("/tasks/{task_id}")
def task(task_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.queue.get(actor.id, task_id)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.queue.cancel(actor.id, task_id)


@router.post("/tasks/{task_id}/retry", status_code=202)
def retry(
    task_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    from table_review.jobs.queue import TaskConflict

    original = request.app.state.queue.get(actor.id, task_id)
    if original.state not in ("failed", "cancelled"):
        raise TaskConflict("Only a stopped task can be explicitly retried")
    return request.app.state.queue.enqueue(
        actor.id, original.document_id, original.kind, original.input, idempotency_key
    )
