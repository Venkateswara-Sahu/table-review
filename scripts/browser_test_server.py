"""Real disposable PostgreSQL/API/worker fixture for browser tests only."""

import io
import json
import os
import sys
import threading
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from table_review.accounts.types import Actor
from table_review.app import create_app
from table_review.documents.types import SourceReference
from table_review.jobs.worker import run_once
from table_review.settings import Settings
from table_review.storage.db import create_session_factory
from table_review.tables.types import CellContent, TableContent
from tests.fixtures.pdf_factory import blank_report, rotated_cropped_report, two_page_report


def main():
    import uvicorn

    url = (
        os.environ.get("TABLE_REVIEW_TEST_DATABASE_URL")
        or json.loads((ROOT / ".cache/test-db.json").read_text())["url"]
    )
    schema = "test_" + uuid4().hex
    admin = create_session_factory(url).kw["bind"]
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    factory = create_session_factory(url, schema=schema)
    with factory.kw["bind"].begin() as connection:
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    fixtures = ROOT / ".cache/browser-fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    blob_root = fixtures / schema
    app = create_app(
        Settings(
            database_url=url,
            database_schema=schema,
            blob_root=blob_root,
            journal_path=fixtures / (schema + "-lifecycle") / "journal.sqlite3",
            public_origin="http://127.0.0.1:5173",
            secure_cookies=False,
            initialize_journal=True,
            model_path=ROOT / "models",
        )
    )
    if os.environ.get("TABLE_REVIEW_BROWSER_FIXTURE_MODE") == "contract":
        from scripts.browser_contract_fixture import contract_extractor

        app.state.queue.handlers["extract"] = contract_extractor(app.state.queue)

    (fixtures / "active.json").write_text(
        json.dumps({"pid": os.getpid(), "schema": schema}), encoding="utf-8"
    )
    owners = [
        app.state.accounts.create(username, "browser-fixture-password-123")
        for username in ("browser-a", "browser-b")
    ]
    actor = Actor(id=owners[0].id)
    for filename, creator, extract_pages in (
        ("report.pdf", two_page_report, [2]),
        ("rotated.pdf", rotated_cropped_report, [1]),
        ("blank.pdf", blank_report, None),
        ("long-report.pdf", lambda path: blank_report(path, pages=12), None),
        ("fallback.pdf", two_page_report, None),
        ("review.pdf", two_page_report, None),
    ):
        path = creator(fixtures / filename)
        doc = app.state.documents.upload(
            actor, io.BytesIO(path.read_bytes()), filename, uuid4().hex
        )
        assert run_once(app.state.queue, "browser-fixture-preparation")
        if extract_pages:
            task = app.state.queue.enqueue(
                actor.id, doc.id, "extract", {"pages": extract_pages}, uuid4().hex
            )
            assert run_once(app.state.queue, "browser-fixture-extraction")
            result = app.state.queue.get(actor.id, task.id)
            assert result.state == "succeeded", result.failure_code
        if filename == "fallback.pdf":
            content = TableContent(
                rows=1,
                columns=1,
                source_reference=SourceReference(page=1, precision="page", origin="user"),
                cells=(
                    CellContent(
                        id=str(uuid4()),
                        row=0,
                        col=0,
                        raw_text="",
                        text="manual value",
                        reference=SourceReference(page=1, precision="page", origin="user"),
                    ),
                ),
            )
            app.state.repository.create_table(
                actor.id,
                doc.id,
                content,
                origin="manual",
                extraction_manifest={"fixture": "coarse-reference"},
            )

    @app.get("/api/v1/test-ready", include_in_schema=False)
    def ready():
        return {"ready": True}

    stopping = threading.Event()

    def worker():
        while not stopping.is_set():
            if not run_once(app.state.queue, "browser-test-worker"):
                stopping.wait(0.2)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning", access_log=False)
    finally:
        stopping.set()
        thread.join(timeout=5)
        factory.kw["bind"].dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    main()
