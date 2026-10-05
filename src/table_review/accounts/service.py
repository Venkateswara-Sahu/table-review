import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from table_review.accounts.types import (
    AccountRecord,
    Actor,
    MemoryRevocationJournal,
    RevocationJournal,
    SessionGrant,
)
from table_review.storage.db import SessionFactory
from table_review.storage.models import AccountRow, SessionRow, SignInThrottleRow


class InvalidAccount(ValueError):
    pass


class AuthenticationError(PermissionError):
    pass


class SignInThrottled(AuthenticationError):
    pass


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _password(value):
    if not 12 <= len(value) <= 1024:
        raise InvalidAccount("Password must contain 12 to 1024 characters")


class AccountService:
    def __init__(self, session_factory: SessionFactory, journal: RevocationJournal | None = None):
        self.session_factory = session_factory
        self.journal = journal or MemoryRevocationJournal()
        self.hasher = PasswordHasher()
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    def create(self, username: str, password: str) -> AccountRecord:
        normalized = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9._@+-]{3,80}", normalized):
            raise InvalidAccount("Use a username with 3 to 80 supported characters")
        _password(password)
        hashed = self.hasher.hash(password)
        try:
            with self.session_factory.begin() as session:
                row = AccountRow(username=normalized, password_hash=hashed)
                session.add(row)
                session.flush()
                return AccountRecord(id=row.id, username=row.username)
        except IntegrityError as error:
            raise InvalidAccount("Username unavailable") from error

    def sign_in(self, username: str, password: str, client_key: str = "local") -> SessionGrant:
        normalized = username.strip().lower()
        throttle_key = secret_hash(normalized + "\0" + client_key)
        now = datetime.now(UTC)
        failure = None
        grant = None
        with self.session_factory.begin() as session:
            session.execute(
                insert(SignInThrottleRow)
                .values(key=throttle_key, failures=0, window_started_at=now)
                .on_conflict_do_nothing()
            )
            throttle = session.scalar(
                select(SignInThrottleRow)
                .where(SignInThrottleRow.key == throttle_key)
                .with_for_update()
            )
            if now - throttle.window_started_at >= timedelta(minutes=5):
                throttle.window_started_at = now
                throttle.failures = 0
            if throttle.failures >= 5:
                failure = SignInThrottled("Invalid credentials")
            else:
                account = session.scalar(
                    select(AccountRow).where(AccountRow.username == normalized)
                )
                try:
                    valid = self.hasher.verify(
                        account.password_hash if account else self._dummy_hash, password
                    )
                except VerificationError:
                    valid = False
                if not valid or account is None or account.revoked_at is not None:
                    throttle.failures += 1
                    failure = AuthenticationError("Invalid credentials")
                else:
                    if self.hasher.check_needs_rehash(account.password_hash):
                        account.password_hash = self.hasher.hash(password)
                    throttle.failures = 0
                    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    expires = now + timedelta(days=7)
                    session.add(
                        SessionRow(
                            owner_id=account.id,
                            auth_epoch=account.auth_epoch,
                            token_hash=secret_hash(token),
                            csrf_hash=secret_hash(csrf),
                            expires_at=expires,
                        )
                    )
                    grant = SessionGrant(
                        actor=Actor(id=account.id, username=account.username),
                        token=token,
                        csrf_token=csrf,
                        expires_at=expires,
                    )
        # Failed attempts must commit before returning an authentication error.
        if failure:
            raise failure
        return grant

    def _session(self, session, token):
        if not token or len(token) > 128:
            raise AuthenticationError("Sign in required")
        row = session.scalar(select(SessionRow).where(SessionRow.token_hash == secret_hash(token)))
        if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
            raise AuthenticationError("Sign in required")
        account = session.get(AccountRow, row.owner_id)
        if (
            account is None
            or account.revoked_at is not None
            or account.auth_epoch != row.auth_epoch
        ):
            raise AuthenticationError("Sign in required")
        return row, account

    def resolve_session(self, token: str) -> Actor:
        with self.session_factory() as session:
            _, account = self._session(session, token)
            return Actor(id=account.id, username=account.username)

    def validate_csrf(self, token: str, csrf: str) -> None:
        with self.session_factory() as session:
            row, _ = self._session(session, token)
            if not csrf or not secrets.compare_digest(row.csrf_hash, secret_hash(csrf)):
                raise AuthenticationError("CSRF token invalid")

    def sign_out(self, token: str) -> None:
        with self.session_factory.begin() as session:
            row, _ = self._session(session, token)
            row.revoked_at = datetime.now(UTC)

    def _invalidate(self, owner_id: UUID, kind: str, password: str | None = None):
        if password is not None:
            _password(password)
        now, epoch = datetime.now(UTC), uuid4()
        with self.session_factory.begin() as session:
            account = session.scalar(
                select(AccountRow).where(AccountRow.id == owner_id).with_for_update()
            )
            if account is None:
                raise InvalidAccount("Account unavailable")
            self.journal.record(owner_id, epoch, kind, now)
            account.auth_epoch = epoch
            if password is None:
                account.revoked_at = now
            else:
                account.password_hash = self.hasher.hash(password)
                account.revoked_at = None
            session.execute(
                update(SessionRow).where(SessionRow.owner_id == owner_id).values(revoked_at=now)
            )

    def reset_password(self, owner_id: UUID, password: str) -> None:
        self._invalidate(owner_id, "reset_password", password)

    def revoke(self, owner_id: UUID) -> None:
        self._invalidate(owner_id, "revoke_account")
