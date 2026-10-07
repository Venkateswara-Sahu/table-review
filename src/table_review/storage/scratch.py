"""Native locks keep active scratch safe even when an operation stalls."""

import os
from contextlib import contextmanager


@contextmanager
def exclusive_lock(stream):
    position = stream.tell()
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError("Scratch is in use") from error
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    stream.seek(position)
    try:
        yield
    finally:
        position = stream.tell()
        stream.seek(0)
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.seek(position)


@contextmanager
def upload_scratch(root):
    import tempfile
    from pathlib import Path
    from uuid import uuid4

    with tempfile.TemporaryDirectory(prefix="upload-" + uuid4().hex + "-", dir=root) as temporary:
        directory = Path(temporary)
        with (directory / ".active").open("x+b") as stream, exclusive_lock(stream):
            yield directory
