import io
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.fixtures.pdf_factory import two_page_report
from tests.fixtures.tables import simple_table


@pytest.fixture
def owned_table(app, owner, tmp_path):
    from table_review.accounts.types import Actor

    actor = Actor(id=owner.id)
    path = two_page_report(tmp_path / "review.pdf")
    doc = app.state.documents.upload(
        actor, io.BytesIO(path.read_bytes()), "review.pdf", uuid4().hex
    )
    table = app.state.repository.create_table(
        owner.id,
        doc.id,
        simple_table(),
        extraction_manifest={"device": "cpu", "model_identity": "test-fixture"},
    )
    return actor, doc, table


def edit(client, table, operation, base=None, key=None):
    return client.post(
        f"/api/v1/tables/{table.id}/edits",
        json={"base_revision_id": str(base or table.current_revision_id), "operation": operation},
        headers={"Idempotency-Key": str(key or uuid4())},
    )


def check_all(client, table):
    return client.post(
        f"/api/v1/revisions/{table.current_revision_id}/checked",
        json={"cell_ids": [cell.id for cell in simple_table().cells], "checked": True},
    )


def approve(client, revision_id, warnings=True):
    return client.post(
        f"/api/v1/revisions/{revision_id}/approval", json={"allow_warnings": warnings}
    )


def test_never_auto_approves_and_checks_not_run_is_a_warning(client, owned_table):
    _, _, table = owned_table
    state = client.get(f"/api/v1/revisions/{table.current_revision_id}/review")
    assert state.status_code == 200
    assert state.json()["checked_cell_ids"] == []
    assert state.json()["current_approval"] is None
    assert state.json()["checks_not_run"]
    assert approve(client, table.current_revision_id).status_code == 409
    assert check_all(client, table).status_code == 200
    assert approve(client, table.current_revision_id, warnings=False).status_code == 409
    snapshot = approve(client, table.current_revision_id).json()
    assert snapshot["label"] == "reviewed_with_warnings"
    assert snapshot["unresolved_count"] == 3  # Not run, unavailable coverage, limited geometry.
    assert {i["kind"] for i in snapshot["issues"]} == {"coverage_unavailable", "limited_reference"}
    assert snapshot["check_versions"] == {
        "engine": "2",
        "coverage": "not_run",
        "source_identity": "not_run",
        "placement": "2",
        "structural": "2",
    }


def test_text_edit_retains_old_snapshot_and_carries_only_unchanged_checks(client, app, owned_table):
    actor, _, table = owned_table
    check_all(client, table)
    old_snapshot = approve(client, table.current_revision_id).json()
    response = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "42"})
    assert response.status_code == 200
    saved = response.json()
    state = client.get(f"/api/v1/revisions/{saved['id']}/review").json()
    assert "r1c1" not in state["checked_cell_ids"] and len(state["checked_cell_ids"]) == 8
    assert state["current_approval"] is None
    assert app.state.reviews.current_approval(actor, table.current_revision_id) is None
    retained = client.get(f"/api/v1/approvals/{old_snapshot['id']}").json()
    assert retained == old_snapshot
    assert saved["extraction_manifest"]["device"] == "cpu"
    original = client.get(
        f"/api/v1/tables/{table.id}/revisions/{table.original_revision_id}"
    ).json()
    assert original["content"]["cells"][4]["text"] == "1:1"
    assert saved["content"]["cells"][4]["raw_text"] == "1:1"
    assert saved["content"]["cells"][4]["text"] == "42"


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "insert_row", "index": 1},
        {"kind": "delete_row", "index": 1},
        {"kind": "insert_column", "index": 1},
        {"kind": "delete_column", "index": 1},
        {"kind": "merge", "row": 0, "col": 0, "rows": 1, "columns": 2},
        {"kind": "set_headers", "header_rows": [0], "row_label_columns": [0]},
        {"kind": "set_notes", "unit_notes": ["EUR thousands"]},
    ],
)
def test_structural_and_association_edits_clear_all_checks(client, owned_table, operation):
    _, _, table = owned_table
    check_all(client, table)
    approve(client, table.current_revision_id)
    response = edit(client, table, operation)
    assert response.status_code == 200
    state = client.get(f"/api/v1/revisions/{response.json()['id']}/review").json()
    assert state["checked_cell_ids"] == [] and state["current_approval"] is None


