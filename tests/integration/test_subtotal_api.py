from uuid import uuid4

import pytest

from tests.fixtures.subtotals import subtotal_table
from tests.integration.test_review_api import owned_table  # noqa: F401


@pytest.fixture
def subtotal_record(app, owner, owned_table):  # noqa: F811
    _, doc, _ = owned_table
    return app.state.repository.create_table(
        owner.id, doc.id, subtotal_table(), extraction_manifest={"fixture": "declared subtotal"}
    )


def confirm(client, table, base=None, key=None, **changes):
    operation = {
        "kind": "confirm_subtotal",
        "group_id": str(uuid4()),
        "name": "Operating total",
        "total_cell_id": "r3c1",
        "terms": [{"cell_id": "r1c1", "sign": 1}, {"cell_id": "r2c1", "sign": 1}],
        "tolerance": "0",
        "confirmed_scope": True,
        **changes,
    }
    return client.post(
        f"/api/v1/tables/{table.id}/edits",
        json={"base_revision_id": str(base or table.current_revision_id), "operation": operation},
        headers={"Idempotency-Key": str(key or uuid4())},
    )


def results(client, table, revision):
    return client.get(f"/api/v1/tables/{table.id}/revisions/{revision}/subtotals")


def test_declared_group_is_revisioned_owner_scoped_and_retries_are_exact(
    client, client_b, owner, subtotal_record
):
    table = subtotal_record
    group, key = uuid4(), uuid4()
    saved = confirm(client, table, group_id=str(group), key=key)
    assert saved.status_code == 200
    revision = saved.json()["id"]
    assert confirm(client, table, group_id=str(group), key=key).json()["id"] == revision
    item = results(client, table, revision).json()["items"][0]
    assert item["state"] == "match" and item["input_sum"] == "8.05"
    assert item["group"]["confirmed_by"] == str(owner.id) and item["group"]["confirmed_at"]
    assert results(client_b, table, revision).status_code == 404
    assert results(client, table, table.original_revision_id).json()["items"] == []


def test_edit_reopens_group_and_reconfirmation_produces_explained_difference(
    client, subtotal_record
):
    table = subtotal_record
    saved = confirm(client, table).json()
    altered = client.post(
        f"/api/v1/tables/{table.id}/edits",
        json={
            "base_revision_id": saved["id"],
            "operation": {"kind": "set_text", "cell_id": "r3c1", "text": "9.05"},
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert altered.status_code == 200
    item = results(client, table, altered.json()["id"]).json()["items"][0]
    assert item["state"] in ("unavailable", "needs_confirmation")
    assert results(client, table, saved["id"]).json()["items"][0]["state"] == "match"
    revision = altered.json()["id"]
    for kind in ("period", "currency", "scale"):
        context = client.get(f"/api/v1/tables/{table.id}/revisions/{revision}/context/r3c1").json()
        candidate = next(item for item in context["proposals"] if item["kind"] == kind)
        restored = client.post(
            f"/api/v1/tables/{table.id}/edits",
            json={
                "base_revision_id": revision,
                "operation": {
                    "kind": "confirm_context",
                    "cell_ids": ["r3c1"],
                    "candidate_id": candidate["id"],
                },
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert restored.status_code == 200
        revision = restored.json()["id"]
    reconfirmed = confirm(
        client, table, base=revision, group_id=saved["content"]["subtotal_groups"][0]["id"]
    )
    assert reconfirmed.status_code == 200
    revision = reconfirmed.json()["id"]
    item = results(client, table, revision).json()["items"][0]
    assert item["state"] == "difference" and item["difference"] == "1.00"
    review = client.get(f"/api/v1/revisions/{revision}/review").json()
    assert any(issue["kind"] == "subtotal_difference" for issue in review["issues"])


def test_unreadable_input_blocks_an_existing_relation_without_content_edit(client, subtotal_record):
    table = subtotal_record
    saved = confirm(client, table).json()
    unreadable = client.post(
        f"/api/v1/revisions/{saved['id']}/cell-review",
        json={
            "cell_ids": ["r1c1"],
            "verdict": "unreadable",
            "reason": "Digits cannot be read reliably",
        },
    )
    assert unreadable.status_code == 200
    assert results(client, table, saved["id"]).json()["items"][0]["state"] == "unavailable"
    assert any(issue["kind"] == "subtotal_unavailable" for issue in unreadable.json()["issues"])
    assert confirm(client, table, base=saved["id"]).status_code == 422


def test_bad_or_unconfirmed_scope_is_rejected_atomically(client, subtotal_record):
    table = subtotal_record
    assert confirm(client, table, terms=[{"cell_id": "r3c1", "sign": 1}]).status_code == 422
    assert confirm(client, table, confirmed_scope=False).status_code == 422
    assert client.get(f"/api/v1/tables/{table.id}").json()["revision"]["id"] == str(
        table.current_revision_id
    )


def test_carried_unreadable_decision_keeps_subtotal_warning_after_removing_another_group(
    client, subtotal_record
):
    table = subtotal_record
    first = confirm(client, table).json()
    second = confirm(client, table, base=first["id"]).json()
    removed_id = second["content"]["subtotal_groups"][-1]["id"]
    response = client.post(
        f"/api/v1/revisions/{second['id']}/cell-review",
        json={"cell_ids": ["r1c1"], "verdict": "unreadable", "reason": "Unreadable source"},
    )
    assert response.status_code == 200
    changed = client.post(
        f"/api/v1/tables/{table.id}/edits",
        json={
            "base_revision_id": second["id"],
            "operation": {"kind": "remove_subtotal", "group_id": removed_id},
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert changed.status_code == 200
    review = client.get(f"/api/v1/revisions/{changed.json()['id']}/review").json()
    assert review["cell_reviews"]["r1c1"]["verdict"] == "unreadable"
    assert any(issue["kind"] == "subtotal_unavailable" for issue in review["issues"])
    assert results(client, table, changed.json()["id"]).json()["items"][0]["state"] == "unavailable"
