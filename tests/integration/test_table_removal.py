# ruff: noqa: F811
# Imported pytest fixture is intentionally reused as test parameters.
from uuid import uuid4

from tests.integration.test_review_api import approve, check_all, edit, owned_table  # noqa: F401


def visibility(client, table, removed, version=0, revision=None):
    return client.post(
        f"/api/v1/tables/{table.id}/removal",
        json={
            "removed": removed,
            "expected_version": version,
            "base_revision_id": str(revision or table.current_revision_id),
        },
    )


def test_remove_denies_active_reads_and_restores_without_review_or_approval(
    client, app, owned_table
):
    _, doc, table = owned_table
    check_all(client, table)
    approval = approve(client, table.current_revision_id).json()
    removed = visibility(client, table, True)
    assert removed.status_code == 200
    assert removed.json()["removed_at"] and removed.json()["removal_version"] == 1
    assert client.get(f"/api/v1/documents/{doc.id}/tables").json()["items"] == []
    archived = client.get(f"/api/v1/documents/{doc.id}/tables?removed=true").json()["items"]
    assert [item["id"] for item in archived] == [str(table.id)]
    for route in (
        f"/tables/{table.id}",
        f"/tables/{table.id}/history",
        f"/tables/{table.id}/revisions/{table.current_revision_id}",
        f"/revisions/{table.current_revision_id}/review",
        f"/revisions/{table.current_revision_id}/timeline",
    ):
        assert client.get("/api/v1" + route).status_code == 404
    assert (
        edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "99"}).status_code
        == 404
    )
    restored = visibility(client, table, False, 1)
    assert restored.status_code == 200 and restored.json()["removed_at"] is None
    assert restored.json()["removal_version"] == 2
    state = client.get(f"/api/v1/revisions/{table.current_revision_id}/review").json()
    assert state["checked_cell_ids"] == [] and state["cell_reviews"] == {}
    assert state["issue_decisions"] == {} and state["current_approval"] is None
    assert approve(client, table.current_revision_id).status_code == 409
    events = client.get(f"/api/v1/revisions/{table.current_revision_id}/timeline").json()["items"]
    transitions = [item for item in events if item["kind"] == "table_visibility"]
    assert len(transitions) == 2
    assert transitions[0]["details"]["invalidated_approval_id"] == approval["id"]
    assert len(transitions[0]["details"]["invalidated_cell_ids"]) == 9
    assert transitions[0]["details"]["before"]["cell_reviews"]
    assert client.get(f"/api/v1/approvals/{approval['id']}").json() == approval


def test_visibility_retries_and_delayed_prior_cycle_cannot_repeat_changes(client, owned_table):
    _, _, table = owned_table
    assert visibility(client, table, True).status_code == 200
    assert visibility(client, table, True).json()["removal_version"] == 1
    assert visibility(client, table, False, 1).status_code == 200
    assert visibility(client, table, False, 1).json()["removal_version"] == 2
    assert visibility(client, table, True, 0).status_code == 409
    assert visibility(client, table, True, 2).status_code == 200
    assert visibility(client, table, False, 1).status_code == 409
    assert visibility(client, table, False, 3).status_code == 200
    events = client.get(f"/api/v1/revisions/{table.current_revision_id}/timeline").json()["items"]
    assert len([item for item in events if item["kind"] == "table_visibility"]) == 4


def test_removal_owner_revision_and_parent_deletion_guards(client, client_b, owned_table):
    _, doc, table = owned_table
    assert visibility(client_b, table, True).status_code == 404
    assert client_b.get(f"/api/v1/documents/{doc.id}/tables?removed=true").status_code == 404
    assert visibility(client, table, True, revision=uuid4()).status_code == 409
    assert visibility(client, table, True).status_code == 200
    assert client.delete(f"/api/v1/documents/{doc.id}").status_code == 200
    assert visibility(client, table, False, 1).status_code == 404
    assert client.get(f"/api/v1/documents/{doc.id}/tables?removed=true").status_code == 404


def test_oversized_removal_evidence_rolls_back_visibility_and_review(
    client, app, owned_table, monkeypatch
):
    from table_review.reviews import timeline

    _, _, table = owned_table
    check_all(client, table)
    before = client.get(f"/api/v1/revisions/{table.current_revision_id}/review").json()
    monkeypatch.setattr(timeline, "BUDGET", 512)
    assert visibility(client, table, True).status_code == 422
    active = client.get(f"/api/v1/tables/{table.id}")
    assert active.status_code == 200 and active.json()["table"]["removal_version"] == 0
    assert client.get(f"/api/v1/revisions/{table.current_revision_id}/review").json() == before


def test_writer_waiting_for_document_lock_cannot_edit_after_removal(
    app, client, owned_table, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread

    import pytest

    from table_review.storage.repository import NotFound
    from table_review.tables.grid import SetText

    actor, _, table = owned_table
    waiting, release = Event(), Event()
    original = app.state.repository._lock_document

    def pause(session, owner, document):
        if current_thread().name.startswith("waiting-edit"):
            waiting.set()
            assert release.wait(10)
        return original(session, owner, document)

    monkeypatch.setattr(app.state.repository, "_lock_document", pause)
    with ThreadPoolExecutor(1, thread_name_prefix="waiting-edit") as pool:
        future = pool.submit(
            app.state.tables.edit,
            actor,
            table.id,
            table.current_revision_id,
            SetText(kind="set_text", cell_id="r1c1", text="late overwrite"),
        )
        try:
            assert waiting.wait(10)
            assert visibility(client, table, True).status_code == 200
        finally:
            release.set()
        with pytest.raises(NotFound):
            future.result(timeout=10)
    assert visibility(client, table, False, 1).status_code == 200
    assert client.get(f"/api/v1/tables/{table.id}").json()["revision"]["id"] == str(
        table.current_revision_id
    )


def test_export_requested_before_removal_keeps_its_captured_file(app, client, owned_table):
    from sqlalchemy import update

    from table_review.jobs.models import TaskRow
    from tests.integration.test_exports import finish_requested_export_via_worker, request

    # Isolate this worker rehearsal from queued jobs left by earlier module tests.
    with app.state.session_factory.begin() as session:
        session.execute(
            update(TaskRow)
            .where(TaskRow.state.in_(("queued", "running")))
            .values(state="cancelled", lease_token=None)
        )
    _, _, table = owned_table
    queued = request(client, table).json()
    assert visibility(client, table, True).status_code == 200
    _, expected = finish_requested_export_via_worker(app, client, queued["id"])
    assert client.get(f"/api/v1/exports/{queued['id']}/download").content == expected
    assert (
        client.post(
            f"/api/v1/tables/{table.id}/exports",
            json={"revision_id": str(table.current_revision_id), "format": "workbook"},
            headers={"Idempotency-Key": uuid4().hex},
        ).status_code
        == 404
    )