def test_split_and_interpretation_edit_are_saved_atomically(client, owned_table):
    _, _, table = owned_table
    merged = edit(
        client, table, {"kind": "merge", "row": 0, "col": 0, "rows": 1, "columns": 2}
    ).json()
    split = edit(client, table, {"kind": "split", "cell_id": "r0c0"}, base=merged["id"])
    assert split.status_code == 200
    assert len(split.json()["content"]["cells"]) == 9
    interpreted = edit(
        client,
        table,
        {"kind": "set_interpretation", "cell_id": "r1c1", "interpretation": "text"},
        base=split.json()["id"],
    )
    assert interpreted.status_code == 200
    cell = next(c for c in interpreted.json()["content"]["cells"] if c["id"] == "r1c1")
    assert cell["interpretation"] == "text"


def test_restore_is_new_draft_with_history_and_original_retained(client, owned_table):
    _, _, table = owned_table
    changed = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "42"}).json()
    restored = client.post(
        f"/api/v1/tables/{table.id}/restore",
        json={
            "base_revision_id": changed["id"],
            "historical_revision_id": str(table.original_revision_id),
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert restored.status_code == 200
    result = restored.json()
    assert result["id"] not in (changed["id"], str(table.original_revision_id))
    assert result["content"] == simple_table().model_dump(mode="json")
    state = client.get(f"/api/v1/revisions/{result['id']}/review").json()
    assert state["checked_cell_ids"] == [] and state["current_approval"] is None
    history = client.get(f"/api/v1/tables/{table.id}/history").json()["items"]
    assert len(history) == 2
    assert history[0]["operation"]["kind"] == "restore"


def test_manual_table_uses_honest_region_reference_and_original_page(client, owned_table):
    _, doc, _ = owned_table
    region = {
        "page": 2,
        "left": 60,
        "top": 124,
        "right": 480,
        "bottom": 252,
        "page_width": 612,
        "page_height": 792,
    }
    response = client.post(
        f"/api/v1/documents/{doc.id}/tables", json={"region": region, "rows": 2, "columns": 3}
    )
    assert response.status_code == 201
    table = response.json()
    assert table["origin"] == "manual"
    content = client.get(f"/api/v1/tables/{table['id']}").json()["revision"]["content"]
    assert len(content["cells"]) == 6
    assert all(
        c["reference"]["precision"] == "table"
        and c["reference"]["page"] == 2
        and c["reference"]["origin"] == "user"
        for c in content["cells"]
    )
    assert all(c["text"] == "" and c["raw_text"] == "" for c in content["cells"])
    region["page"] = 3
    assert (
        client.post(
            f"/api/v1/documents/{doc.id}/tables", json={"region": region, "rows": 2, "columns": 3}
        ).status_code
        == 422
    )


def test_edit_retry_is_exact_and_changed_retry_conflicts(client, owned_table):
    _, _, table = owned_table
    key = uuid4()
    operation = {"kind": "set_text", "cell_id": "r1c1", "text": "42"}
    first = edit(client, table, operation, key=key)
    assert first.status_code == 200
    assert edit(client, table, operation, key=key).json() == first.json()
    operation["text"] = "43"
    assert edit(client, table, operation, key=key).status_code == 409


@pytest.mark.parametrize("kind", ["insert_row", "insert_column", "split"])
def test_structural_edit_retry_returns_saved_ids_and_rejects_changed_payload(
    client, app, owned_table, kind
):
    _, _, table = owned_table
    base = table.current_revision_id
    if kind == "split":
        merged = edit(client, table, {"kind": "merge", "row": 0, "col": 0, "rows": 1, "columns": 2})
        assert merged.status_code == 200
        base = merged.json()["id"]
        operation = {"kind": "split", "cell_id": merged.json()["content"]["cells"][0]["id"]}
    else:
        operation = {"kind": kind, "index": 1}
    key = uuid4()
    first = edit(client, table, operation, base=base, key=key)
    assert first.status_code == 200
    count = app.state.repository.revision_count(table.id)
    retry = edit(client, table, operation, base=base, key=key)
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert app.state.repository.revision_count(table.id) == count
    changed = (
        {**operation, "index": 0}
        if kind != "split"
        else {"kind": "set_text", "cell_id": operation["cell_id"], "text": "changed"}
    )
    assert edit(client, table, changed, base=base, key=key).status_code == 409


def test_concurrent_identical_insert_retry_creates_one_revision(app, owned_table, monkeypatch):
    from threading import Barrier

    from table_review.tables.grid import parse_edit

    actor, _, table = owned_table
    barrier = Barrier(2)
    original = app.state.repository.get_edit_retry

    def lookup(*args):
        result = original(*args)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(app.state.repository, "get_edit_retry", lookup)
    key = uuid4()
    operation = parse_edit({"kind": "insert_row", "index": 1})
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(
                app.state.tables.edit,
                actor,
                table.id,
                table.current_revision_id,
                operation,
                idempotency_key=key,
            )
            for _ in range(2)
        ]
        results = [future.result(timeout=10) for future in futures]
    assert results[0].id == results[1].id
    assert results[0].content == results[1].content
    assert app.state.repository.revision_count(table.id) == 2


def test_competing_browser_revisions_do_not_overwrite(client, app, owned_table):
    actor, _, table = owned_table
    from table_review.storage.repository import RevisionConflict
    from table_review.tables.grid import parse_edit

    def save(value):
        try:
            app.state.tables.edit(
                actor,
                table.id,
                table.current_revision_id,
                parse_edit({"kind": "set_text", "cell_id": "r1c1", "text": value}),
            )
            return "saved"
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(save, ["42", "99"])) == ["conflict", "saved"]


