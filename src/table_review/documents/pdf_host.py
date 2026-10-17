"""Linux-only trusted template; each PDF operation runs in a fresh child."""

import ctypes
import json
import os
import signal
import sys
from pathlib import Path

from PIL import Image

from table_review.documents.pdf_child import apply_memory_limit, run_request


def main():
    # Warm trusted decoding/encoding libraries before any PDF bytes are read.
    Image.preinit()
    apply_memory_limit(int(sys.argv[1]))
    print("ready", flush=True)
    for line in sys.stdin:
        request, destination = map(Path, json.loads(line))
        parent = os.getpid()
        child = os.fork()
        if child == 0:
            try:
                os.setsid()
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
                    os._exit(1)
                # Native code cannot write into or read the template protocol.
                with open(os.devnull, "r+b", buffering=0) as silent:
                    for descriptor in (0, 1, 2):
                        os.dup2(silent.fileno(), descriptor)
                # No PDF state survives this process; temporary files stay in
                # the current operation's private, bounded scratch directory.
                os.environ.update({key: str(request.parent) for key in ("TMP", "TEMP", "TMPDIR")})
                import tempfile

                tempfile.tempdir = str(request.parent)
                run_request(request, destination)
                os._exit(0)
            except BaseException:  # noqa: BLE001 - no source-bearing error escapes the child
                os._exit(1)
        _, status = os.waitpid(child, 0)
        print("ok" if os.waitstatus_to_exitcode(status) == 0 else "failed", flush=True)


if __name__ == "__main__":
    main()
