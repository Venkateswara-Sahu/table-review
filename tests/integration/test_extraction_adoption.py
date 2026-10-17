# ruff: noqa: F811
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import update

from table_review.jobs.models import TaskRow
from table_review.tables.types import TableContent
from tests.integration.test_jobs import extracted_outcome
from tests.integration.test_review_api import edit, owned_table  # noqa: F401


@pytest.fixture(autouse=True)
def stop_tasks(app):
    with app.state.session_factory.begin() as session:
        session.execute(
            update(TaskRow)
            .where(TaskRow.state.in_(("queued", "running")))
            .values(state="cancelled", lease_token=None)
        )


@pytest.fixture
def adoption_pair(app, owner, owned_table):
    _actor, doc, _ = owned_table
    with app.state.session_factory.begin() as session:
        session.execute(update(TaskRow).where(TaskRow.state == "queued").values(state="cancelled"))
    outcome = extracted_outcome()
    content = TableContent.model_validate(outcome.extraction["tables"][0]["content"])
    target = app.state.repository.create_table(
        owner.id, doc.id, content, extraction_manifest={"fixture": "earlier"}
    )
    task = app.state.queue.enqueue(owner.id, doc.id, "extract", {"pages": [2]}, uuid4().hex)
    lease = app.state.queue.claim(datetime.now(UTC))
    assert lease.task_id == task.id
    outcome = extracted_outcome(literal="200")
    extraction = {
        **outcome.extraction,
        "manifest": {**outcome.extraction["manifest"], "source_sha256": doc.source_sha256},
    }
    assert app.state.queue.complete(lease, outcome.model_copy(update={"extraction": extraction}))
    result = app.state.queue.get(owner.id, task.id)
    candidate = app.state.repository.get_table(owner.id, result.result["table_ids"][0])
    return target, candidate


def preview(client, target, candidate, offset=0):
    return client.get(
        f"/api/v1/tables/{target.id}/adoption-preview",
        params={"candidate_table_id": str(candidate.id), "offset": offset},
    )


def adopt(client, target, data, key=None, **changes):
    fields = (
        "base_revision_id",
        "candidate_table_id",
        "candidate_revision_id",
        "target_visibility_version",
        "candidate_visibility_version",
    )
    payload = {key: data[key] for key in fields}
    return client.post(
        f"/api/v1/tables/{target.id}/adopt",
        json={**payload, "confirmed_same_table": True, "confirmed_replacement": True, **changes},
        headers={"Idempotency-Key": str(key or uuid4())},
    )


