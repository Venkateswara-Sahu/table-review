"""Bounded local extraction verification with durable private crash evidence.

This trusted operator command retains the pytest environment for the disposable
DB fixtures. It is not the credential-scrubbed runner for untrusted PDF jobs.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.native_test_process import prepare_test_process
from table_review.jobs.runner import _terminate, _WindowsJob

__all__ = ["prepare_test_process", "run_verification"]


def run_verification(command, output, *, timeout):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Verification timeout must be positive and finite")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = {
        name: output / name for name in ("stdout.log", "stderr.log", "result.json", "pytest.xml")
    }
    if any(path.exists() for path in files.values()):
        raise FileExistsError("Use a new directory to preserve prior verification evidence")
    started = time.monotonic()
    status, process, job, returncode = "interrupted", None, None, None
    try:
        with files["stdout.log"].open("xb") as stdout, files["stderr.log"].open("xb") as stderr:
            process = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
            )
            job = _WindowsJob(process)
            try:
                returncode = process.wait(timeout=timeout)
                status = (
                    "passed"
                    if returncode == 0
                    else "process_crash"
                    if returncode < 0 or (os.name == "nt" and returncode & 0x80000000)
                    else "failed"
                )
            except subprocess.TimeoutExpired:
                status = "timeout"
    finally:
        if process is not None:
            _terminate(process)
            returncode = process.returncode
        if job is not None:
            job.close()
        lines = files["stdout.log"].read_text(encoding="utf-8", errors="replace").splitlines()
        tests = []
        for line in lines:
            if line.startswith("=") and any(
                section in line
                for section in (
                    "warnings summary",
                    "FAILURES",
                    "ERRORS",
                    "short test summary",
                )
            ):
                break
            if match := re.match(r"^(tests[/\\]\S+\.py::\S+)", line):
                tests.append(match.group(1))
        result = {
            "schema": "extraction_test_verification_v1",
            "status": status,
            "returncode": returncode,
            "elapsed_seconds": time.monotonic() - started,
            "timeout_seconds": timeout,
            "last_test": tests[-1] if tests else None,
        }
        with files["result.json"].open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    output = (
        args.output
        or Path(".cache")
        / (
            "verify-extraction-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            + "-"
            + uuid4().hex[:8]
        )
    ).resolve()
    bootstrap = (
        "import faulthandler,runpy,sys; from scripts.native_test_process import prepare_test_process; "
        "prepare_test_process(); faulthandler.enable(); "
        "sys.argv=['pytest',*sys.argv[1:]]; runpy.run_module('pytest',run_name='__main__')"
    )
    command = [
        sys.executable,
        "-X",
        "faulthandler",
        "-u",
        "-c",
        bootstrap,
        "tests/domain",
        "tests/integration",
        "tests/acceptance",
        "-m",
        "extraction and not packaged",
        "-vv",
        "--tb=line",
        "--color=no",
        "-p",
        "no:cacheprovider",
        "--basetemp=" + str(output / "scratch"),
        "--junitxml=" + str(output / "pytest.xml"),
    ]
    result = run_verification(command, output, timeout=args.timeout)
    print(json.dumps({**result, "evidence_directory": str(output)}))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
