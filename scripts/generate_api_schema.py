"""Generate public contracts from the same configured application routers."""

import json
import tempfile
from pathlib import Path

from table_review.app import create_app
from table_review.settings import Settings


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="table-review-openapi-") as temporary:
        app = create_app(
            Settings(
                database_url="postgresql+psycopg://unused:unused@127.0.0.1/unused",
                blob_root=Path(temporary) / "blobs",
                initialize_journal=True,
            )
        )
        try:
            target = root / "docs/api/openapi.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        finally:
            app.state.session_factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
