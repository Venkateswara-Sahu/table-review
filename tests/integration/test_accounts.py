import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest


def test_revoked_session_stops_work(client, admin_service, owner):
    admin_service.revoke(owner.id)
    assert client.get("/api/v1/session").status_code == 401
    assert (
        client.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-test-password-123"},
        ).status_code
        == 401
    )


def test_secret_hashes_and_seven_day_expiry(client, owner, session_factory):
    from sqlalchemy import select

    from table_review.storage.models import AccountRow, SessionRow

    token = client.cookies.get("tr_session")
    with session_factory() as session:
        account = session.get(AccountRow, owner.id)
        grant = session.scalar(select(SessionRow).where(SessionRow.owner_id == owner.id))
        assert account.password_hash.startswith("$argon2id$")
        assert "temporary-test-password" not in account.password_hash
        assert grant.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token != grant.token_hash
        assert (
            grant.csrf_hash == hashlib.sha256(client.headers["X-CSRF-Token"].encode()).hexdigest()
        )
        assert 6.99 * 86400 < (grant.expires_at - grant.created_at).total_seconds() <= 7 * 86400 + 1
    with session_factory.begin() as session:
        grant = session.scalar(select(SessionRow).where(SessionRow.owner_id == owner.id))
        grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get("/api/v1/session").status_code == 401


def test_sign_out_revokes_token_even_if_cookie_is_replayed(client):
    token = client.cookies.get("tr_session")
    assert client.delete("/api/v1/session").status_code == 204
    client.cookies.set("tr_session", token)
    assert client.get("/api/v1/session").status_code == 401


def test_password_reset_invalidates_sessions_and_old_password(client, admin_service, owner):
    admin_service.reset_password(owner.id, "reset-test-password-789")
    assert client.get("/api/v1/session").status_code == 401
    assert (
        client.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-test-password-123"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "reset-test-password-789"},
        ).status_code
        == 200
    )


def test_failed_logins_are_throttled_without_account_disclosure(anonymous, owner):
    anonymous.headers["Origin"] = "http://testserver"
    fake = "missing-" + uuid4().hex
    for name in (owner.username, fake):
        for _ in range(5):
            response = anonymous.post(
                "/api/v1/session", json={"username": name, "password": "wrong-test-password"}
            )
            assert (
                response.status_code == 401 and response.json()["message"] == "Invalid credentials"
            )
        response = anonymous.post(
            "/api/v1/session", json={"username": name, "password": "wrong-test-password"}
        )
        assert response.status_code == 429 and response.json()["code"] == "sign_in_throttled"


def test_throttle_expires_after_five_minutes(anonymous, owner, session_factory):
    from sqlalchemy import select

    from table_review.storage.models import SignInThrottleRow

    anonymous.headers["Origin"] = "http://testserver"
    for _ in range(5):
        anonymous.post(
            "/api/v1/session", json={"username": owner.username, "password": "wrong-test-password"}
        )
    with session_factory.begin() as session:
        rows = session.scalars(select(SignInThrottleRow)).all()
        for row in rows:
            row.window_started_at = datetime.now(UTC) - timedelta(minutes=6)
    assert (
        anonymous.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-test-password-123"},
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "origin", [None, "null", "https://evil.example", "http://testserver.evil.example"]
)
def test_sign_in_requires_exact_origin(anonymous, owner, origin):
    if origin:
        anonymous.headers["Origin"] = origin
    assert (
        anonymous.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-test-password-123"},
        ).status_code
        == 403
    )


def test_authenticated_mutations_require_session_bound_csrf(client, client_b):
    client.headers.pop("X-CSRF-Token")
    assert client.delete("/api/v1/session").status_code == 403
    client.headers["X-CSRF-Token"] = client_b.headers["X-CSRF-Token"]
    assert client.delete("/api/v1/session").status_code == 403
    assert client.get("/api/v1/session").status_code == 200


