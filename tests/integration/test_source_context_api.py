import io
from uuid import uuid4

import pytest

from table_review.accounts.types import Actor
from tests.domain.test_source_context import contextual_table
from tests.fixtures.pdf_factory import two_page_report


@pytest.fixture
def context_table(app, owner, tmp_path):
    source = two_page_report(tmp_path / "context.pdf")
    document = app.state.documents.upload(
        Actor(id=owner.id), io.BytesIO(source.read_bytes()), "context.pdf", uuid4().hex
    )
    return app.state.repository.create_table(
        owner.id, document.id, contextual_table(), extraction_manifest={"fixture": "context"}
    )


def context(client, table, revision=None, cell="r2c1"):
    return client.get(
        f"/api/v1/tables/{table.id}/revisions/{revision or table.current_revision_id}/context/{cell}"
    )


def edit(client, table, operation, base=None, key=None):
    return client.post(
        f"/api/v1/tables/{table.id}/edits",
        json={
            "base_revision_id": str(base or table.current_revision_id),
            "operation": operation,
        },
        headers={"Idempotency-Key": str(key or uuid4())},
    )


def test_context_confirmation_is_owner_scoped_idempotent_and_source_backed(
    client, client_b, owner, context_table
):
    table = context_table
    response = context(client, table)
    assert response.status_code == 200
    assert context(client_b, table).status_code == 404
    proposal = next(item for item in response.json()["proposals"] if item["kind"] == "period")
    operation = {
        "kind": "confirm_context",
        "cell_ids": ["r2c1", "r3c1"],
        "candidate_id": proposal["id"],
    }
    key = uuid4()
    first = edit(client, table, operation, key=key)
    assert first.status_code == 200
    assert edit(client, table, operation, key=key).json()["id"] == first.json()["id"]
    confirmed = context(client, table, first.json()["id"]).json()["confirmed"]
    assert len(confirmed) == 1
    assert confirmed[0]["value"] == "2024"
    assert confirmed[0]["source_ids"] == ["cell:year-2024"]
    assert confirmed[0]["confirmed_by"] == str(owner.id) and confirmed[0]["confirmed_at"]
    assert context(client, table).json()["confirmed"] == []


def test_wrong_period_range_is_rejected_atomically(client, context_table):
    table = context_table
    proposal = next(
        item for item in context(client, table).json()["proposals"] if item["kind"] == "period"
    )
    failed = edit(
        client,
        table,
        {"kind": "confirm_context", "cell_ids": ["r2c1", "r2c3"], "candidate_id": proposal["id"]},
    )
    assert failed.status_code == 422
    assert context(client, table).json()["confirmed"] == []


def test_editing_year_reopens_dependent_cell_reviews_and_context(client, context_table):
    table = context_table
    proposal = next(
        item for item in context(client, table).json()["proposals"] if item["kind"] == "period"
    )
    saved = edit(
        client,
        table,
        {"kind": "confirm_context", "cell_ids": ["r2c1"], "candidate_id": proposal["id"]},
    ).json()
    checked = client.post(
        f"/api/v1/revisions/{saved['id']}/checked",
        json={"cell_ids": ["r2c1", "r2c3"], "checked": True},
    )
    assert checked.status_code == 200
    changed = edit(
        client,
        table,
        {"kind": "set_text", "cell_id": "year-2024", "text": "2025"},
        base=saved["id"],
    )
    assert changed.status_code == 200
    current = context(client, table, changed.json()["id"]).json()
    assert current["confirmed"] == []
    assert any(
        item["kind"] == "period" and item["value"] == "2025" for item in current["proposals"]
    )
    review = client.get(f"/api/v1/revisions/{changed.json()['id']}/review").json()
    assert "r2c1" not in review["checked_cell_ids"] and "r2c3" in review["checked_cell_ids"]
    assert len(context(client, table, saved["id"]).json()["confirmed"]) == 1


def test_simultaneous_identical_confirmation_returns_one_revision(
    app, owner, context_table, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from table_review.tables.context import ContextIndex
    from table_review.tables.grid import parse_edit

    table = context_table
    candidate = next(
        item
        for item in ContextIndex(contextual_table()).cell("r2c1").proposals
        if item.kind == "period"
    )
    operation = parse_edit(
        {"kind": "confirm_context", "cell_ids": ["r2c1"], "candidate_id": candidate.id}
    )
    barrier = Barrier(2)
    repository = app.state.repository
    original = repository.get_edit_retry

    def synchronized(*args):
        result = original(*args)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(repository, "get_edit_retry", synchronized)
    key = uuid4()

    def confirm():
        return app.state.tables.edit(
            Actor(id=owner.id), table.id, table.current_revision_id, operation, idempotency_key=key
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: confirm(), range(2)))
    assert first.id == second.id
    assert first.content.context_bindings == second.content.context_bindings
    assert repository.revision_count(table.id) == 2


def test_stale_confirmations_and_restore_preserve_historical_evidence(client, context_table):
    table = context_table
    candidate = next(
        item for item in context(client, table).json()["proposals"] if item["kind"] == "period"
    )
    op = {"kind": "confirm_context", "cell_ids": ["r2c1"], "candidate_id": candidate["id"]}
    saved = edit(client, table, op).json()
    assert edit(client, table, op).status_code == 409
    changed = edit(
        client, table, {"kind": "set_text", "cell_id": "r2c1", "text": "15"}, base=saved["id"]
    ).json()
    restored = client.post(
        f"/api/v1/tables/{table.id}/restore",
        json={
            "base_revision_id": changed["id"],
            "historical_revision_id": saved["id"],
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert restored.status_code == 200
    assert not restored.json()["content"]["context_bindings"]
    assert len(context(client, table, saved["id"]).json()["confirmed"]) == 1