def test_explicit_adoption_retains_corrections_and_original_and_uses_candidate_source(
    client, app, owner, adoption_pair
):
    target, candidate = adoption_pair
    corrected = edit(client, target, {"kind": "set_text", "cell_id": "r0c0", "text": "150"}).json()
    assert (
        client.post(
            f"/api/v1/revisions/{corrected['id']}/checked",
            json={"cell_ids": ["r0c0"], "checked": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/revisions/{corrected['id']}/approval", json={"allow_warnings": True}
        ).status_code
        == 200
    )
    response = preview(client, target, candidate)
    assert response.status_code == 200
    data = response.json()
    assert data["base_revision_id"] == corrected["id"]
    assert data["changed_cell_count"] == 1
    key = uuid4()
    saved = adopt(client, target, data, key)
    assert saved.status_code == 200
    saved = saved.json()
    assert saved["parent_revision_id"] == corrected["id"]
    assert saved["content"]["cells"][0]["text"] == "200"
    assert saved["content"]["cells"][0]["id"] != "r0c0"
    assert saved["content"]["context_bindings"] == []
    assert saved["content"]["subtotal_groups"] == []
    assert adopt(client, target, data, key).json()["id"] == saved["id"]
    table = client.get(f"/api/v1/tables/{target.id}").json()["table"]
    assert table["original_revision_id"] == str(target.original_revision_id)
    assert (
        client.get(f"/api/v1/tables/{target.id}/revisions/{corrected['id']}").json()["content"][
            "cells"
        ][0]["text"]
        == "150"
    )
    state = client.get(f"/api/v1/revisions/{saved['id']}/review").json()
    assert state["checked_cell_ids"] == [] and state["issue_decisions"] == {}
    assert state["current_approval"] is None and state["check_versions"]["coverage"] == "1"
    assert saved["extraction_manifest"]["adoption"]["candidate_revision_id"] == str(
        candidate.original_revision_id
    )
    events = client.get(f"/api/v1/revisions/{saved['id']}/timeline").json()["items"]
    assert events[-1]["details"]["operation"]["kind"] == "adopt_extraction"


def test_owner_confirmation_and_stale_draft_guards(client, client_b, adoption_pair):
    target, candidate = adoption_pair
    data = preview(client, target, candidate).json()
    assert preview(client_b, target, candidate).status_code == 404
    assert adopt(client_b, target, data).status_code == 404
    assert preview(client, target, target).status_code == 422
    assert adopt(client, target, data, confirmed_same_table=False).status_code == 422
    assert adopt(client, target, data, confirmed_replacement=False).status_code == 422
    assert adopt(client, target, data, candidate_revision_id=str(uuid4())).status_code == 409
    changed = edit(client, target, {"kind": "set_text", "cell_id": "r0c0", "text": "175"}).json()
    assert adopt(client, target, data).status_code == 409
    assert client.get(f"/api/v1/tables/{target.id}").json()["revision"]["id"] == changed["id"]


@pytest.mark.parametrize("which", ["target", "candidate"])
def test_remove_restore_cycle_invalidates_preview_even_when_revision_is_unchanged(
    client, adoption_pair, which
):
    target, candidate = adoption_pair
    data = preview(client, target, candidate).json()
    table = target if which == "target" else candidate
    for removed, version in ((True, 0), (False, 1)):
        response = client.post(
            f"/api/v1/tables/{table.id}/removal",
            json={
                "removed": removed,
                "expected_version": version,
                "base_revision_id": str(table.current_revision_id),
            },
        )
        assert response.status_code == 200
        if removed:
            assert preview(client, target, candidate).status_code == 404
            assert adopt(client, target, data).status_code == 404
    assert adopt(client, target, data).status_code == 409


def test_original_candidate_is_pinned_and_restore_recovers_its_own_source(client, adoption_pair):
    target, candidate = adoption_pair
    edit(client, candidate, {"kind": "set_text", "cell_id": "r0c0", "text": "250"})
    data = preview(client, target, candidate).json()
    saved = adopt(client, target, data).json()
    assert saved["content"]["cells"][0]["text"] == "200"
    changed = edit(
        client,
        target,
        {"kind": "set_text", "cell_id": saved["content"]["cells"][0]["id"], "text": "201"},
        base=saved["id"],
    ).json()
    assert (
        client.get(f"/api/v1/revisions/{changed['id']}/review").json()["check_versions"]["coverage"]
        == "1"
    )
    restored = client.post(
        f"/api/v1/tables/{target.id}/restore",
        json={
            "base_revision_id": changed["id"],
            "historical_revision_id": str(target.original_revision_id),
        },
        headers={"Idempotency-Key": str(uuid4())},
    ).json()
    assert restored["content"]["cells"][0]["text"] == "100"
    assert "adoption" not in restored["extraction_manifest"]
    assert (
        client.get(f"/api/v1/revisions/{restored['id']}/review").json()["check_versions"][
            "coverage"
        ]
        == "not_run"
    )
    again = client.post(
        f"/api/v1/tables/{target.id}/restore",
        json={"base_revision_id": restored["id"], "historical_revision_id": saved["id"]},
        headers={"Idempotency-Key": str(uuid4())},
    ).json()
    assert again["extraction_manifest"]["adoption"]["candidate_revision_id"] == str(
        candidate.original_revision_id
    )
    assert (
        client.get(f"/api/v1/revisions/{again['id']}/review").json()["check_versions"]["coverage"]
        == "1"
    )


def test_changed_structure_drops_old_context_and_subtotals_without_rewriting_them(
    client, app, owner, adoption_pair
):
    from tests.domain.test_subtotals import definition
    from tests.fixtures.subtotals import subtotal_table

    target, candidate = adoption_pair
    current = app.state.repository.get_revision(owner.id, target.id, target.original_revision_id)
    content = subtotal_table()
    content = content.model_copy(
        update={
            "subtotal_groups": (definition(content),),
            "source_reference": current.content.source_reference,
        }
    )
    target = app.state.repository.create_table(
        owner.id, target.document_id, content, extraction_manifest={"fixture": "scope"}
    )
    data = preview(client, target, candidate).json()
    assert data["current_context_confirmations"] == 9 and data["current_subtotal_declarations"] == 1
    saved = adopt(client, target, data).json()
    assert saved["content"]["rows"] == 1 and saved["content"]["columns"] == 1
    assert saved["content"]["context_bindings"] == saved["content"]["subtotal_groups"] == []
    old = client.get(f"/api/v1/tables/{target.id}/revisions/{target.original_revision_id}").json()
    assert len(old["content"]["context_bindings"]) == 9
    assert len(old["content"]["subtotal_groups"]) == 1


def test_deleted_parent_and_cross_report_and_disjoint_sources_are_rejected(
    client, app, owner, adoption_pair, tmp_path
):
    import io

    from table_review.accounts.types import Actor
    from tests.fixtures.pdf_factory import two_page_report

    target, candidate = adoption_pair
    original = app.state.repository.get_revision(owner.id, target.id, target.original_revision_id)
    reference = original.content.source_reference
    disjoint = original.content.model_copy(
        update={
            "source_reference": reference.model_copy(
                update={"box": reference.box.model_copy(update={"left": 200, "right": 300})}
            )
        }
    )
    other = app.state.repository.create_table(
        owner.id, target.document_id, disjoint, extraction_manifest={}
    )
    assert preview(client, other, candidate).status_code == 422
    source = two_page_report(tmp_path / "other.pdf")
    doc = app.state.documents.upload(
        Actor(id=owner.id), io.BytesIO(source.read_bytes()), "other.pdf", uuid4().hex
    )
    other = app.state.repository.create_table(
        owner.id, doc.id, original.content, extraction_manifest={}
    )
    assert preview(client, other, candidate).status_code == 404
    data = preview(client, target, candidate).json()
    assert client.delete(f"/api/v1/documents/{target.document_id}").status_code == 200
    assert preview(client, target, candidate).status_code == 404
    assert adopt(client, target, data).status_code == 404


def test_concurrent_identical_adoption_creates_one_revision(client, app, owner, adoption_pair):
    from concurrent.futures import ThreadPoolExecutor

    from table_review.accounts.types import Actor
    from table_review.tables.adoption import AdoptInput

    target, candidate = adoption_pair
    data = preview(client, target, candidate).json()
    payload = AdoptInput(
        **{
            name: data[name]
            for name in AdoptInput.model_fields
            if name not in ("confirmed_same_table", "confirmed_replacement")
        },
        confirmed_same_table=True,
        confirmed_replacement=True,
    )
    count = app.state.repository.revision_count(target.id)
    key = uuid4()
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(app.state.adoption.adopt, Actor(id=owner.id), target.id, payload, key)
            for _ in range(2)
        ]
        revisions = [future.result(timeout=15) for future in futures]
    assert revisions[0].id == revisions[1].id
    assert app.state.repository.revision_count(target.id) == count + 1


