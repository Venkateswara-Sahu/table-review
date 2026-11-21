"""Warm bounded PDF preview worker for non-fork platforms (Windows/macOS)."""

import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image

from table_review.documents.pdf_child import run_request


def main():
    proto_fd = os.dup(sys.stdout.fileno())
    proto_out = os.fdopen(proto_fd, "w", encoding="utf-8", buffering=1)
    with open(os.devnull, "wb") as silent:
        os.dup2(silent.fileno(), 1)
    sys.stdout = sys.stderr

    Image.preinit()
    proto_out.write("ready\n")
    proto_out.flush()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request, destination = map(Path, json.loads(line))
            scratch = str(request.parent)
            os.environ["TMP"] = scratch
            os.environ["TEMP"] = scratch
            os.environ["TMPDIR"] = scratch
            tempfile.tempdir = scratch
            run_request(request, destination)
            proto_out.write("ok\n")
            proto_out.flush()
        except Exception:  # noqa: BLE001 - isolate failures without leaking source data
            proto_out.write("failed\n")
            proto_out.flush()


if __name__ == "__main__":
    main()
