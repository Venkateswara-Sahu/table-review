from uuid import uuid4

from tests.integration.test_review_api import approve, check_all, edit, owned_table  # noqa: F401


def timeline(client, revision, suffix=""):
    return client.get(f"/api/v1/revisions/{revision}/timeline{suffix}")


def test_timeline_orders_review_decisions_approval_and_edit_with_invalidation(
    client,
    client_b,
    owned_table,  # noqa: F811
):
    _, _, table = owned_table
    state = client.get(f"/api/v1/revisions/{table.current_revision_id}/review").json()
    check_all(client, table)
    issue = state["issues"][0]
    for kind in ("dismiss", "reopen"):
        decision = client.post(
            f"/api/v1/revisions/{table.current_revision_id}/issues/{issue['id']}/decision",
            json={"kind": kind, "reason": "Compared the source wording"},
        )
        assert decision.status_code == 200
    approval = approve(client, table.current_revision_id).json()
    changed = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "42"}).json()
    response = timeline(client, changed["id"])
    assert response.status_code == 200
    assert timeline(client_b, changed["id"]).status_code == 404
    rows = response.json()["items"]
    assert [row["sequence"] for row in rows] == sorted({row["sequence"] for row in rows})
    assert {
        "coverage_start",
        "checks_changed",
        "cell_review",
        "issue_decision",
        "approval",
        "edit",
    } <= {row["kind"] for row in rows}
    entry = next(row for row in rows if row["kind"] == "edit")
    delta = next(item for item in entry["details"]["changes"] if item["cell_id"] == "r1c1")
    assert delta["before"]["text"] == "1:1" and delta["after"]["text"] == "42"
    assert "r1c1" in entry["details"]["invalidated_cell_ids"]
    assert entry["details"]["invalidated_approval_id"] == approval["id"]
    assert entry["actor_id"] and entry["recorded_at"]
    historical = timeline(client, table.current_revision_id).json()
    assert not any(row["kind"] == "edit" for row in historical["items"])


def test_noop_review_and_exact_edit_retry_do_not_duplicate_timeline(client, owned_table):  # noqa: F811
    _, _, table = owned_table
    check_all(client, table)
    first = timeline(client, table.current_revision_id)
    assert first.status_code == 200
    check_all(client, table)
    assert timeline(client, table.current_revision_id).json() == first.json()
    key = uuid4()
    operation = {"kind": "set_text", "cell_id": "r1c1", "text": "42"}
    changed = edit(client, table, operation, key=key).json()
    before = timeline(client, changed["id"]).json()
    assert edit(client, table, operation, key=key).status_code == 200
    assert timeline(client, changed["id"]).json() == before


def test_export_captures_timeline_before_later_review(client, app, owned_table):  # noqa: F811
    from table_review.jobs.models import TaskRow

    _, _, table = owned_table
    check_all(client, table)
    captured = timeline(client, table.current_revision_id)
    assert captured.status_code == 200
    response = client.post(
        f"/api/v1/tables/{table.id}/exports",
        json={"revision_id": str(table.current_revision_id), "format": "csv_package"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202
    client.post(
        f"/api/v1/revisions/{table.current_revision_id}/checked",
        json={"cell_ids": ["r1c1"], "checked": False},
    )
    with app.state.session_factory() as session:
        snapshot = session.get(TaskRow, response.json()["id"]).input["snapshot"]
    assert snapshot["timeline_events"] == captured.json()["items"]
    assert len(timeline(client, table.current_revision_id).json()["items"]) > len(
        snapshot["timeline_events"]
    )


def test_concurrent_reviews_have_unique_publication_sequence(app, client, owned_table):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    actor, _, table = owned_table
    barrier = Barrier(2)

    def check(cell):
        barrier.wait(timeout=5)
        return app.state.reviews.mark_checked(actor, table.current_revision_id, (cell,), True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(check, ("r1c1", "r1c2")))
    rows = timeline(client, table.current_revision_id).json()["items"]
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))
    assert len([row for row in rows if row["kind"] == "cell_review"]) == 2