def test_coarse_source_with_a_box_cannot_authorize_replacement(client, app, owner, adoption_pair):
    target, candidate = adoption_pair
    original = app.state.repository.get_revision(owner.id, target.id, target.original_revision_id)
    content = original.content.model_copy(
        update={
            "source_reference": original.content.source_reference.model_copy(
                update={"precision": "page"}
            )
        }
    )
    coarse = app.state.repository.create_table(
        owner.id, target.document_id, content, extraction_manifest={}
    )
    assert preview(client, coarse, candidate).status_code == 422


def test_preview_bounds_metadata_even_without_changed_cells(client, app, owner, adoption_pair):
    _, candidate = adoption_pair
    original = app.state.repository.get_revision(
        owner.id, candidate.id, candidate.original_revision_id
    )
    content = original.content.model_copy(update={"unit_notes": ("x" * (300 * 1024),)})
    target = app.state.repository.create_table(
        owner.id, candidate.document_id, content, extraction_manifest={}
    )
    assert preview(client, target, candidate).status_code == 422
    assert preview(client, target, candidate, offset=1000).status_code == 422


def test_adoption_resets_a_dismissed_warning_even_if_its_source_fingerprint_is_unchanged(
    client, app, owner, adoption_pair
):
    from table_review.checks.engine import evaluate_checks
    from table_review.checks.types import CheckContext

    target, candidate = adoption_pair
    reference = app.state.repository.get_revision(
        owner.id, target.id, target.original_revision_id
    ).content.source_reference

    def unavailable_source(session, revision):
        issues = evaluate_checks(
            TableContent.model_validate(revision.content),
            CheckContext(source_tokens=(), source_regions=(reference.box,), source_available=False),
        )
        return [item.model_dump(mode="json") for item in issues], {"coverage": "not_run"}

    app.state.reviews.checker = unavailable_source
    state = client.get(f"/api/v1/revisions/{target.current_revision_id}/review").json()
    warning = next(item for item in state["issues"] if item["kind"] == "coverage_unavailable")
    assert warning["affected_cell_ids"] == []
    decision = client.post(
        f"/api/v1/revisions/{target.current_revision_id}/issues/{warning['id']}/decision",
        json={"kind": "dismiss", "reason": "Earlier source inspected"},
    )
    assert decision.status_code == 200
    saved = adopt(client, target, preview(client, target, candidate).json()).json()
    state = client.get(f"/api/v1/revisions/{saved['id']}/review").json()
    assert any(item["fingerprint"] == warning["fingerprint"] for item in state["issues"])
    assert state["issue_decisions"] == {}


