"""Verification must preserve evidence when its native test process dies."""

import json
import os
import sys

import psutil
import pytest

from scripts.verify_extraction import run_verification


def test_success_records_output_and_exit_status(tmp_path):
    result = run_verification(
        [sys.executable, "-c", 'print("verified fixture")'], tmp_path, timeout=5
    )
    assert result["status"] == "passed" and result["returncode"] == 0
    assert (tmp_path / "stdout.log").read_text(encoding="utf-8").strip() == "verified fixture"
    assert json.loads((tmp_path / "result.json").read_text()) == result


def test_native_process_exit_is_not_reported_as_a_test_failure_or_success(tmp_path):
    if os.name == "nt":
        code = "import ctypes; ctypes.windll.kernel32.ExitProcess(0xC0000005)"
    else:
        code = "import os,signal; os.kill(os.getpid(),signal.SIGKILL)"
    result = run_verification([sys.executable, "-c", code], tmp_path, timeout=5)
    assert result["status"] == "process_crash"
    assert result["returncode"] != 0
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "process_crash"


def test_pytest_failure_is_retained_without_retry_or_masking(tmp_path):
    result = run_verification(
        [sys.executable, "-c", 'print("tests failed"); raise SystemExit(1)'], tmp_path, timeout=5
    )
    assert result["status"] == "failed" and result["returncode"] == 1


def test_timeout_records_the_active_case_and_kills_descendants(tmp_path):
    child_pid = tmp_path / "child.pid"
    code = """import pathlib,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
print('tests/fixture.py::test_waiting',flush=True)
time.sleep(60)
"""
    result = run_verification(
        [sys.executable, "-u", "-c", code, str(child_pid)], tmp_path, timeout=2
    )
    assert result["status"] == "timeout"
    assert result["last_test"] == "tests/fixture.py::test_waiting"
    pid = int(child_pid.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "timeout"


def test_existing_evidence_directory_is_not_overwritten(tmp_path):
    (tmp_path / "stdout.log").write_text("existing evidence")
    with pytest.raises(FileExistsError):
        run_verification([sys.executable, "-c", 'print("overwrite")'], tmp_path, timeout=5)
    assert (tmp_path / "stdout.log").read_text() == "existing evidence"


@pytest.mark.skipif(os.name != "nt", reason="Windows native error UI policy")
def test_windows_verification_child_disables_native_crash_dialogs(tmp_path):
    code = (
        "import ctypes; from scripts.verify_extraction import prepare_test_process; "
        "prepare_test_process(); assert ctypes.windll.kernel32.GetErrorMode() & 3 == 3"
    )
    result = run_verification([sys.executable, "-c", code], tmp_path, timeout=5)
    assert result["status"] == "passed"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_deadline_must_be_positive_and_finite_before_launch(tmp_path, timeout):
    with pytest.raises(ValueError, match="positive and finite"):
        run_verification([sys.executable, "-c", "pass"], tmp_path / "evidence", timeout=timeout)
    assert not (tmp_path / "evidence").exists()


def test_warning_summary_does_not_replace_the_last_executed_test(tmp_path):
    text = (
        "tests/fixture.py::test_last PASSED [100%]\n"
        "================ warnings summary ================\n"
        "tests/fixture.py::test_earlier\n"
    )
    result = run_verification(
        [sys.executable, "-c", "print(" + repr(text) + ")"], tmp_path, timeout=5
    )
    assert result["last_test"] == "tests/fixture.py::test_last"
