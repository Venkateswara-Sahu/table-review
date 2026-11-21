"""Private, bounded controller for a Linux PDF library template."""

import json
import os
import queue
import select
import subprocess
import sys
import threading
import time

import psutil

from table_review.jobs.runner import ProcessingFailure, _terminate, _WindowsJob, child_environment


class PreviewHost:
    def __init__(self, settings, scratch):
        self.settings, self.scratch = settings, scratch
        self.process = None
        self._buffer = b""

    def command(self):
        return [
            sys.executable,
            "-m",
            "table_review.documents.pdf_host",
            str(self.settings.pdf_memory_bytes),
        ]

    def _receive(self, deadline):
        while self.process.poll() is None:
            if time.monotonic() >= deadline:
                raise ProcessingFailure("timeout")
            try:
                root = psutil.Process(self.process.pid)
                family = [root, *root.children(recursive=True)]
                memory = sum(member.memory_info().rss for member in family)
                if memory > self.settings.pdf_memory_bytes:
                    raise ProcessingFailure("resource_exhaustion")
            except psutil.NoSuchProcess:
                continue
            ready, _, _ = select.select([self.process.stdout], [], [], 0.005)
            if ready:
                chunk = os.read(self.process.stdout.fileno(), 128)
                if not chunk:
                    break
                self._buffer += chunk
                if len(self._buffer) > 256:
                    raise ProcessingFailure("infrastructure_interruption")
                if b"\n" in self._buffer:
                    message, self._buffer = self._buffer.split(b"\n", 1)
                    return message.decode("ascii").strip()
        raise ProcessingFailure("infrastructure_interruption")

    def warmup(self, deadline=None):
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        # The existing guardian kills descendants when the API dies, even if
        # a native child creates its own process session.
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "table_review.jobs.linux_exec",
                str(os.getpid()),
                "--forward-stdin",
                *self.command(),
            ],
            env=child_environment(self.scratch),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        try:
            if (
                self._receive(
                    deadline
                    if deadline is not None
                    else time.monotonic() + self.settings.pdf_timeout_seconds
                )
                != "ready"
            ):
                raise ProcessingFailure("infrastructure_interruption")
        except BaseException:
            self.close()
            raise

    def run(self, request, destination, timeout):
        import json

        try:
            deadline = time.monotonic() + timeout
            self.warmup(deadline=deadline)
            self.process.stdin.write(json.dumps([str(request), str(destination)]) + "\n")
            self.process.stdin.flush()
            if self._receive(deadline) != "ok":
                raise ProcessingFailure("infrastructure_interruption")
        except (OSError, ValueError) as error:
            self.close()
            raise ProcessingFailure("infrastructure_interruption") from error
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process is not None:
            try:
                _terminate(self.process)
            finally:
                self.process.stdin.close()
                self.process.stdout.close()
                self.process = None
                self._buffer = b""


def _read_lines(stream, out_queue: queue.Queue):
    try:
        while raw := stream.readline():
            out_queue.put(("line", raw.decode("ascii", errors="replace").strip()))
    except OSError:
        pass
    finally:
        out_queue.put(("eof", ""))


class LocalPreviewHost:
    """Bounded warm PDF preview worker for platforms without fork (Windows/macOS)."""

    def __init__(self, settings, scratch):
        self.settings, self.scratch = settings, scratch
        self.process: subprocess.Popen | None = None
        self._job: _WindowsJob | None = None
        self._lines: queue.Queue | None = None
        self._reader: threading.Thread | None = None

    def command(self):
        return [sys.executable, "-m", "table_review.documents.pdf_host_local"]

    def _receive(self, deadline):
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise ProcessingFailure("timeout")
            if self.process is None:
                raise ProcessingFailure("infrastructure_interruption")
            try:
                root = psutil.Process(self.process.pid)
                family = [root, *root.children(recursive=True)]
                memory = sum(member.memory_info().rss for member in family)
                if memory > self.settings.pdf_memory_bytes:
                    raise ProcessingFailure("resource_exhaustion")
            except psutil.NoSuchProcess:
                pass
            wait = min(0.02, max(0.002, deadline - now))
            try:
                kind, value = self._lines.get(timeout=wait)
            except queue.Empty:
                if self.process.poll() is not None:
                    raise ProcessingFailure("infrastructure_interruption") from None
                continue
            if kind == "line":
                return value
            raise ProcessingFailure("infrastructure_interruption")

    def warmup(self, deadline=None):
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        self.process = subprocess.Popen(
            self.command(),
            env=child_environment(self.scratch),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=os.name != "nt",
        )
        self._lines = queue.Queue()
        self._reader = threading.Thread(
            target=_read_lines, args=(self.process.stdout, self._lines), daemon=True
        )
        self._reader.start()
        try:
            self._job = _WindowsJob(self.process)
            ready_deadline = (
                deadline
                if deadline is not None
                else time.monotonic() + self.settings.pdf_timeout_seconds
            )
            if self._receive(ready_deadline) != "ready":
                raise ProcessingFailure("infrastructure_interruption")
        except BaseException:
            self.close()
            raise

    def run(self, request, destination, timeout):
        try:
            deadline = time.monotonic() + timeout
            self.warmup(deadline=deadline)
            payload = (json.dumps([str(request), str(destination)]) + "\n").encode("utf-8")
            self.process.stdin.write(payload)
            self.process.stdin.flush()
            if self._receive(deadline) != "ok":
                raise ProcessingFailure("infrastructure_interruption")
        except (OSError, ValueError) as error:
            self.close()
            raise ProcessingFailure("infrastructure_interruption") from error
        except BaseException:
            self.close()
            raise

    def close(self):
        process = self.process
        job = self._job
        self.process = None
        self._job = None
        self._lines = None
        self._reader = None
        if process is not None:
            try:
                _terminate(process)
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
        if job is not None:
            job.close()
