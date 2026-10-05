import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

from table_review.extraction.types import ExtractionResult
from table_review.jobs.types import ProcessingLimits


class ProcessingFailure(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def code_identity(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for source in sorted(root.rglob("*.py")):
        name = source.relative_to(root).as_posix().encode("utf-8")
        content = source.read_bytes()
        digest.update(
            len(name).to_bytes(8, "big") + name + len(content).to_bytes(8, "big") + content
        )
    return {
        "code_revision": digest.hexdigest(),
        "code_revision_kind": "installed_python_source_sha256",
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
    }


class _WindowsJob:
    """Kernel closes the job on worker death, killing all assigned descendants."""

    def __init__(self, process):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("Could not establish worker process isolation")
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            kernel.CloseHandle(handle)
            raise OSError("Could not establish worker process isolation")
        if not kernel.AssignProcessToJobObject(handle, int(process._handle)):
            kernel.CloseHandle(handle)
            raise OSError("Could not establish worker process isolation")
        self.kernel, self.handle = kernel, handle

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _terminate(process):
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    psutil.wait_procs(children, timeout=5)


def run_child(command, limits, *, work_dir, cancelled=lambda: False, heartbeat=lambda: None):
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Do not copy DB credentials, account cookies or arbitrary model service tokens.
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL"}
    }
    env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HOME": str(work_dir / "hf"),
            "HF_TOKEN_PATH": str(work_dir / "hf/token"),
            "TORCH_HOME": str(work_dir / "torch"),
            "TMP": str(work_dir),
            "TEMP": str(work_dir),
            "TMPDIR": str(work_dir),
            "OMP_NUM_THREADS": "4",
            "MKL_NUM_THREADS": "4",
        }
    )
    # pathlib.home() is used at import time by Docling, even with local models.
    if os.name == "nt":
        env["USERPROFILE"] = str(work_dir)
    elif "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    env["DOCLING_CACHE_DIR"] = str(work_dir / "docling")
    process = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    )
    job = None
    started, next_heartbeat, peak = time.monotonic(), time.monotonic(), 0
    try:
        job = _WindowsJob(process)
        while process.poll() is None:
            now = time.monotonic()
            if cancelled():
                raise ProcessingFailure("cancelled")
            if now - started >= limits.timeout_seconds:
                raise ProcessingFailure("timeout")
            if now >= next_heartbeat:
                heartbeat()
                next_heartbeat = now + limits.heartbeat_seconds
            try:
                parent = psutil.Process(process.pid)
                rss = parent.memory_info().rss
                for child in parent.children(recursive=True):
                    try:
                        rss += child.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                peak = max(peak, rss)
                if rss > limits.memory_bytes:
                    raise ProcessingFailure("resource_exhaustion")
            except psutil.NoSuchProcess:
                pass
            time.sleep(limits.poll_seconds)
        if cancelled():
            raise ProcessingFailure("cancelled")
        if process.returncode:
            raise ProcessingFailure("infrastructure_interruption")
        return {
            "elapsed_seconds": time.monotonic() - started,
            "sampled_peak_rss_bytes": peak,
            "rss_sample_interval_seconds": limits.poll_seconds,
            "hard_memory_enforcement": False,
        }
    finally:
        _terminate(process)
        if job:
            job.close()


def run_operation(payload, limits, *, work_dir, cancelled=lambda: False, heartbeat=lambda: None):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    request_path, result_path = work_dir / "request.json", work_dir / "result.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = run_child(
        [sys.executable, "-m", "table_review.jobs.child", str(request_path), str(result_path)],
        limits,
        work_dir=work_dir,
        cancelled=cancelled,
        heartbeat=heartbeat,
    )
    if not result_path.is_file():
        raise ProcessingFailure("infrastructure_interruption")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if "failure_code" in result:
        raise ProcessingFailure(result["failure_code"])
    if payload["kind"] == "extract":
        result["manifest"]["bounded_runner"] = metrics
        result["manifest"].update(code_identity())
    return result


def run_bounded(request, limits: ProcessingLimits) -> ExtractionResult:
    with tempfile.TemporaryDirectory(prefix="table-extraction-") as temporary:
        result = run_operation(
            {"kind": "extract", "request": request.model_dump(mode="json")},
            limits,
            work_dir=Path(temporary),
        )
        return ExtractionResult.model_validate(result)
