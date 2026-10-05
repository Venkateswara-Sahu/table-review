from uuid import uuid4

import pytest

from tests.integration.test_review_api import edit, owned_table  # noqa: F401


@pytest.fixture
def issue_table(app, owned_table):  # noqa: F811
    from table_review.checks.engine import evaluate_checks
    from table_review.checks.types import CheckContext
    from table_review.documents.types import PageBox, SourceReference
    from table_review.extraction.types import SourceToken
    from table_review.tables.types import TableContent

    actor, doc, table = owned_table
    area = PageBox(
        page=2, left=100, top=100, right=125, bottom=115, page_width=612, page_height=792
    )
    context = CheckContext(
        source_tokens=(
            SourceToken(
                id="natural-source-fixture",
                text="42",
                box=area,
                provenance=SourceReference(page=2, precision="cell", box=area),
            ),
        ),
        source_regions=(area,),
        check_version="decision-fixture-v1",
    )

    def checker(session, revision):
        return (
            [
                i.model_dump(mode="json")
                for i in evaluate_checks(TableContent.model_validate(revision.content), context)
            ],
            {"fixture": context.check_version},
        )

    app.state.reviews.checker = checker
    return actor, doc, table


def decide(
    client,
    table,
    issue,
    kind="dismiss",
    reason="Compared with original page; this is a header",
    base=None,
):
    return client.post(
        f"/api/v1/revisions/{base or table.current_revision_id}/issues/{issue['id']}/decision",
        json={"kind": kind, "reason": reason},
    )


def get_coverage(client, revision):
    state = client.get(f"/api/v1/revisions/{revision}/review").json()
    return state, next(i for i in state["issues"] if i["kind"] == "coverage_discrepancy")


def test_dismissal_persists_actor_reason_and_exact_fingerprint(client, issue_table):
    actor, _, table = issue_table
    _state, issue = get_coverage(client, table.current_revision_id)
    result = decide(client, table, issue)
    assert result.status_code == 200
    decision = result.json()["issue_decisions"][issue["fingerprint"]]
    assert decision["kind"] == "dismiss" and decision["actor_id"] == str(actor.id)
    assert decision["reason"] == "Compared with original page; this is a header"
    assert decision["decided_at"]
    assert (
        client.get(f"/api/v1/revisions/{table.current_revision_id}/review").json()[
            "issue_decisions"
        ]
        == result.json()["issue_decisions"]
    )


def test_decision_validation_owner_scope_and_stale_revision(client, client_b, issue_table):
    _, _, table = issue_table
    _, issue = get_coverage(client, table.current_revision_id)
    assert decide(client_b, table, issue).status_code == 404
    assert decide(client, table, issue, reason="   ").status_code == 422
    assert decide(client, table, {**issue, "id": str(uuid4())}).status_code == 404
    saved = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "unrelated"}).json()
    assert decide(client, table, issue).status_code == 409
    assert decide(client, table, issue, base=saved["id"]).status_code == 200


def test_only_identical_evidence_carries_decision_between_revisions(client, issue_table):
    _, _, table = issue_table
    _, issue = get_coverage(client, table.current_revision_id)
    assert decide(client, table, issue).status_code == 200
    saved = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "unrelated"}).json()
    state, same = get_coverage(client, saved["id"])
    assert same["fingerprint"] == issue["fingerprint"]
    assert same["fingerprint"] in state["issue_decisions"]
    notes = edit(
        client, table, {"kind": "set_notes", "unit_notes": ["USD thousands"]}, base=saved["id"]
    ).json()
    updated, new_issue = get_coverage(client, notes["id"])
    assert new_issue["fingerprint"] != issue["fingerprint"]
    assert new_issue["fingerprint"] not in updated["issue_decisions"]


def test_decision_change_invalidates_approval_and_keeps_prior_snapshot(client, issue_table):
    _, _, table = issue_table
    _state, issue = get_coverage(client, table.current_revision_id)
    cells = client.get(f"/api/v1/tables/{table.id}").json()["revision"]["content"]["cells"]
    client.post(
        f"/api/v1/revisions/{table.current_revision_id}/checked",
        json={"cell_ids": [c["id"] for c in cells], "checked": True},
    )
    snapshot = client.post(
        f"/api/v1/revisions/{table.current_revision_id}/approval", json={"allow_warnings": True}
    ).json()
    assert decide(client, table, issue).json()["current_approval"] is None
    assert client.get(f"/api/v1/approvals/{snapshot['id']}").json() == snapshot
    assert (
        decide(client, table, issue, kind="reopen", reason="Recheck this location").json()[
            "issue_decisions"
        ][issue["fingerprint"]]["kind"]
        == "reopen"
    )


def test_decision_history_is_append_only_and_exact_retries_do_not_duplicate(
    client, app, issue_table
):
    from sqlalchemy import select, update
    from sqlalchemy.exc import DBAPIError

    from table_review.reviews.models import IssueDecisionRow

    _, _, table = issue_table
    _, issue = get_coverage(client, table.current_revision_id)
    assert decide(client, table, issue).status_code == 200
    assert decide(client, table, issue).status_code == 200
    assert decide(client, table, issue, kind="reopen", reason="Recheck source").status_code == 200
    with app.state.session_factory() as session:
        rows = session.scalars(
            select(IssueDecisionRow)
            .where(IssueDecisionRow.revision_id == table.current_revision_id)
            .order_by(IssueDecisionRow.created_at)
        ).all()
        assert [row.decision["kind"] for row in rows] == ["dismiss", "reopen"]
        first = rows[0].id
    with pytest.raises(DBAPIError, match="immutable"), app.state.session_factory.begin() as session:
        session.execute(
            update(IssueDecisionRow)
            .where(IssueDecisionRow.id == first)
            .values(decision={"kind": "resolved"})
        )
