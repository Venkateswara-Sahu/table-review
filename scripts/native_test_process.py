"""Set unattended native-error policy before importing application libraries."""

import os

_diagnostic_output = None
_diagnostic_started = None
_fault_stream = None


def prepare_test_process():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetErrorMode.argtypes = []
        kernel.GetErrorMode.restype = wintypes.UINT
        kernel.SetErrorMode.argtypes = [wintypes.UINT]
        kernel.SetErrorMode.restype = wintypes.UINT
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0003)


def record_phase(phase, *, event="started", nodeid=None):
    """Flush each boundary independently so a fatal exit cannot lose the active case."""
    if _diagnostic_output is None:
        return
    import json
    import time

    with (_diagnostic_output / "progress.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "phase": phase,
                    "event": event,
                    "nodeid": nodeid,
                    "elapsed_seconds": time.monotonic() - _diagnostic_started,
                }
            )
            + "\n"
        )


def start_diagnostics(output, *, timeout, deadline=None):
    """Use only the standard library until crash handling and identity are recorded."""
    import faulthandler
    import json
    import platform
    import sys
    import time
    from importlib.metadata import PackageNotFoundError, version
    from pathlib import Path

    global _diagnostic_output, _diagnostic_started, _fault_stream
    _diagnostic_output = Path(output)
    _diagnostic_started = time.monotonic()
    (_diagnostic_output / "progress.jsonl").touch(exist_ok=False)
    _fault_stream = (_diagnostic_output / "fault.log").open("x", encoding="utf-8")
    faulthandler.enable(file=_fault_stream, all_threads=True)
    # Capture one Python stack before the parent's unchanged deadline kills a hung child.
    # Process startup has already consumed part of that deadline.
    remaining = timeout if deadline is None else deadline - time.monotonic()
    faulthandler.dump_traceback_later(max(0.001, remaining * 0.9), file=_fault_stream)
    record_phase("bootstrap")
    packages = {}
    for name in (
        "pytest",
        "pydantic",
        "pydantic-core",
        "pypdfium2",
        "docling",
        "torch",
        "torchvision",
        "onnxruntime",
        "numpy",
    ):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    with (_diagnostic_output / "runtime.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "executable": sys.executable,
                "base_executable": getattr(sys, "_base_executable", sys.executable),
                "python_version": sys.version,
                "system": platform.system(),
                "machine": platform.machine(),
                "pid": os.getpid(),
                "packages": packages,
            },
            stream,
            indent=2,
        )
