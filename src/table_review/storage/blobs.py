import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from table_review.storage.models import BlobRecord
from table_review.storage.scratch import exclusive_lock


class PrivateBlobStore:
    """Only application-created opaque keys; owner namespace is mandatory."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, owner_id: UUID, key: str) -> Path:
        owner = UUID(str(owner_id))
        if not re.fullmatch(r"[0-9a-f]{32}(?:/[0-9a-f]{32}){0,3}", key):
            raise ValueError("Blob key must be opaque hex components")
        path = self.root / owner.hex / key
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root / owner.hex):
            raise ValueError("Blob path escapes its owner namespace")
        if any(
            parent.is_symlink() for parent in [path, *path.parents] if parent != self.root.parent
        ):
            raise ValueError("Blob paths cannot contain symbolic links")
        return path

    def put(self, owner_id: UUID, key: str, source: BinaryIO) -> BlobRecord:
        path = self._path(owner_id, key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output, exclusive_lock(output):
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            # Link is an atomic, exclusive publication; unlike replace it cannot overwrite.
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        with path.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        return BlobRecord(owner_id=owner_id, key=key, sha256=digest, byte_count=path.stat().st_size)

    def open(self, owner_id: UUID, key: str) -> BinaryIO:
        return self._path(owner_id, key).open("rb")

    def delete(self, owner_id: UUID, key: str) -> None:
        self._path(owner_id, key).unlink(missing_ok=True)