def test_private_resource_read_hides_foreign_and_missing_documents(
    client, client_b, owner, repository
):
    document = repository.create_document(
        owner.id,
        filename="private.pdf",
        source_sha256="a" * 64,
        byte_count=10,
        page_count=2,
        blob_key=uuid4().hex,
    )
    assert client.get(f"/api/v1/documents/{document.id}").status_code == 200
    foreign = client_b.get(f"/api/v1/documents/{document.id}")
    missing = client_b.get(f"/api/v1/documents/{uuid4()}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["message"] == missing.json()["message"] == "Resource not found"
    assert set(foreign.json()) == {"code", "message", "request_id", "details"}


def test_default_cookie_configuration_is_secure(app, owner):
    from fastapi.testclient import TestClient

    from table_review.app import create_app

    settings = app.state.settings.model_copy(
        update={"secure_cookies": True, "public_origin": "https://testserver"}
    )
    secure_app = create_app(settings)
    with TestClient(secure_app, base_url="https://testserver") as client:
        response = client.post(
            "/api/v1/session",
            headers={"Origin": "https://testserver"},
            json={"username": owner.username, "password": "temporary-test-password-123"},
        )
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(c for c in cookies if c.startswith("tr_session="))
        assert (
            "HttpOnly" in session_cookie
            and "Secure" in session_cookie
            and "SameSite=strict" in session_cookie
        )


def test_validation_never_echoes_password_and_no_public_registration(anonymous, client):
    anonymous.headers["Origin"] = "http://testserver"
    response = anonymous.post(
        "/api/v1/session", json={"username": None, "password": "sensitive-test-value"}
    )
    assert response.status_code == 422
    assert "sensitive-test-value" not in response.text
    assert anonymous.post("/api/v1/accounts", json={}).status_code == 401
    assert client.post("/api/v1/accounts", json={}).status_code == 404


def test_early_rejections_are_private_and_correlated(anonymous):
    response = anonymous.post("/api/v1/session", json={})
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_admin_commands_prompt_for_passwords_and_never_print_them(
    monkeypatch, capsys, session_factory, admin_service
):
    from sqlalchemy import select, text

    from table_review.accounts.admin import main
    from table_review.storage.models import AccountRow

    engine = session_factory.kw["bind"]
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    url = engine.url.set(query={"options": "-csearch_path=" + schema}).render_as_string(
        hide_password=False
    )
    monkeypatch.setenv("TABLE_REVIEW_DATABASE_URL", url)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "operator-test-password-123")
    name = "admin-" + uuid4().hex
    monkeypatch.setattr("sys.argv", ["admin", "create", name])
    main()
    grant = admin_service.sign_in(name, "operator-test-password-123")
    monkeypatch.setattr("sys.argv", ["admin", "reset", name])
    main()
    with pytest.raises(PermissionError):
        admin_service.resolve_session(grant.token)
    monkeypatch.setattr("sys.argv", ["admin", "revoke", name])
    main()
    with session_factory() as session:
        account = session.scalar(select(AccountRow).where(AccountRow.username == name))
        assert account.revoked_at is not None
    assert "operator-test-password" not in capsys.readouterr().out


def test_unexpected_errors_keep_envelope_without_source_or_secrets(app):
    from fastapi.testclient import TestClient

    @app.get("/test-internal-error")
    def fail():
        raise RuntimeError("private report cell and token must not leak")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-internal-error")
    assert response.status_code == 500
    assert set(response.json()) == {"code", "message", "request_id", "details"}
    assert "private report cell" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_creation_validation_and_duplicate_names(admin_service):
    from table_review.accounts.service import InvalidAccount

    name = "user-" + uuid4().hex
    admin_service.create(name, "test-created-password-123")
    with pytest.raises(InvalidAccount):
        admin_service.create(name.upper(), "test-created-password-123")
    with pytest.raises(InvalidAccount):
        admin_service.create("new-" + uuid4().hex, "short")