def test_uncheck_invalidates_current_approval_but_not_saved_snapshot(client, owned_table):
    _, _, table = owned_table
    check_all(client, table)
    snapshot = approve(client, table.current_revision_id).json()
    state = client.post(
        f"/api/v1/revisions/{table.current_revision_id}/checked",
        json={"cell_ids": ["r1c1"], "checked": False},
    ).json()
    assert state["current_approval"] is None
    assert client.get(f"/api/v1/approvals/{snapshot['id']}").json() == snapshot
    assert (
        client.post(
            f"/api/v1/revisions/{table.current_revision_id}/checked",
            json={"cell_ids": ["missing-cell"], "checked": True},
        ).status_code
        == 422
    )


def test_checks_change_invalidates_approval_and_clean_checked_table_can_be_reviewed(
    client, app, owned_table
):
    actor, _, table = owned_table
    check_all(client, table)
    # This test isolates the approval contract from the actual check engine.
    app.state.reviews.checker = None
    app.state.reviews.set_checks(actor, table.current_revision_id, (), {"fixture": "v1"})
    snapshot = approve(client, table.current_revision_id, warnings=False).json()
    assert snapshot["label"] == "reviewed" and snapshot["unresolved_count"] == 0
    app.state.reviews.set_checks(actor, table.current_revision_id, (), {"fixture": "v2"})
    assert app.state.reviews.current_approval(actor, table.current_revision_id) is None
    assert app.state.reviews.snapshot(actor, snapshot["id"]).label == "reviewed"


def test_all_review_resources_are_owner_scoped(client, client_b, owned_table):
    _, doc, table = owned_table
    check_all(client, table)
    snapshot = approve(client, table.current_revision_id).json()
    for path in (
        f"/tables/{table.id}",
        f"/tables/{table.id}/history",
        f"/tables/{table.id}/revisions/{table.original_revision_id}",
        f"/revisions/{table.current_revision_id}/review",
        f"/approvals/{snapshot['id']}",
        f"/documents/{doc.id}/tables",
    ):
        assert client_b.get("/api/v1" + path).status_code == 404
    assert (
        edit(client_b, table, {"kind": "set_text", "cell_id": "r1c1", "text": "42"}).status_code
        == 404
    )
    assert (
        client_b.post(
            f"/api/v1/revisions/{table.current_revision_id}/checked",
            json={"cell_ids": ["r1c1"], "checked": True},
        ).status_code
        == 404
    )
    assert approve(client_b, table.current_revision_id).status_code == 404


def test_review_failure_rolls_back_content_and_event(app, owned_table, monkeypatch):
    actor, _, table = owned_table
    from table_review.tables.grid import parse_edit

    def fail(*args):
        raise RuntimeError("injected internal checking failure")

    monkeypatch.setattr(app.state.reviews, "after_revision", fail)
    with pytest.raises(RuntimeError, match="injected"):
        app.state.tables.edit(
            actor,
            table.id,
            table.current_revision_id,
            parse_edit({"kind": "set_text", "cell_id": "r1c1", "text": "42"}),
        )
    assert (
        app.state.repository.get_table(actor.id, table.id).current_revision_id
        == table.current_revision_id
    )
    assert app.state.repository.revision_count(table.id) == 1
    assert app.state.tables.history(actor, table.id) == ()


def test_snapshot_database_rejects_mutation(client, app, owned_table):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _, _, table = owned_table
    check_all(client, table)
    snapshot = approve(client, table.current_revision_id).json()
    with pytest.raises(DBAPIError), app.state.session_factory.begin() as session:
        session.execute(
            text("UPDATE review_snapshots SET label='reviewed' WHERE id=:id"),
            {"id": snapshot["id"]},
        )
