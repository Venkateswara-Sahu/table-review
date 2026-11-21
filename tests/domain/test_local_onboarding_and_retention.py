"""Domain verification for opt-in local retention, loopback onboarding, and local launcher."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from table_review.accounts.api import _is_loopback_request
from table_review.accounts.service import AccountService, SetupUnavailable
from table_review.app import create_app
from table_review.lifecycle.deletion import DeletionService
from table_review.lifecycle.journal import LifecycleJournal
from table_review.settings import Settings
from table_review.storage.blobs import PrivateBlobStore
from table_review.storage.models import AccountRow, SessionRow


def _sqlite_auth_factory(db_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    AccountRow.__table__.create(engine)
    SessionRow.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _local_onboarding_app(tmp_path, monkeypatch, *, deployment_mode="local", origin="http://testserver"):
    factory = _sqlite_auth_factory(tmp_path / "auth.sqlite3")
    monkeypatch.setattr("table_review.app.create_session_factory", lambda *args, **kwargs: factory)
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@127.0.0.1/unused",
        blob_root=tmp_path / "blobs",
        model_path=tmp_path / "absent-models",
        journal_path=tmp_path / "lifecycle" / "journal.sqlite3",
        initialize_journal=True,
        deployment_mode=deployment_mode,
        public_origin=origin,
        secure_cookies=(deployment_mode == "hosted"),
        retention_days=None if deployment_mode == "local" else 30,
    )
    return create_app(settings), factory


class _ScalarResult:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)

    def __iter__(self):
        return iter(self._items)


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler

    def scalars(self, query):
        return _ScalarResult(self._handler("scalars", query))

    def scalar(self, query):
        return self._handler("scalar", query)


class _FakeSessionFactory:
    def __init__(self, docs_to_expire):
        self.docs_to_expire = list(docs_to_expire)
        self.retention_queries = 0

    def _handle(self, mode, query):
        text_sql = str(query)
        if mode == "scalars" and "WHERE" in text_sql and "last_foreground_at <=" in text_sql:
            self.retention_queries += 1
            return self.docs_to_expire
        if mode == "scalars":
            return []
        if mode == "scalar":
            return None
        return []

    @contextmanager
    def begin(self):
        yield _FakeSession(self._handle)

    @contextmanager
    def __call__(self):
        yield _FakeSession(self._handle)


# ---------------------------------------------------------------------------
# 1. Opt-in retention policy tests
# ---------------------------------------------------------------------------


def test_retention_from_env_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("TABLE_REVIEW_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1/db")
    monkeypatch.delenv("TABLE_REVIEW_DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("TABLE_REVIEW_RETENTION_DAYS", raising=False)

    assert Settings.from_env().retention_days is None

    monkeypatch.setenv("TABLE_REVIEW_DEPLOYMENT_MODE", "hosted")
    monkeypatch.setenv("TABLE_REVIEW_PUBLIC_ORIGIN", "https://reviews.example.com")
    monkeypatch.setenv("TABLE_REVIEW_SECURE_COOKIES", "true")
    assert Settings.from_env().retention_days == 30

    monkeypatch.setenv("TABLE_REVIEW_RETENTION_DAYS", "none")
    assert Settings.from_env().retention_days is None

    monkeypatch.setenv("TABLE_REVIEW_RETENTION_DAYS", "0")
    assert Settings.from_env().retention_days is None

    monkeypatch.setenv("TABLE_REVIEW_RETENTION_DAYS", "forever")
    assert Settings.from_env().retention_days is None

    monkeypatch.setenv("TABLE_REVIEW_RETENTION_DAYS", "14")
    assert Settings.from_env().retention_days == 14

    monkeypatch.setenv("TABLE_REVIEW_RETENTION_DAYS", "-5")
    with pytest.raises(ValidationError, match="Retention days"):
        Settings.from_env()

    monkeypatch.setenv("TABLE_REVIEW_RETENTION_DAYS", "abc")
    with pytest.raises(ValueError, match="TABLE_REVIEW_RETENTION_DAYS"):
        Settings.from_env()


def test_direct_settings_default_keeps_thirty_day_retention():
    assert Settings().retention_days == 30


def test_deletion_sweep_skips_retention_when_disabled(tmp_path, monkeypatch):
    owner_id, doc_id = uuid4(), uuid4()
    stale_doc = SimpleNamespace(id=doc_id, owner_id=owner_id, blob_key=uuid4().hex)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    stale_attempt = tmp_path / "blobs" / ".attempts" / (uuid4().hex + "-stale")
    stale_attempt.mkdir(parents=True)

    journal = LifecycleJournal(tmp_path / "lifecycle" / "journal.sqlite3", create=True)
    blobs = PrivateBlobStore(tmp_path / "blobs")

    # Local default (retention_days=None): retention query skipped even if stale docs exist.
    factory_disabled = _FakeSessionFactory([stale_doc])
    repo_disabled = SimpleNamespace(session_factory=factory_disabled)
    settings_disabled = Settings(
        blob_root=tmp_path / "blobs",
        journal_path=tmp_path / "lifecycle" / "journal.sqlite3",
        retention_days=None,
    )
    service_disabled = DeletionService(repo_disabled, blobs, journal, settings_disabled)
    recorded: list[tuple[object, object, str]] = []
    monkeypatch.setattr(
        service_disabled,
        "_request",
        lambda session, actor, target_id, at, reason: recorded.append((actor.id, target_id, reason)),
    )
    summary = service_disabled.sweep(now)
    assert summary.documents_expired == 0
    assert factory_disabled.retention_queries == 0
    assert recorded == []

    # Explicit 30-day retention: retention query executes and requests expiry.
    factory_enabled = _FakeSessionFactory([stale_doc])
    repo_enabled = SimpleNamespace(session_factory=factory_enabled)
    settings_enabled = Settings(
        blob_root=tmp_path / "blobs",
        journal_path=tmp_path / "lifecycle" / "journal.sqlite3",
        retention_days=30,
    )
    service_enabled = DeletionService(repo_enabled, blobs, journal, settings_enabled)
    monkeypatch.setattr(
        service_enabled,
        "_request",
        lambda session, actor, target_id, at, reason: recorded.append((actor.id, target_id, reason)),
    )
    summary_enabled = service_enabled.sweep(now + timedelta(days=1))
    assert summary_enabled.documents_expired == 1
    assert factory_enabled.retention_queries == 1
    assert recorded == [(owner_id, doc_id, "retention")]


# ---------------------------------------------------------------------------
# 2. Loopback-only local first-run onboarding tests
# ---------------------------------------------------------------------------


def test_loopback_request_helper_checks_direct_hosts_and_rejects_forwarded():
    assert _is_loopback_request(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={}))
    assert _is_loopback_request(SimpleNamespace(client=SimpleNamespace(host="::1"), headers={}))
    assert _is_loopback_request(SimpleNamespace(client=SimpleNamespace(host="fe80::1%eth0"), headers={})) is False
    assert _is_loopback_request(SimpleNamespace(client=SimpleNamespace(host="::1%lo0"), headers={}))
    assert _is_loopback_request(SimpleNamespace(client=SimpleNamespace(host="localhost"), headers={}))
    assert _is_loopback_request(SimpleNamespace(client=SimpleNamespace(host="testclient"), headers={}))

    assert not _is_loopback_request(
        SimpleNamespace(client=SimpleNamespace(host="192.168.1.25"), headers={})
    )
    assert not _is_loopback_request(
        SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={})
    )
    assert not _is_loopback_request(SimpleNamespace(client=None, headers={}))
    assert not _is_loopback_request(
        SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"x-forwarded-for": "203.0.113.10"},
        )
    )
    assert not _is_loopback_request(
        SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"forwarded": "for=203.0.113.10"},
        )
    )
    assert not _is_loopback_request(
        SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"x-real-ip": "203.0.113.10"},
        )
    )


def test_account_service_bootstrap_first_account_is_strictly_one_time(tmp_path):
    factory = _sqlite_auth_factory(tmp_path / "auth.sqlite3")
    journal = LifecycleJournal(tmp_path / "journal.sqlite3", create=True)
    service = AccountService(factory, journal)

    assert service.has_accounts() is False
    grant = service.bootstrap_first_account("Local_Analyst", "very-strong-local-password")
    assert grant.actor.username == "local_analyst"
    assert service.resolve_session(grant.token) == grant.actor
    assert service.has_accounts() is True

    with pytest.raises(SetupUnavailable, match="already complete"):
        service.bootstrap_first_account("second-user", "another-strong-password-123")


def test_local_setup_endpoints_flow_and_lock_after_first_account(tmp_path, monkeypatch):
    app, _ = _local_onboarding_app(tmp_path, monkeypatch, deployment_mode="local")
    with TestClient(app) as client:
        status = client.get("/api/v1/setup/status")
        assert status.status_code == 200
        assert status.json() == {
            "setup_required": True,
            "deployment_mode": "local",
            "local_onboarding_enabled": True,
        }

        # Exact Origin check still applies to POST /api/v1/setup.
        bad_origin = client.post(
            "/api/v1/setup",
            headers={"Origin": "https://evil.example"},
            json={"username": "analyst", "password": "local-strong-password-123"},
        )
        assert bad_origin.status_code == 403
        assert bad_origin.json()["code"] == "origin_rejected"

        # Forwarded headers disable local onboarding even in local mode.
        proxied_status = client.get(
            "/api/v1/setup/status",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert proxied_status.status_code == 200
        assert proxied_status.json() == {
            "setup_required": False,
            "deployment_mode": "local",
            "local_onboarding_enabled": False,
        }
        proxied_post = client.post(
            "/api/v1/setup",
            headers={"Origin": "http://testserver", "X-Forwarded-For": "203.0.113.9"},
            json={"username": "analyst", "password": "local-strong-password-123"},
        )
        assert proxied_post.status_code == 403
        assert proxied_post.json()["code"] == "setup_unavailable"

        # Short password never leaks in error body.
        weak_pw = client.post(
            "/api/v1/setup",
            headers={"Origin": "http://testserver"},
            json={"username": "analyst", "password": "short"},
        )
        assert weak_pw.status_code == 400
        assert "short" not in weak_pw.text

        # First-run setup creates the account, signs it in, and sets HttpOnly/CSRF cookies.
        created = client.post(
            "/api/v1/setup",
            headers={"Origin": "http://testserver"},
            json={"username": "analyst", "password": "local-strong-password-123"},
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["actor"]["username"] == "analyst"
        assert "tr_session" in client.cookies
        assert "tr_csrf" in client.cookies

        # Authenticated session works immediately.
        me = client.get("/api/v1/session")
        assert me.status_code == 200
        assert me.json()["actor"]["username"] == "analyst"

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        assert caps.json() == {
            "deployment_mode": "local",
            "local_auto_login": False,
            "retention_days": None,
            "default_extraction_backend": "auto",
            "max_selected_pages": 20,
            "available_backends": {
                "native_fast": True,
                "docling": False,
                "vlm": False,
            },
        }

        # Setup is permanently locked after the first account exists.
        after = client.get("/api/v1/setup/status")
        assert after.status_code == 200
        assert after.json()["setup_required"] is False

        second = client.post(
            "/api/v1/setup",
            headers={"Origin": "http://testserver"},
            json={"username": "attacker", "password": "attacker-password-1234"},
        )
        assert second.status_code == 403
        assert second.json()["code"] == "setup_unavailable"


def test_hosted_mode_disables_setup_completely_even_with_zero_accounts(tmp_path, monkeypatch):
    app, factory = _local_onboarding_app(
        tmp_path,
        monkeypatch,
        deployment_mode="hosted",
        origin="https://testserver",
    )
    called_has_accounts = False

    def fail_if_called():
        nonlocal called_has_accounts
        called_has_accounts = True
        return False

    monkeypatch.setattr(app.state.accounts, "has_accounts", fail_if_called)

    with TestClient(app, base_url="https://testserver") as client:
        status = client.get("/api/v1/setup/status")
        assert status.status_code == 200
        assert status.json() == {
            "setup_required": False,
            "deployment_mode": "hosted",
            "local_onboarding_enabled": False,
        }
        assert called_has_accounts is False

        post = client.post(
            "/api/v1/setup",
            headers={"Origin": "https://testserver"},
            json={"username": "analyst", "password": "local-strong-password-123"},
        )
        assert post.status_code == 403
        assert post.json()["code"] == "setup_unavailable"

        # Capabilities endpoint requires authentication (never discloses config anonymously).
        assert client.get("/api/v1/capabilities").status_code == 401

    with factory() as session:
        assert session.query(AccountRow).count() == 0


# ---------------------------------------------------------------------------
# 3. One-command local launcher helper tests
# ---------------------------------------------------------------------------


def test_start_local_helpers(tmp_path):
    from scripts.start_local import build_runtime_env, ensure_dev_env, select_default_backend
    from table_review.runtime import write_model_manifest

    env_file = tmp_path / ".cache" / "dev.env"
    first = ensure_dev_env(env_file)
    assert len(first["POSTGRES_PASSWORD"]) >= 20
    second = ensure_dev_env(env_file)
    assert second == first

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    assert select_default_backend(models_dir, None) == "native_fast"
    assert select_default_backend(models_dir, "vlm") == "vlm"
    (models_dir / "weights.safetensors").write_bytes(b"fixture")
    write_model_manifest(models_dir)
    assert select_default_backend(models_dir, None) == "auto"

    built = build_runtime_env(
        tmp_path,
        database_url="postgresql+psycopg://u:p@127.0.0.1:15432/db",
        port=8123,
        origin="http://127.0.0.1:8123",
        backend="native_fast",
        device="auto",
        retention_days="none",
        base_env={"TABLE_REVIEW_VLM_MODEL": "qwen2.5vl:7b"},
    )
    assert built["TABLE_REVIEW_BIND_HOST"] == "127.0.0.1"
    assert built["TABLE_REVIEW_BIND_PORT"] == "8123"
    assert built["TABLE_REVIEW_DEPLOYMENT_MODE"] == "local"
    assert built["TABLE_REVIEW_LOCAL_AUTO_LOGIN"] == "true"
    assert built["TABLE_REVIEW_RETENTION_DAYS"] == "none"
    assert built["TABLE_REVIEW_EXTRACTION_BACKEND"] == "native_fast"
    assert built["TABLE_REVIEW_VLM_MODEL"] == "qwen2.5vl:7b"


def test_local_auto_login_and_loopback_origin_guards(tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="local auto-login"):
        Settings(
            deployment_mode="hosted",
            public_origin="https://reviews.example.com",
            secure_cookies=True,
            local_auto_login=True,
        )

    factory = _sqlite_auth_factory(tmp_path / "auto-login.sqlite3")
    monkeypatch.setattr("table_review.app.create_session_factory", lambda *args, **kwargs: factory)
    app = create_app(
        Settings(
            database_url="postgresql+psycopg://unused:unused@127.0.0.1/unused",
            blob_root=tmp_path / "blobs",
            model_path=tmp_path / "absent-models",
            journal_path=tmp_path / "lifecycle" / "journal.sqlite3",
            initialize_journal=True,
            deployment_mode="local",
            local_auto_login=True,
            public_origin="http://127.0.0.1:8000",
            secure_cookies=False,
            retention_days=None,
        )
    )
    with TestClient(app) as client:
        # Proxied requests never get auto-logged in.
        proxied = client.get("/api/v1/session", headers={"X-Forwarded-For": "203.0.113.10"})
        assert proxied.status_code == 401

        # Direct loopback opens straight into the local workspace with real session & CSRF cookies.
        session = client.get("/api/v1/session")
        assert session.status_code == 200
        payload = session.json()
        assert payload["actor"]["username"] == "local"
        assert payload["csrf_token"]
        assert "tr_session" in client.cookies
        assert "tr_csrf" in client.cookies

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        assert caps.json()["local_auto_login"] is True

        # Local mode accepts both 127.0.0.1 and localhost on the same port, while rejecting foreign origins.
        bad = client.delete(
            "/api/v1/session",
            headers={"Origin": "https://evil.example", "X-CSRF-Token": payload["csrf_token"]},
        )
        assert bad.status_code == 403
        ok = client.delete(
            "/api/v1/session",
            headers={"Origin": "http://localhost:8000", "X-CSRF-Token": payload["csrf_token"]},
        )
        assert ok.status_code == 204
