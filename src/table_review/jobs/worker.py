import argparse
import logging
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from table_review.accounts.types import Actor
from table_review.documents.service import DocumentError
from table_review.extraction.types import ExtractionRequest, ExtractorConfig
from table_review.jobs.queue import StaleLease
from table_review.jobs.runner import ProcessingFailure, run_operation
from table_review.jobs.types import ProcessingLimits, TaskOutcome


def maintain(queue, now):
    result = queue.deletion.sweep(now)
    if any(result.model_dump().values()):
        logging.getLogger(__name__).info("lifecycle_cleanup %s", result.model_dump_json())
    return result


def run_once(queue, worker_id: str) -> bool:
    lease = queue.claim(datetime.now(UTC), worker_id)
    if lease is None:
        return False
    settings = queue.settings
    remaining = queue.remaining_seconds(lease)
    if remaining <= 0:
        queue.complete(lease, TaskOutcome(status="failed", failure_code="timeout"))
        return True
    limits = ProcessingLimits(
        timeout_seconds=remaining,
        memory_bytes=settings.worker_memory_bytes,
        heartbeat_seconds=settings.heartbeat_seconds,
    )
    # Attempt-scoped private scratch is never a published resource.
    attempt_root = settings.blob_root / ".attempts"
    attempt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"{lease.token.hex}-", dir=attempt_root
        ) as temporary:
            work_dir = Path(temporary)
            with queue.session_factory() as session:
                from table_review.jobs.models import TaskRow

                row = session.get(TaskRow, lease.task_id)
                task = queue.get(row.owner_id, row.id)
            actor = Actor(id=task.owner_id)
            if task.kind in queue.handlers:
                outcome = queue.handlers[task.kind](task, lease, work_dir, limits)
                queue.complete(lease, outcome)
                return True
            source_path = queue.documents.source_path(actor, task.document_id)
            if task.kind == "extract":
                request = ExtractionRequest(
                    source_path=source_path,
                    pages=tuple(task.input["pages"]),
                    region=task.input.get("region"),
                    config=ExtractorConfig(artifacts_path=settings.model_path.resolve()),
                )
                payload = {"kind": "extract", "request": request.model_dump(mode="json")}
            elif task.kind == "prepare":
                payload = {
                    "kind": "prepare",
                    "source_path": str(source_path),
                    "scratch": str(work_dir / "blobs"),
                }
            else:
                raise ProcessingFailure("unsupported_task")
            result = run_operation(
                payload,
                limits,
                work_dir=work_dir,
                cancelled=lambda: not queue.active(lease),
                heartbeat=lambda: queue.heartbeat(lease, datetime.now(UTC)),
            )
            outcome = TaskOutcome(
                status="succeeded",
                extraction=result if task.kind == "extract" else None,
                result={} if task.kind == "extract" else result,
            )
            queue.complete(lease, outcome)
    except (ProcessingFailure, DocumentError) as error:
        code = error.code
        queue.complete(
            lease,
            TaskOutcome(status="cancelled" if code == "cancelled" else "failed", failure_code=code),
        )
    except StaleLease:
        pass
    except OSError:
        queue.complete(lease, TaskOutcome(status="failed", failure_code="storage_failure"))
    except Exception:  # noqa: BLE001 -- isolate task failure from the durable worker loop
        # Never include report strings, filenames or driver exception parameters in outcomes.
        queue.complete(
            lease, TaskOutcome(status="failed", failure_code="infrastructure_interruption")
        )
    finally:
        queue.acknowledge_stopped(lease)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from table_review.app import create_app
    from table_review.settings import Settings

    app = create_app(
        Settings(
            database_url=os.environ["TABLE_REVIEW_DATABASE_URL"],
            blob_root=Path(os.environ.get("TABLE_REVIEW_BLOB_ROOT", "data/blobs")),
            journal_path=Path(os.environ["TABLE_REVIEW_JOURNAL_PATH"])
            if "TABLE_REVIEW_JOURNAL_PATH" in os.environ
            else None,
        )
    )
    try:
        next_maintenance = 0
        while True:
            if time.monotonic() >= next_maintenance:
                # Fail closed if current journal is unavailable; never recreate it here.
                maintain(app.state.queue, datetime.now(UTC))
                next_maintenance = time.monotonic() + 60
            worked = run_once(app.state.queue, f"worker-{os.getpid()}")
            if args.once:
                return
            if not worked:
                time.sleep(1)
    finally:
        app.state.session_factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
