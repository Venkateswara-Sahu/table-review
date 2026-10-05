import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from uuid import uuid4

import pytest

from tests.fixtures.tables import simple_table


def test_reload_and_original_revision_are_immutable(repository, table_record, owner_ids):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from table_review.storage.models import EditEventInput
    from table_review.tables.grid import apply_edit, parse_edit
    from table_review.tables.types import content_checksum

    original = repository.get_revision(
        owner_ids[0], table_record.id, table_record.current_revision_id
    )
    edited = apply_edit(
        original.content, parse_edit({"kind": "set_text", "cell_id": "r1c1", "text": "42"})
    ).content
    new = repository.save_revision(
        owner_ids[0],
        table_record.id,
        original.id,
        edited,
        EditEventInput(idempotency_key=uuid4(), operation={"kind": "set_text"}),
    )
    assert repository.get_table(owner_ids[0], table_record.id).current_revision_id == new.id
    assert repository.get_revision(
        owner_ids[0], table_record.id, new.id
    ).checksum == content_checksum(edited)
    assert (
        repository.get_revision(owner_ids[0], table_record.id, original.id).content
        == simple_table()
    )
    assert repository.revision_count(table_record.id) == 2
    # The DB itself rejects mutating a revision, even if a caller bypasses Repository.
    with pytest.raises(DBAPIError), repository.session_factory.begin() as session:
        session.execute(
            text("UPDATE table_revisions SET checksum='tampered' WHERE id=:id"),
            {"id": original.id},
        )


def submit_competing_edits(repository, table_record, owner_id, *, duplicate=False):
    from sqlalchemy import event

    from table_review.storage.models import EditEventInput
    from table_review.storage.repository import RevisionConflict
    from table_review.tables.grid import apply_edit, parse_edit

    barrier = Barrier(2)
    state = local()
    engine = repository.session_factory.kw["bind"]
    connections = set()
    shared_key = uuid4()

    def after_read(connection, cursor, statement, parameters, context, executemany):
        if (
            getattr(state, "competing", False)
            and not getattr(state, "waited", False)
            and "FROM review_tables" in statement
        ):
            state.waited = True
            connections.add(id(connection))
            barrier.wait(timeout=10)

    event.listen(engine, "after_cursor_execute", after_read)

    def submit(value):
        state.competing = True
        edited = apply_edit(
            simple_table(), parse_edit({"kind": "set_text", "cell_id": "r1c1", "text": value})
        ).content
        try:
            repository.save_revision(
                owner_id,
                table_record.id,
                table_record.current_revision_id,
                edited,
                EditEventInput(
                    idempotency_key=shared_key if duplicate else uuid4(),
                    operation={"kind": "set_text"},
                ),
            )
            return "saved"
        except RevisionConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, ["42", "42"] if duplicate else ["42", "99"]))
    finally:
        event.remove(engine, "after_cursor_execute", after_read)
    assert len(connections) == 2  # actually open separate transactions before either CAS update
    return outcomes


def test_competing_revisions_do_not_overwrite(repository, table_record, owner_ids):
    assert sorted(submit_competing_edits(repository, table_record, owner_ids[0])) == [
        "conflict",
        "saved",
    ]
    assert repository.revision_count(table_record.id) == 2


def test_simultaneous_exact_retries_return_one_saved_revision(repository, table_record, owner_ids):
    assert submit_competing_edits(repository, table_record, owner_ids[0], duplicate=True) == [
        "saved",
        "saved",
    ]
    assert repository.revision_count(table_record.id) == 2


def test_cross_owner_reads_and_writes_are_hidden(repository, table_record, owner_ids):
    from table_review.storage.models import EditEventInput
    from table_review.storage.repository import NotFound

    for call in [
        lambda: repository.get_table(owner_ids[1], table_record.id),
        lambda: repository.get_document(owner_ids[1], table_record.document_id),
        lambda: repository.get_revision(
            owner_ids[1], table_record.id, table_record.current_revision_id
        ),
        lambda: repository.save_revision(
            owner_ids[1],
            table_record.id,
            table_record.current_revision_id,
            simple_table(),
            EditEventInput(idempotency_key=uuid4(), operation={}),
        ),
    ]:
        with pytest.raises(NotFound):
            call()


def test_edit_idempotency_is_bound_to_account_and_payload(repository, table_record, owner_ids):
    from table_review.storage.models import EditEventInput
    from table_review.storage.repository import RevisionConflict
    from table_review.tables.grid import apply_edit, parse_edit

    key = uuid4()
    edited = apply_edit(
        simple_table(), parse_edit({"kind": "set_text", "cell_id": "r1c1", "text": "42"})
    ).content
    event = EditEventInput(idempotency_key=key, operation={"kind": "set_text"})
    first = repository.save_revision(
        owner_ids[0], table_record.id, table_record.current_revision_id, edited, event
    )
    repeat = repository.save_revision(
        owner_ids[0], table_record.id, table_record.current_revision_id, edited, event
    )
    assert first.id == repeat.id and repository.revision_count(table_record.id) == 2
    with pytest.raises(RevisionConflict):
        repository.save_revision(
            owner_ids[0], table_record.id, table_record.current_revision_id, simple_table(), event
        )


def test_private_blob_roundtrip_and_foreign_owner(tmp_path):
    from table_review.storage.blobs import PrivateBlobStore

    store = PrivateBlobStore(tmp_path)
    owner, foreign = uuid4(), uuid4()
    key = uuid4().hex + "/" + uuid4().hex
    record = store.put(owner, key, io.BytesIO(b"private PDF"))
    assert record.byte_count == 11
    with store.open(owner, key) as stream:
        assert stream.read() == b"private PDF"
    with pytest.raises(FileNotFoundError):
        store.open(foreign, key)
    store.delete(owner, key)
    with pytest.raises(FileNotFoundError):
        store.open(owner, key)


@pytest.mark.parametrize(
    "key",
    ["../secret", "/absolute", "C:/outside", "a\\b", "a/../b", "", "a", "x:stream", ".", "a//b"],
)
def test_blob_keys_are_opaque_and_cannot_escape(tmp_path, key):
    from table_review.storage.blobs import PrivateBlobStore

    store = PrivateBlobStore(tmp_path)
    with pytest.raises(ValueError):
        store.put(uuid4(), key, io.BytesIO(b"payload"))


def test_blob_put_never_overwrites_and_failed_write_cleans_up(tmp_path):
    from table_review.storage.blobs import PrivateBlobStore

    store = PrivateBlobStore(tmp_path)
    owner, key = uuid4(), uuid4().hex
    store.put(owner, key, io.BytesIO(b"original"))
    with pytest.raises(FileExistsError):
        store.put(owner, key, io.BytesIO(b"changed"))
    with store.open(owner, key) as stream:
        assert stream.read() == b"original"

    class Broken(io.BytesIO):
        def read(self, *args):
            raise OSError("simulated interrupted upload")

    with pytest.raises(OSError):
        store.put(owner, uuid4().hex, Broken())
    assert not list(tmp_path.rglob("*.tmp"))
