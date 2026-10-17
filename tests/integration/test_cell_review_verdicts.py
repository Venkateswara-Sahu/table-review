# ruff: noqa: F811 -- pytest fixtures are imported and injected as test parameters.
import io
import json
import zipfile
from uuid import uuid4

import pytest

from tests.integration.test_exports import clear_tasks  # noqa: F401
from tests.integration.test_review_api import approve, edit, owned_table  # noqa: F401


def verdict(client, revision_id, cell_ids, value, reason=""):
    return client.post(
        f"/api/v1/revisions/{revision_id}/cell-review",
        json={"cell_ids": cell_ids, "verdict": value, "reason": reason},
    )


def make_blank(client, table):
    result = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": ""})
    assert result.status_code == 200
    return result.json()["id"]


def test_checking_empty_cell_requires_explicit_blank_decision(client, owned_table):
    _, _, table = owned_table
    revision = make_blank(client, table)
    response = client.post(
        f"/api/v1/revisions/{revision}/checked",
        json={"cell_ids": ["r0c0", "r1c1"], "checked": True},
    )
    assert response.status_code == 422
    state = client.get(f"/api/v1/revisions/{revision}/review").json()
    assert state["checked_cell_ids"] == []
    assert any(issue["kind"] == "unreviewed_blank" for issue in state["issues"])


def test_blank_decision_retains_reviewer_source_and_invalidates_after_edit(client, owned_table):
    actor, _, table = owned_table
    revision = make_blank(client, table)
    response = verdict(client, revision, ["r1c1"], "confirmed_blank")
    assert response.status_code == 200
    record = response.json()["cell_reviews"]["r1c1"]
    assert record["verdict"] == "confirmed_blank" and record["actor_id"] == str(actor.id)
    assert record["revision_id"] == revision and record["decided_at"]
    assert record["source_references"][0]["page"] == 2
    assert "r1c1" in response.json()["checked_cell_ids"]
    changed = edit(
        client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "—"}, base=revision
    ).json()
    state = client.get(f"/api/v1/revisions/{changed['id']}/review").json()
    assert "r1c1" not in state["cell_reviews"]
    assert "r1c1" not in state["checked_cell_ids"]


def test_unchanged_decision_carries_original_evidence_then_header_edit_invalidates(
    client, owned_table
):
    _, _, table = owned_table
    revision = make_blank(client, table)
    record = verdict(client, revision, ["r1c1"], "confirmed_blank").json()["cell_reviews"]["r1c1"]
    changed = edit(
        client, table, {"kind": "set_text", "cell_id": "r0c0", "text": "new label"}, base=revision
    ).json()
    state = client.get(f"/api/v1/revisions/{changed['id']}/review").json()
    assert state["cell_reviews"]["r1c1"] == record
    headers = edit(
        client,
        table,
        {"kind": "set_headers", "header_rows": [0], "row_label_columns": [0]},
        base=changed["id"],
    ).json()
    state = client.get(f"/api/v1/revisions/{headers['id']}/review").json()
    assert state["cell_reviews"] == {} and state["checked_cell_ids"] == []


def test_legacy_blank_check_does_not_become_confirmation_and_events_survive(
    app, client, owned_table
):
    from uuid import UUID

    from table_review.reviews.models import CellReviewEventRow, ReviewStateRow

    actor, _, table = owned_table
    revision = UUID(make_blank(client, table))
    with app.state.session_factory.begin() as session:
        state = session.get(ReviewStateRow, revision)
        state.checked_cell_ids = ["r0c0", "r1c1"]
        state.cell_reviews = {}
        session.add(
            CellReviewEventRow(
                revision_id=revision, actor_id=actor.id, cell_ids=["r0c0", "r1c1"], checked=True
            )
        )
    state = client.get(f"/api/v1/revisions/{revision}/review").json()
    assert state["checked_cell_ids"] == ["r0c0"] and "r1c1" not in state["cell_reviews"]
    record = state["cell_reviews"]["r0c0"]
    assert (
        record["origin"] == "legacy" and record["actor_id"] is None and record["decided_at"] is None
    )
    history = client.get(f"/api/v1/revisions/{revision}/checked-history").json()["items"]
    assert len(history) == 1 and history[0]["cell_ids"] == ["r0c0", "r1c1"]


@pytest.mark.parametrize("value,cell", [("confirmed_blank", "r0c0"), ("confirmed_value", "r1c1")])
def test_verdict_text_mismatch_is_rejected_atomically(client, owned_table, value, cell):
    _, _, table = owned_table
    revision = make_blank(client, table)
    assert verdict(client, revision, [cell], value).status_code == 422


