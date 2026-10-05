"""Stop only this project's test servers; remove only their disposable schema."""

import json
import os
import re
from pathlib import Path

import psutil
from sqlalchemy import text

from table_review.storage.db import create_session_factory

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SERVER = ROOT / "scripts/browser_test_server.py"
EXPECTED_VITE = ROOT / "web/node_modules/vite/bin/vite.js"


def matches(process, expected):
    try:
        return any(Path(arg).resolve() == expected.resolve() for arg in process.cmdline()[1:])
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def main():
    state_path = ROOT / ".cache/browser-fixtures/active.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    for process in psutil.process_iter():
        if process.pid == os.getpid():
            continue
        if matches(process, EXPECTED_SERVER) or matches(process, EXPECTED_VITE):
            try:
                descendants = process.children(recursive=True)
                for child in reversed(descendants):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                psutil.wait_procs(descendants + [process], timeout=5)
            except psutil.NoSuchProcess:
                pass
    if state:
        schema = state["schema"]
        if not re.fullmatch(r"test_[a-f0-9]{32}", schema):
            raise ValueError("Invalid disposable schema")
        url = (
            os.environ.get("TABLE_REVIEW_TEST_DATABASE_URL")
            or json.loads((ROOT / ".cache/test-db.json").read_text())["url"]
        )
        engine = create_session_factory(url).kw["bind"]
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            state_path.unlink(missing_ok=True)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
