import json
import os
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture(scope="session")
def session_factory():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from table_review.storage.db import create_session_factory

    url = os.getenv("TABLE_REVIEW_TEST_DATABASE_URL")
    if not url:
        url = json.loads(Path(".cache/test-db.json").read_text())["url"]
    factory = create_session_factory(url)
    engine = factory.kw["bind"]
    assert engine.dialect.name == "postgresql"
    # A random dedicated schema prevents tests from dropping the application's saved work.
    schema = "test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    factory = create_session_factory(url, schema=schema)
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield factory
    engine.dispose()
    admin = create_session_factory(url).kw["bind"]
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def owner_ids():
    return uuid4(), uuid4()


@pytest.fixture
def repository(session_factory):
    from table_review.storage.repository import Repository

    return Repository(session_factory)


@pytest.fixture
def table_record(repository, owner_ids):
    from tests.fixtures.tables import simple_table

    document = repository.create_document(
        owner_ids[0],
        filename="private-report.pdf",
        source_sha256="a" * 64,
        byte_count=10,
        page_count=2,
        blob_key=uuid4().hex,
    )
    return repository.create_table(
        owner_ids[0], document.id, simple_table(), extraction_manifest={"device": "cpu"}
    )
