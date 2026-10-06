"""Operator bootstrap and retryable cleanup. Never recreates a missing current journal implicitly."""

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from table_review.lifecycle.journal import LifecycleJournal
from table_review.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("initialize", "verify", "sweep"))
    args = parser.parse_args()
    settings = Settings(
        database_url=os.environ.get("TABLE_REVIEW_DATABASE_URL", ""),
        blob_root=Path(os.environ.get("TABLE_REVIEW_BLOB_ROOT", "data/blobs")),
        journal_path=Path(os.environ["TABLE_REVIEW_JOURNAL_PATH"])
        if "TABLE_REVIEW_JOURNAL_PATH" in os.environ
        else None,
    )
    if args.action in ("initialize", "verify"):
        journal = LifecycleJournal(
            settings.lifecycle_journal_path, create=args.action == "initialize"
        )
        journal.verify()
        print("Lifecycle journal verified")
        return
    from table_review.app import create_app

    app = create_app(settings)
    try:
        print(app.state.deletion.sweep(datetime.now(UTC)).model_dump_json())
    finally:
        app.state.session_factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
