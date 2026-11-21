import hashlib
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from table_review.accounts.types import Actor
from table_review.documents.errors import DocumentError
from table_review.documents.service import DocumentService
from table_review.settings import Settings
from table_review.storage.blobs import PrivateBlobStore
from table_review.storage.models import DocumentRecord
from tests.fixtures.pdf_factory import two_page_report


class _FakeRepo:
    def __init__(self, record: DocumentRecord):
        self.record = record
        self.calls = 0

    def get_document(self, actor_id, document_id):
        assert actor_id == self.record.owner_id and document_id == self.record.id
        self.calls += 1
        return self.record


def test_source_path_uses_stat_cache_and_rejects_tampering(tmp_path, monkeypatch):
    actor = Actor(id=uuid4())
    pdf_path = two_page_report(tmp_path / "src.pdf")
    raw_bytes = pdf_path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()

    blobs = PrivateBlobStore(tmp_path / "blobs")
    blob_key = uuid4().hex
    with pdf_path.open("rb") as stream:
        blobs.put(actor.id, blob_key, stream)

    record = DocumentRecord(
        id=uuid4(),
        owner_id=actor.id,
        filename="src.pdf",
        source_sha256=digest,
        byte_count=len(raw_bytes),
        page_count=2,
        blob_key=blob_key,
        preparation_state="ready",
        created_at=datetime.now(UTC),
        last_foreground_at=datetime.now(UTC),
    )
    repo = _FakeRepo(record)
    service = DocumentService(repo, blobs, Settings(blob_root=tmp_path / "blobs"))

    digest_calls = 0
    real_file_digest = hashlib.file_digest

    def spy_file_digest(fileobj, alg, *args, **kwargs):
        nonlocal digest_calls
        digest_calls += 1
        return real_file_digest(fileobj, alg, *args, **kwargs)

    monkeypatch.setattr(hashlib, "file_digest", spy_file_digest)

    # First call verifies the source file hash on disk.
    resolved = service.source_path(actor, record.id)
    assert resolved.is_file()
    assert digest_calls == 1
    assert repo.calls == 1

    # Second call re-checks auth via repository but hits stat cache without re-hashing.
    service.source_path(actor, record.id)
    assert digest_calls == 1
    assert repo.calls == 2

    # verify_fresh=True always forces a fresh hash read (e.g. export generation).
    service.source_path(actor, record.id, verify_fresh=True)
    assert digest_calls == 2

    # Corrupting stored bytes while preserving size & mtime still fails when verified fresh,
    # and corrupting bytes normally invalidates stat signature immediately.
    before = resolved.stat()
    tampered = bytearray(resolved.read_bytes())
    tampered[-5] ^= 0x55
    resolved.write_bytes(tampered)
    os.utime(resolved, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(DocumentError, match="no longer matches"):
        service.source_path(actor, record.id, verify_fresh=True)

    resolved.write_bytes(raw_bytes + b"%extra")
    with pytest.raises(DocumentError, match="no longer matches"):
        service.source_path(actor, record.id)
