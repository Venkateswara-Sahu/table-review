import argparse
import json
import logging
import os
import signal
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from table_review.accounts.types import Actor
from table_review.documents.service import DocumentError
from table_review.extraction.timing import TimingLedger
from table_review.extraction.types import ExtractionRequest, ExtractorConfig
from table_review.jobs.queue import StaleLease
from table_review.jobs.runner import ProcessingFailure, run_operation
from table_review.jobs.types import ProcessingLimits, TaskOutcome


def maintain(queue, now):
    result = queue.deletion.sweep(now)
    if queue.settings.backup_root.is_dir():
        from table_review.lifecycle.backup import BackupError, purge_backups

        try:
            expired = purge_backups(queue.settings, now)
            result = result.model_copy(update={"expired_backups_removed": expired})
        except (BackupError, OSError):
            result = result.model_copy(update={"failure_count": result.failure_count + 1})
    if any(result.model_dump().values()):
        logging.getLogger(__name__).info("lifecycle_cleanup %s", result.model_dump_json())
    return result


def run_once(queue, worker_id: str) -> bool:
    lease = queue.claim(datetime.now(UTC), worker_id)
    if lease is None:
        return False
    started, started_at = time.monotonic(), datetime.now(UTC)
    timing = TimingLedger()
    settings = queue.settings
    remaining = queue.remaining_seconds(lease)
    if remaining <= 0:
        timing.call(
            "publication_operation",
            queue.complete,
            lease,
            TaskOutcome(status="failed", failure_code="timeout"),
        )
        log_task_metric(queue, lease, started, started_at, timing)
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
                timing.call("publication_operation", queue.complete, lease, outcome)
                return True
            source_path = timing.call(
                "source_validation", queue.documents.source_path, actor, task.document_id
            )
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
            result = timing.call(
                "bounded_operation",
                run_operation,
                payload,
                limits,
                work_dir=work_dir,
                cancelled=lambda: not queue.active(lease),
                heartbeat=lambda: queue.heartbeat(lease, datetime.now(UTC)),
            )
            if task.kind == "extract":
                result["manifest"]["worker_timing"] = {
                    **timing.snapshot(),
                    "task_age_at_claim_seconds": max(
                        0, (started_at - task.created_at).total_seconds()
                    ),
                    "task_age_semantics": "includes_prior_attempts_when_retried",
                }
            outcome = TaskOutcome(
                status="succeeded",
                extraction=result if task.kind == "extract" else None,
                result={} if task.kind == "extract" else result,
            )
            timing.call("publication_operation", queue.complete, lease, outcome)
    except (ProcessingFailure, DocumentError) as error:
        code = error.code
        timing.call(
            "publication_operation",
            queue.complete,
            lease,
            TaskOutcome(status="cancelled" if code == "cancelled" else "failed", failure_code=code),
        )
    except StaleLease:
        pass
    except OSError:
        timing.call(
            "publication_operation",
            queue.complete,
            lease,
            TaskOutcome(status="failed", failure_code="storage_failure"),
        )
    except Exception:  # noqa: BLE001 -- isolate task failure from the durable worker loop
        # Never include report strings, filenames or driver exception parameters in outcomes.
        timing.call(
            "publication_operation",
            queue.complete,
            lease,
            TaskOutcome(status="failed", failure_code="infrastructure_interruption"),
        )
    finally:
        queue.acknowledge_stopped(lease)
        log_task_metric(queue, lease, started, started_at, timing)
    return True


def log_task_metric(queue, lease, started, started_at, timing=None):
    from table_review.jobs.models import TaskRow

    try:
        with queue.session_factory() as session:
            row = session.get(TaskRow, lease.task_id)
            if row is None:
                return
            value = {
                "kind": row.kind if row.kind in {"prepare", "extract", "export"} else "unknown",
                "state": row.state
                if row.state in {"queued", "running", "succeeded", "failed", "cancelled"}
                else "unknown",
                "attempt": lease.attempt_no,
                "duration_seconds": round(time.monotonic() - started, 3),
                "task_age_at_claim_seconds": round(
                    max(0, (started_at - row.created_at).total_seconds()), 3
                ),
                "task_age_semantics": "includes_prior_attempts_when_retried",
                "worker_stages": timing.snapshot() if timing is not None else None,
                "publication_timing_semantics": "includes_commit_on_return_and_failed_attempt_work",
            }
        logging.getLogger(__name__).info("task_metric %s", json.dumps(value, sort_keys=True))
    except Exception:  # noqa: BLE001 - telemetry failure must not stop the durable worker
        logging.getLogger(__name__).warning("task_metric_unavailable")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    def shutdown(*args):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    from table_review.app import create_app
    from table_review.settings import Settings

    app = create_app(Settings.from_env())
    from table_review.runtime import WorkerPulse

    try:
        with WorkerPulse(app.state.settings.pulse_path):
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
