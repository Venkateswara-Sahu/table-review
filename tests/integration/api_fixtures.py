from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(session_factory, tmp_path: Path):
    from sqlalchemy import text

    from table_review.app import create_app
    from table_review.settings import Settings

    engine = session_factory.kw["bind"]
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    settings = Settings(
        database_url=engine.url.render_as_string(hide_password=False),
        database_schema=schema,
        blob_root=tmp_path / "blobs",
        public_origin="http://testserver",
        secure_cookies=False,
    )
    return create_app(settings)


@pytest.fixture
def admin_service(app):
    return app.state.accounts


@pytest.fixture
def owner(admin_service):
    from uuid import uuid4

    return admin_service.create("user-" + uuid4().hex, "temporary-test-password-123")


@pytest.fixture
def anonymous(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client(app, owner):
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        response = client.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-test-password-123"},
        )
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield client


@pytest.fixture
def client_b(app, admin_service):
    from uuid import uuid4

    owner = admin_service.create("user-" + uuid4().hex, "temporary-other-password-456")
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        response = client.post(
            "/api/v1/session",
            json={"username": owner.username, "password": "temporary-other-password-456"},
        )
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield client