def test_pages_pin_cutoff_and_do_not_skip_after_later_review(app, client, owned_table):  # noqa: F811
    actor, _, table = owned_table
    for number in range(103):
        app.state.reviews.mark_checked(actor, table.current_revision_id, ("r1c1",), number % 2 == 0)
    first = timeline(client, table.current_revision_id).json()
    assert len(first["items"]) == 100 and first["has_more"]
    app.state.reviews.mark_checked(actor, table.current_revision_id, ("r1c1",), False)
    second = timeline(
        client,
        table.current_revision_id,
        f"?after={first['next_after_sequence']}&through={first['through_sequence']}",
    ).json()
    assert not second["has_more"]
    combined = first["items"] + second["items"]
    assert len({row["sequence"] for row in combined}) == first["through_sequence"]
    assert second["through_sequence"] == first["through_sequence"]


def test_oversize_event_rolls_back_revision_and_preserves_history(client, owned_table, monkeypatch):  # noqa: F811
    import table_review.reviews.timeline as ledger

    _, _, table = owned_table
    check_all(client, table)
    before = timeline(client, table.current_revision_id).json()
    monkeypatch.setattr(ledger, "BUDGET", 8192)
    response = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "x" * 9000})
    assert response.status_code == 422
    assert client.get(f"/api/v1/tables/{table.id}").json()["revision"]["id"] == str(
        table.current_revision_id
    )
    assert timeline(client, table.current_revision_id).json() == before


def test_timeline_is_immutable_and_export_budget_never_drops_events(app, client, owned_table):  # noqa: F811
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _, _, table = owned_table
    check_all(client, table)
    rows = timeline(client, table.current_revision_id).json()["items"]
    with pytest.raises(DBAPIError), app.state.session_factory.begin() as session:
        session.execute(
            text("UPDATE review_timeline_events SET sequence=sequence WHERE id=:id"),
            {"id": rows[0]["id"]},
        )
    app.state.reviews.history_bytes_limit = 512
    response = client.post(
        f"/api/v1/tables/{table.id}/exports",
        json={"revision_id": str(table.current_revision_id), "format": "workbook"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 422


def test_unchanged_approval_retry_returns_the_same_snapshot_and_event(client, owned_table):  # noqa: F811
    _, _, table = owned_table
    check_all(client, table)
    first = approve(client, table.current_revision_id)
    before = timeline(client, table.current_revision_id).json()
    second = approve(client, table.current_revision_id)
    assert first.json()["id"] == second.json()["id"]
    assert timeline(client, table.current_revision_id).json() == before


def test_refresh_preserves_removed_undecided_fingerprints(app, client, owned_table):  # noqa: F811
    actor, _, table = owned_table
    state = client.get(f"/api/v1/revisions/{table.current_revision_id}/review").json()
    old = sorted(issue["fingerprint"] for issue in state["issues"])
    app.state.reviews.set_checks(
        actor, table.current_revision_id, (), {"engine": "1", "fixture": "updated"}
    )
    rows = timeline(client, table.current_revision_id).json()["items"]
    refreshed = [row for row in rows if row["kind"] == "checks_changed"][-1]
    assert refreshed["details"]["before"]["fingerprints"] == old
    assert refreshed["details"]["after"]["fingerprints"] == []


def test_ancestry_is_loaded_in_a_fixed_number_of_queries(app, owned_table):  # noqa: F811
    from sqlalchemy import event

    from table_review.reviews.timeline import read_events
    from table_review.storage.models import RevisionRow
    from table_review.tables.grid import parse_edit

    actor, _, table = owned_table
    revision = table.current_revision_id
    for number in range(12):
        saved = app.state.tables.edit(
            actor,
            table.id,
            revision,
            parse_edit({"kind": "set_text", "cell_id": "r1c1", "text": str(number)}),
        )
        revision = saved.id
    engine = app.state.session_factory.kw["bind"]
    queries = []

    def count(*args):
        queries.append(1)

    with app.state.session_factory() as session:
        current = session.get(RevisionRow, revision)
        event.listen(engine, "before_cursor_execute", count)
        try:
            rows, _, _ = read_events(session, current, page_size=100)
        finally:
            event.remove(engine, "before_cursor_execute", count)
    assert rows and len(queries) <= 6
