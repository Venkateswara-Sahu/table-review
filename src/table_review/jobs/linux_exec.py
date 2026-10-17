"""Linux process guardian: parent death kills descendants, including escaped sessions."""

import ctypes
import os
import signal
import subprocess
import sys

import psutil


def stop_descendants():
    descendants = psutil.Process().children(recursive=True)
    for child in reversed(descendants):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(descendants, timeout=3)


def parent_died(*args):
    stop_descendants()
    os.killpg(os.getpgrp(), signal.SIGKILL)


def main():
    expected_parent = int(sys.argv[1])
    signal.signal(signal.SIGTERM, parent_died)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError("Could not establish parent-death guard")
    if os.getppid() != expected_parent:
        parent_died()
    try:
        # Prefer the bounded native task as an OOM victim over its queue controller.
        try:
            with open("/proc/self/oom_score_adj", "w") as stream:
                stream.write("500")
        except OSError:
            pass
        command = sys.argv[2:]
        forward_stdin = command[0] == "--forward-stdin"
        if forward_stdin:
            command = command[1:]
        process = subprocess.Popen(
            command, stdin=sys.stdin if forward_stdin else subprocess.DEVNULL
        )
        return process.wait()
    finally:
        stop_descendants()


if __name__ == "__main__":
    raise SystemExit(main())
