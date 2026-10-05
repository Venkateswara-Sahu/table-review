from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from table_review.accounts.api import require_actor
from table_review.accounts.types import Actor
from table_review.documents.types import ImmutableModel
from table_review.reviews.types import ReviewSnapshot, ReviewState

router = APIRouter(prefix="/api/v1")


class CheckedInput(ImmutableModel):
    cell_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    checked: bool


class ApprovalInput(ImmutableModel):
    allow_warnings: bool = False


@router.get("/revisions/{revision_id}/review", response_model=ReviewState)
def review(revision_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.reviews.state(actor, revision_id)


@router.post("/revisions/{revision_id}/checked", response_model=ReviewState)
def checked(
    revision_id: UUID,
    payload: CheckedInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.reviews.mark_checked(
        actor, revision_id, payload.cell_ids, payload.checked
    )


@router.post("/revisions/{revision_id}/approval", response_model=ReviewSnapshot)
def approve(
    revision_id: UUID,
    payload: ApprovalInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.reviews.approve(actor, revision_id, payload.allow_warnings)


@router.get("/approvals/{snapshot_id}", response_model=ReviewSnapshot)
def snapshot(snapshot_id: UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    return request.app.state.reviews.snapshot(actor, snapshot_id)
