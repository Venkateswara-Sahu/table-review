"""Independent queue worker process used only by the recovery test."""

import sys
from pathlib import Path

from table_review.app import create_app
from table_review.extraction.types import ExtractedTable, ExtractionResult
from table_review.jobs.runner import run_child
from table_review.jobs.types import TaskOutcome
from table_review.jobs.worker import run_once
from table_review.settings import Settings
from tests.fixtures.tables import simple_table

if __name__ == "__main__":
    config, pidfile = map(Path, sys.argv[1:3])
    app = create_app(Settings.model_validate_json(config.read_text()))
    queue = app.state.queue

    def handler(task, lease, work_dir, limits):
        if len(sys.argv) == 3:
            run_child(
                [sys.executable, "-m", "tests.fixtures.slow_child", str(pidfile)],
                limits,
                work_dir=work_dir,
                cancelled=lambda: not queue.active(lease),
            )
        doc = queue.repository.get_document(task.owner_id, task.document_id)
        result = ExtractionResult(
            tables=(
                ExtractedTable(
                    id="recovery-fixture",
                    page_no=2,
                    region=None,
                    content=simple_table(),
                    source_tokens=(),
                ),
            ),
            page_info=(),
            warnings=(),
            manifest={
                "source_sha256": doc.source_sha256,
                "fixture": "worker-recovery-not-model-evaluation",
            },
        )
        return TaskOutcome(status="succeeded", extraction=result.model_dump(mode="json"))

    queue.handlers["extract"] = handler
    try:
        assert run_once(queue, "recovery-test-worker")
    finally:
        app.state.session_factory.kw["bind"].dispose()