def test_unreadable_requires_reason_and_blocks_clean_approval(app, client, owned_table):
    actor, _, table = owned_table
    revision = make_blank(client, table)
    app.state.reviews.checker = None
    app.state.reviews.set_checks(actor, revision, (), {"fixture": "ran"})
    assert verdict(client, revision, ["r1c1"], "unreadable", "   ").status_code == 422
    assert (
        verdict(client, revision, ["r1c1"], "unreadable", "Source digits are illegible").status_code
        == 200
    )
    other = [f"r{r}c{c}" for r in range(3) for c in range(3) if (r, c) != (1, 1)]
    assert verdict(client, revision, other, "confirmed_value").status_code == 200
    state = client.get(f"/api/v1/revisions/{revision}/review").json()
    assert state["unreadable_count"] == 1
    assert state["unresolved_count"] == 1
    assert any(i["kind"] == "unreadable_value" for i in state["issues"])
    assert approve(client, revision, warnings=False).status_code == 409
    snapshot = approve(client, revision).json()
    assert snapshot["label"] == "reviewed_with_warnings"
    assert snapshot["cell_reviews"]["r1c1"]["verdict"] == "unreadable"
    assert snapshot["unreadable_count"] == 1
    assert verdict(client, revision, ["r1c1"], "confirmed_blank").status_code == 200
    assert client.get(f"/api/v1/approvals/{snapshot['id']}").json() == snapshot


def test_repeat_verdict_is_noop_and_other_owner_cannot_decide(client, client_b, owned_table):
    _, _, table = owned_table
    revision = table.current_revision_id
    first = verdict(client, revision, ["r0c0"], "confirmed_value")
    assert first.status_code == 200
    assert verdict(client, revision, ["r0c0"], "confirmed_value").json() == first.json()
    assert verdict(client_b, revision, ["r0c0"], "unreadable", "cannot read").status_code == 404
    events = client.get(f"/api/v1/revisions/{revision}/checked-history").json()["items"]
    assert len(events) == 1 and events[0]["cell_reviews"]["r0c0"]["verdict"] == "confirmed_value"
    assert verdict(client, revision, ["r0c0"], "unreviewed").status_code == 200
    state = client.get(f"/api/v1/revisions/{revision}/review").json()
    assert state["checked_cell_ids"] == [] and state["cell_reviews"] == {}


@pytest.mark.parametrize("format", ["workbook", "csv_package"])
def test_unreadable_export_retains_captured_decision_and_never_types_number(
    app, client, owned_table, format
):
    from openpyxl import load_workbook

    from tests.integration.test_exports import finish_requested_export_via_worker

    _, _, table = owned_table
    revision = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "42"}).json()[
        "id"
    ]
    saved = verdict(client, revision, ["r1c1"], "unreadable", "Digit may be damaged")
    assert saved.status_code == 200
    requested = client.post(
        f"/api/v1/tables/{table.id}/exports",
        json={"revision_id": revision, "format": format},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert requested.status_code == 202
    assert verdict(client, revision, ["r1c1"], "confirmed_value").status_code == 200
    record, blob = finish_requested_export_via_worker(app, client, requested.json()["id"])
    captured = record["manifest"]["review"]["cell_reviews"]["r1c1"]
    assert captured["verdict"] == "unreadable" and captured["reason"] == "Digit may be damaged"
    assert len(record["manifest"]["cell_review_events"]) == 1
    if format == "csv_package":
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            cells = json.loads(archive.read("cells.json"))
            target = next(cell for cell in cells if cell["id"] == "r1c1")
            assert target["cell_review"] == captured
            assert target["export_type"] == "text" and target["numeric_value"] is None
            assert b"[unreadable] 42" in archive.read("table.csv")
            assert target["csv_prefix"] == "[unreadable] "
    else:
        wb = load_workbook(io.BytesIO(blob))
        assert wb["Table"]["B2"].value == "42" and wb["Table"]["B2"].data_type == "s"
        source_row = next(
            row for row in wb["Source"].iter_rows(values_only=True) if row[0] == "r1c1"
        )
        assert "unreadable" in source_row[-1]


def test_review_history_paginates_by_payload_budget_and_export_rejects_over_budget(
    app, client, owned_table
):
    _, _, table = owned_table
    revision = table.current_revision_id
    app.state.reviews.history_bytes_limit = 2400
    for reason in ("a" * 900, "b" * 900, "c" * 900):
        assert verdict(client, revision, ["r0c0"], "unreadable", reason).status_code == 200
    first = client.get(f"/api/v1/revisions/{revision}/checked-history").json()
    assert len(first["items"]) == 1 and first["has_more"]
    second = client.get(f"/api/v1/revisions/{revision}/checked-history?offset=1").json()
    assert len(second["items"]) == 1 and second["items"][0]["id"] != first["items"][0]["id"]
    response = client.post(
        f"/api/v1/tables/{table.id}/exports",
        json={"revision_id": str(revision), "format": "csv_package"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 422
    app.state.reviews.history_bytes_limit = 100
    assert client.get(f"/api/v1/revisions/{revision}/checked-history").status_code == 422
