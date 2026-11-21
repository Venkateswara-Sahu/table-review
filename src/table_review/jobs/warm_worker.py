"""Persistent warm extraction worker and bounded controller across Windows and Linux."""

import collections
import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

from table_review.jobs.runner import (
    ProcessingFailure,
    _drain_stderr,
    _terminate,
    _WindowsJob,
    attach_extract_metadata,
    child_environment,
    memory_contract,
    run_operation,
)


def _read_stdout(stream, event_queue: queue.Queue):
    try:
        while raw_line := stream.readline():
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                event_queue.put(("msg", json.loads(text)))
            except json.JSONDecodeError:
                event_queue.put(("proto_error", text[:120]))
                return
    except OSError:
        pass
    finally:
        event_queue.put(("eof", None))


def _update_worker_scratch(work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    work_str = str(work_dir)
    os.environ["TMP"] = work_str
    os.environ["TEMP"] = work_str
    os.environ["TMPDIR"] = work_str
    tempfile.tempdir = work_str
    os.environ["HF_HOME"] = str(work_dir / "hf")
    os.environ["TORCH_HOME"] = str(work_dir / "torch")
    os.environ["DOCLING_CACHE_DIR"] = str(work_dir / "docling")
    if os.name == "nt":
        os.environ["USERPROFILE"] = work_str


def main():
    proto_fd = os.dup(sys.stdout.fileno())
    proto_out = os.fdopen(proto_fd, "w", encoding="utf-8", buffering=1)
    # Keep stray C/library writes on stdout from corrupting the line-delimited protocol.
    os.dup2(sys.stderr.fileno(), 1)
    sys.stdout = sys.stderr

    from table_review.extraction.docling_adapter import DoclingExtractor
    from table_review.jobs.child import execute_file

    extractor = DoclingExtractor(warm_model_cache=True)
    proto_out.write(json.dumps({"event": "ready"}) + "\n")
    proto_out.flush()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            work_dir = Path(cmd["work_dir"])
            _update_worker_scratch(work_dir)
            execute_file(
                Path(cmd["request_path"]),
                Path(cmd["result_path"]),
                docling_extractor=extractor,
            )
            proto_out.write(json.dumps({"event": "done"}) + "\n")
            proto_out.flush()
        except Exception as error:  # noqa: BLE001 - isolate fatal protocol errors cleanly
            proto_out.write(
                json.dumps({"event": "done", "error_type": type(error).__name__}) + "\n"
            )
            proto_out.flush()


class WarmChildHost:
    """Keeps one bounded extraction subprocess warm across tasks and recycles cleanly on faults."""

    def __init__(
        self,
        scratch: Path,
        *,
        recycle_rss_ratio: float = 0.85,
        command: list[str] | None = None,
    ):
        self.scratch = Path(scratch).resolve()
        self.recycle_rss_ratio = recycle_rss_ratio
        self._command = command
        self._process: subprocess.Popen | None = None
        self._job: _WindowsJob | None = None
        self._events: queue.Queue | None = None
        self._stderr_buf: collections.deque | None = None
        self._threads: list[threading.Thread] = []

    @property
    def pid(self) -> int | None:
        if self._process is not None and self._process.poll() is None:
            return self._process.pid
        return None

    def _sample_rss(self) -> int:
        if self._process is None:
            return 0
        try:
            parent = psutil.Process(self._process.pid)
            rss = parent.memory_info().rss
            for child in parent.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            return rss
        except psutil.NoSuchProcess:
            return 0

    def _log_crash(self, failure_code: str):
        tail = ""
        if self._stderr_buf:
            lines = bytes(self._stderr_buf).decode("utf-8", errors="replace").splitlines()[-5:]
            tail = " | ".join(line.strip()[:120] for line in lines if line.strip())[:300]
        returncode = self._process.poll() if self._process is not None else None
        logging.getLogger(__name__).warning(
            "warm_worker_terminated failure_code=%s returncode=%s tail=%s",
            failure_code,
            returncode,
            tail,
        )

    def _wait_for_event(
        self,
        expected: str,
        limits,
        deadline: float,
        cancelled,
        heartbeat,
        next_heartbeat: float,
    ) -> tuple[int, float]:
        peak = 0
        while True:
            if cancelled():
                raise ProcessingFailure("cancelled")
            now = time.monotonic()
            if now >= deadline:
                raise ProcessingFailure("timeout")
            if now >= next_heartbeat:
                heartbeat()
                next_heartbeat = now + limits.heartbeat_seconds

            rss = self._sample_rss()
            if rss:
                peak = max(peak, rss)
                if rss > limits.memory_bytes:
                    raise ProcessingFailure("resource_exhaustion")

            wait_time = min(limits.poll_seconds, max(0.005, deadline - now))
            try:
                kind, payload = self._events.get(timeout=wait_time)
            except queue.Empty:
                if self._process is not None and self._process.poll() is not None:
                    self._log_crash("infrastructure_interruption")
                    raise ProcessingFailure("infrastructure_interruption") from None
                continue

            if kind == "msg" and isinstance(payload, dict) and payload.get("event") == expected:
                return peak, next_heartbeat
            if kind in ("eof", "proto_error"):
                self._log_crash("infrastructure_interruption")
                raise ProcessingFailure("infrastructure_interruption")

    def _ensure_started(self, limits, deadline: float, cancelled, heartbeat, next_heartbeat: float):
        if self._process is not None and self._process.poll() is None:
            return 0, next_heartbeat
        self.close()
        self.scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = child_environment(self.scratch)
        cmd = list(self._command or [sys.executable, "-m", "table_review.jobs.warm_worker"])
        if sys.platform == "linux" and self._command is None:
            cmd = [
                sys.executable,
                "-m",
                "table_review.jobs.linux_exec",
                str(os.getpid()),
                "--forward-stdin",
                *cmd,
            ]
        process = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=os.name != "nt",
        )
        self._process = process
        self._events = queue.Queue()
        self._stderr_buf = collections.deque(maxlen=4096)
        stdout_thread = threading.Thread(
            target=_read_stdout, args=(process.stdout, self._events), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(process.stderr, self._stderr_buf), daemon=True
        )
        self._threads = [stdout_thread, stderr_thread]
        stdout_thread.start()
        stderr_thread.start()
        try:
            self._job = _WindowsJob(process)
            return self._wait_for_event("ready", limits, deadline, cancelled, heartbeat, next_heartbeat)
        except BaseException:
            self.close()
            raise

    def run(
        self,
        payload,
        limits,
        *,
        work_dir,
        cancelled=lambda: False,
        heartbeat=lambda: None,
        extra_env: dict[str, str] | None = None,
    ):
        if extra_env:
            return run_operation(
                payload,
                limits,
                work_dir=work_dir,
                cancelled=cancelled,
                heartbeat=heartbeat,
                extra_env=extra_env,
            )

        work_dir = Path(work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        request_path, result_path = work_dir / "request.json", work_dir / "result.json"
        request_path.write_text(json.dumps(payload), encoding="utf-8")
        result_path.unlink(missing_ok=True)

        started = time.monotonic()
        deadline = started + limits.timeout_seconds
        next_heartbeat = started
        try:
            start_peak, next_heartbeat = self._ensure_started(
                limits, deadline, cancelled, heartbeat, next_heartbeat
            )
            message = (
                json.dumps(
                    {
                        "request_path": str(request_path),
                        "result_path": str(result_path),
                        "work_dir": str(work_dir),
                    }
                ).encode("utf-8")
                + b"\n"
            )
            self._process.stdin.write(message)
            self._process.stdin.flush()
            run_peak, _ = self._wait_for_event(
                "done", limits, deadline, cancelled, heartbeat, next_heartbeat
            )
            peak = max(start_peak, run_peak)
        except ProcessingFailure:
            self.close()
            raise
        except OSError:
            self._log_crash("infrastructure_interruption")
            self.close()
            raise ProcessingFailure("infrastructure_interruption") from None
        except BaseException:
            self.close()
            raise

        if not result_path.is_file():
            self.close()
            raise ProcessingFailure("infrastructure_interruption")

        result = json.loads(result_path.read_text(encoding="utf-8"))
        if "failure_code" in result:
            if "error_type" in result:
                logging.getLogger(__name__).warning(
                    "warm_worker_operation_failed failure_code=%s error_type=%s",
                    result["failure_code"],
                    result["error_type"],
                )
            if peak > int(limits.memory_bytes * self.recycle_rss_ratio):
                self.close()
            raise ProcessingFailure(result["failure_code"])

        if payload["kind"] == "extract":
            metrics = {
                "elapsed_seconds": time.monotonic() - started,
                "sampled_peak_rss_bytes": peak,
                "rss_sample_interval_seconds": limits.poll_seconds,
                "warm_worker": True,
                **memory_contract(),
            }
            attach_extract_metadata(result, metrics)

        if peak > int(limits.memory_bytes * self.recycle_rss_ratio):
            self.close()
        return result

    def close(self):
        process = self._process
        job = self._job
        threads = list(self._threads)
        self._process = None
        self._job = None
        self._events = None
        self._stderr_buf = None
        self._threads = []
        if process is not None:
            if process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                _terminate(process)
            except Exception:  # noqa: BLE001, S110 - best-effort cleanup on shutdown
                pass
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except OSError:
                        pass
        for thread in threads:
            thread.join(timeout=1)
        if job is not None:
            try:
                job.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
