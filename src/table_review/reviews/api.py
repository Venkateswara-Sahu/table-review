from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from table_review.accounts.api import require_actor
from table_review.accounts.types import Actor
from table_review.checks.types import IssueDecisionInput
from table_review.documents.types import ImmutableModel
from table_review.reviews.types import (
    CellReviewHistory,
    CellReviewInput,
    ReviewSnapshot,
    ReviewState,
    ReviewTimeline,
)

router = APIRouter(prefix="/api/v1")


@router.post("/revisions/{revision_id}/issues/{issue_id}/decision", response_model=ReviewState)
def decide_issue(
    revision_id: UUID,
    issue_id: UUID,
    payload: IssueDecisionInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.reviews.decide_issue(actor, revision_id, issue_id, payload)


class CheckedInput(ImmutableModel):
    cell_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    checked: bool


class ApprovalInput(ImmutableModel):
    allow_warnings: bool = False


@router.post("/revisions/{revision_id}/cell-review", response_model=ReviewState)
def cell_review(
    revision_id: UUID,
    payload: CellReviewInput,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    return request.app.state.reviews.decide_cells(actor, revision_id, payload)


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


@router.get("/revisions/{revision_id}/checked-history", response_model=CellReviewHistory)
def checked_history(
    revision_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return request.app.state.reviews.checked_history(actor, revision_id, offset)


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


@router.get("/revisions/{revision_id}/timeline", response_model=ReviewTimeline)
def timeline(
    revision_id: UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    after: Annotated[int, Query(ge=0)] = 0,
    through: Annotated[int | None, Query(ge=0)] = None,
    cell_id: str | None = None,
):
    return request.app.state.reviews.timeline(
        actor, revision_id, after=after, through=through, cell_id=cell_id
    )
