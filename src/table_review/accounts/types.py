from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import Field

from table_review.documents.types import ImmutableModel


class Actor(ImmutableModel):
    id: UUID
    username: str = ""


class AccountRecord(ImmutableModel):
    id: UUID
    username: str


class SessionGrant(ImmutableModel):
    actor: Actor
    token: str = Field(repr=False)
    csrf_token: str = Field(repr=False)
    expires_at: datetime


class RevocationJournal(Protocol):
    def record(self, owner_id: UUID, auth_epoch: UUID, kind: str, at: datetime) -> None: ...
    def allows(self, owner_id: UUID, auth_epoch: UUID) -> bool: ...


class MemoryRevocationJournal:
    """Task-4 contract implementation; durable lifecycle reconciliation arrives in Task 12."""

    def __init__(self):
        self.events = []

    def record(self, owner_id, auth_epoch, kind, at):
        self.events.append((owner_id, auth_epoch, kind, at))

    def allows(self, owner_id, auth_epoch):
        latest = next((event for event in reversed(self.events) if event[0] == owner_id), None)
        return latest is None or (latest[2] != "revoke_account" and latest[1] == auth_epoch)