def test_waiting_adoption_rechecks_candidate_visibility_under_publication_lock(
    client, app, owner, adoption_pair, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread

    from table_review.accounts.types import Actor
    from table_review.storage.repository import NotFound
    from table_review.tables.adoption import AdoptInput

    target, candidate = adoption_pair
    data = preview(client, target, candidate).json()
    payload = AdoptInput(
        **{
            name: data[name]
            for name in AdoptInput.model_fields
            if name not in ("confirmed_same_table", "confirmed_replacement")
        },
        confirmed_same_table=True,
        confirmed_replacement=True,
    )
    waiting, release = Event(), Event()
    lock = app.state.repository._lock_document

    def pause(session, actor, document):
        if current_thread().name.startswith("waiting-adoption"):
            waiting.set()
            assert release.wait(15)
        return lock(session, actor, document)

    monkeypatch.setattr(app.state.repository, "_lock_document", pause)
    with ThreadPoolExecutor(1, thread_name_prefix="waiting-adoption") as pool:
        future = pool.submit(
            app.state.adoption.adopt, Actor(id=owner.id), target.id, payload, uuid4()
        )
        try:
            assert waiting.wait(10)
            assert (
                client.post(
                    f"/api/v1/tables/{candidate.id}/removal",
                    json={
                        "base_revision_id": str(candidate.current_revision_id),
                        "expected_version": 0,
                        "removed": True,
                    },
                ).status_code
                == 200
            )
        finally:
            release.set()
        with pytest.raises(NotFound):
            future.result(timeout=10)
    assert client.get(f"/api/v1/tables/{target.id}").json()["revision"]["id"] == str(
        target.current_revision_id
    )


def test_captured_exports_retain_old_values_and_adopted_source_manifest(client, app, adoption_pair):
    import io
    import json
    import zipfile

    from openpyxl import load_workbook

    from tests.integration.test_exports import finish_requested_export_via_worker

    target, candidate = adoption_pair
    old = client.post(
        f"/api/v1/tables/{target.id}/exports",
        json={"revision_id": str(target.current_revision_id), "format": "workbook"},
        headers={"Idempotency-Key": uuid4().hex},
    ).json()
    saved = adopt(client, target, preview(client, target, candidate).json()).json()
    new = client.post(
        f"/api/v1/tables/{target.id}/exports",
        json={"revision_id": saved["id"], "format": "csv_package"},
        headers={"Idempotency-Key": uuid4().hex},
    ).json()
    _, blob = finish_requested_export_via_worker(app, client, old["id"])
    assert load_workbook(io.BytesIO(blob))["Table"]["A1"].value == 100
    record, package = finish_requested_export_via_worker(app, client, new["id"])
    assert record["manifest"]["source"]["extraction"]["adoption"]["candidate_revision_id"] == str(
        candidate.original_revision_id
    )
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("cells.json"))[0]["text"] == "200"
        assert any(
            item["details"].get("operation", {}).get("kind") == "adopt_extraction"
            for item in json.loads(archive.read("timeline.json"))["events"]
        )
    assert client.get(f"/api/v1/exports/{old['id']}/download").content == blob
