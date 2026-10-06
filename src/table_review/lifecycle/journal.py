"""Independent, durable opaque lifecycle identities. Never copy into DB/blob backups."""

import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from table_review.documents.types import ImmutableModel


class JournalUnavailable(RuntimeError):
    pass


class LifecycleEvent(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    target_id: UUID
    kind: Literal["delete_document", "revoke_account", "reset_password"]
    at: datetime
    auth_epoch: UUID | None = None
    reason: Literal["manual", "retention", "account"] = "manual"

    @field_validator("at")
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None:
            raise ValueError("Journal time must include its timezone")
        return value.astimezone(UTC)


class JournalEntry(ImmutableModel):
    sequence: int
    event: LifecycleEvent
    previous_sha256: str
    sha256: str


def _payload(event):
    return json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class LifecycleJournal:
    def __init__(self, path: Path, *, create=False):
        self.path = Path(path).absolute()
        if create:
            self.initialize()
        if self.path.exists():
            self.verify()

    @contextmanager
    def _connect(self):
        if not self.path.is_file() or self.path.is_symlink():
            raise JournalUnavailable("Current lifecycle journal is unavailable")
        try:
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=15, isolation_level=None
            )
            connection.execute("PRAGMA synchronous=FULL")
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise JournalUnavailable("Current lifecycle journal is unavailable") from error

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self.verify()
            return
        os.close(fd)
        try:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute(
                    "CREATE TABLE identity (version INTEGER NOT NULL, deployment_id TEXT NOT NULL)"
                )
                connection.execute("INSERT INTO identity VALUES (1, ?)", (str(uuid4()),))
                connection.execute(
                    "CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL, target_id TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, payload TEXT NOT NULL, previous_sha256 TEXT NOT NULL, sha256 TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX by_target ON events(owner_id, target_id, kind, sequence)"
                )
                connection.execute("CREATE INDEX by_time ON events(at)")
                connection.execute(
                    "CREATE TRIGGER immutable_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'Lifecycle journal is append only'); END"
                )
                connection.execute(
                    "CREATE TRIGGER immutable_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'Lifecycle journal is append only'); END"
                )
        except sqlite3.Error as error:
            raise JournalUnavailable("Lifecycle journal initialization failed") from error

    @staticmethod
    def _entry(row):
        entry = JournalEntry(
            sequence=row["sequence"],
            event=LifecycleEvent.model_validate_json(row["payload"]),
            previous_sha256=row["previous_sha256"],
            sha256=row["sha256"],
        )
        event = entry.event
        if (row["id"], row["owner_id"], row["target_id"], row["kind"], row["at"]) != (
            str(event.id),
            str(event.owner_id),
            str(event.target_id),
            event.kind,
            event.at.isoformat(),
        ):
            raise JournalUnavailable("Lifecycle journal integrity check failed")
        return entry

    def verify(self):
        try:
            with self._connect() as connection:
                identity = connection.execute(
                    "SELECT version, deployment_id FROM identity"
                ).fetchall()
                if len(identity) != 1 or identity[0]["version"] != 1:
                    raise JournalUnavailable("Lifecycle journal identity is invalid")
                UUID(identity[0]["deployment_id"])
                previous, sequence = "0" * 64, 0
                for row in connection.execute("SELECT * FROM events ORDER BY sequence"):
                    entry = self._entry(row)
                    digest = hashlib.sha256(
                        (previous + _payload(entry.event)).encode("utf-8")
                    ).hexdigest()
                    if (
                        entry.sequence != sequence + 1
                        or entry.previous_sha256 != previous
                        or entry.sha256 != digest
                    ):
                        raise JournalUnavailable("Lifecycle journal integrity check failed")
                    sequence, previous = entry.sequence, entry.sha256
        except (sqlite3.Error, ValueError) as error:
            raise JournalUnavailable("Current lifecycle journal is unavailable") from error

    def append(self, event: LifecycleEvent) -> JournalEntry:
        event = LifecycleEvent.model_validate(event)
        payload = _payload(event)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM events WHERE id=?", (str(event.id),)
                ).fetchone()
                if existing:
                    if existing["payload"] != payload:
                        raise ValueError("Lifecycle event retry differs from saved event")
                    connection.commit()
                    return self._entry(existing)
                last = connection.execute(
                    "SELECT sha256 FROM events ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                previous = last[0] if last else "0" * 64
                digest = hashlib.sha256((previous + payload).encode("utf-8")).hexdigest()
                connection.execute(
                    "INSERT INTO events(id,owner_id,target_id,kind,at,payload,previous_sha256,sha256) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(event.id),
                        str(event.owner_id),
                        str(event.target_id),
                        event.kind,
                        event.at.isoformat(),
                        payload,
                        previous,
                        digest,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM events WHERE id=?", (str(event.id),)
                ).fetchone()
                connection.commit()
                return self._entry(row)
        except sqlite3.Error as error:
            raise JournalUnavailable("Lifecycle event could not be preserved") from error

    def events_since(self, time: datetime) -> tuple[JournalEntry, ...]:
        if time.tzinfo is None:
            raise ValueError("Journal time must include its timezone")
        try:
            with self._connect() as connection:
                return tuple(
                    self._entry(row)
                    for row in connection.execute(
                        "SELECT * FROM events WHERE at>=? ORDER BY sequence",
                        (time.astimezone(UTC).isoformat(),),
                    )
                )
        except sqlite3.Error as error:
            raise JournalUnavailable("Lifecycle journal could not be read") from error

    def deletion_entry(self, owner_id, document_id):
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM events WHERE owner_id=? AND target_id=? AND kind='delete_document' ORDER BY sequence DESC LIMIT 1",
                    (str(owner_id), str(document_id)),
                ).fetchone()
                return self._entry(row) if row else None
        except sqlite3.Error as error:
            raise JournalUnavailable("Lifecycle journal could not be read") from error

    def is_deleted(self, owner_id, document_id):
        return self.deletion_entry(owner_id, document_id) is not None

    def record(self, owner_id, auth_epoch, kind, at):
        return self.append(
            LifecycleEvent(
                id=auth_epoch,
                owner_id=owner_id,
                target_id=owner_id,
                auth_epoch=auth_epoch,
                kind=kind,
                at=at,
                reason="account",
            )
        )

    def allows(self, owner_id, auth_epoch):
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM events WHERE owner_id=? AND kind IN ('reset_password','revoke_account') ORDER BY sequence DESC LIMIT 1",
                    (str(owner_id),),
                ).fetchone()
                if not row:
                    return True
                event = self._entry(row).event
                return event.kind != "revoke_account" and event.auth_epoch == auth_epoch
        except sqlite3.Error as error:
            raise JournalUnavailable("Lifecycle journal could not be read") from error
