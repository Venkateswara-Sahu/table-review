import io
import json
import zipfile
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import select, update

from table_review.documents.types import SourceReference
from table_review.jobs.models import TaskRow
from table_review.jobs.worker import run_once
from table_review.storage.models import DocumentRow
from table_review.tables.types import CellContent, TableContent
from tests.integration.test_review_api import (  # noqa: F401
    approve,
    check_all,
    edit,
    owned_table,
)


@pytest.fixture(autouse=True)
def clear_tasks(app):
    with app.state.session_factory.begin() as session:
        session.execute(
            update(TaskRow)
            .where(TaskRow.state.in_(("queued", "running")))
            .values(state="cancelled", lease_token=None)
        )


def tombstone(app, document_id):
    from datetime import UTC, datetime

    with app.state.session_factory.begin() as session:
        session.execute(
            update(DocumentRow)
            .where(DocumentRow.id == document_id)
            .values(deleted_at=datetime.now(UTC))
        )


def request(client, table, format="workbook", key=None):
    return client.post(
        f"/api/v1/tables/{table.id}/exports",
        json={"revision_id": str(table.current_revision_id), "format": format},
        headers={"Idempotency-Key": key or uuid4().hex},
    )


def finish_requested_export_via_worker(app, client, export_id):
    for _ in range(6):
        assert run_once(app.state.queue, "export-test-worker")
        record = client.get(f"/api/v1/exports/{export_id}")
        if record.json()["state"] in ("succeeded", "failed", "cancelled"):
            break
    assert record.status_code == 200
    assert record.json()["state"] == "succeeded", record.json()
    response = client.get(f"/api/v1/exports/{export_id}/download")
    assert response.status_code == 200
    return record.json(), response.content


def test_export_keeps_requested_approval(app, client, owned_table):  # noqa: F811
    _, _, table = owned_table
    check_all(client, table)
    approval = approve(client, table.current_revision_id).json()
    response = request(client, table)
    assert response.status_code == 202
    export_id = response.json()["id"]
    changed = edit(client, table, {"kind": "set_text", "cell_id": "r1c1", "text": "later edit"})
    assert changed.status_code == 200
    record, blob = finish_requested_export_via_worker(app, client, export_id)
    assert record["manifest"]["revision_id"] == str(table.current_revision_id)
    assert record["manifest"]["approval_id"] == approval["id"]
    assert record["manifest"]["review_status"] == "reviewed_with_warnings"
    assert record["manifest"]["unresolved_count"] == approval["unresolved_count"]
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        names = [
            s.attrib["name"]
            for s in ET.fromstring(archive.read("xl/workbook.xml")).findall("x:sheets/x:sheet", ns)
        ]
        assert names == ["Table", "Source", "Changes", "Manifest", "Context", "Timeline"]
        assert b"later edit" not in archive.read("xl/worksheets/sheet1.xml")
        assert b"1:1" in archive.read("xl/worksheets/sheet1.xml")


def test_retry_owner_download_and_deleted_document(app, client, client_b, owned_table):  # noqa: F811
    _, doc, table = owned_table
    key = uuid4().hex
    first = request(client, table, key=key)
    assert first.status_code == 202
    export_id = first.json()["id"]
    check_all(client, table)
    approve(client, table.current_revision_id)
    assert request(client, table, key=key).json()["id"] == export_id
    assert request(client, table, "csv_package", key).status_code == 409
    assert client_b.get(f"/api/v1/exports/{export_id}").status_code == 404
    assert client_b.get(f"/api/v1/exports/{export_id}/download").status_code == 404
    assert request(client_b, table).status_code == 404
    record, _ = finish_requested_export_via_worker(app, client, export_id)
    assert record["manifest"]["review_status"] == "draft"
    assert record["manifest"]["approval_id"] is None
    tombstone(app, doc.id)
    assert client.get(f"/api/v1/exports/{export_id}/download").status_code == 404


def test_csv_retains_exact_literals_with_escape_metadata(app, client, owned_table):  # noqa: F811
    actor, doc, _ = owned_table
    literals = [
        "=1+1",
        "\t\n＝SUM(A1)",
        "@evil",
        "+42",
        "-42",
        "00123",
        "1234567890123456",
        'a,"b"\nc',
        "12.50%",
        "1.2(a)",
    ]
    content = TableContent(
        rows=len(literals),
        columns=1,
        cells=tuple(
            CellContent(
                id=f"v{i}",
                row=i,
                col=0,
                raw_text=s,
                text=s,
                interpretation="text",
                reference=SourceReference(page=2, precision="page"),
            )
            for i, s in enumerate(literals)
        ),
        unit_notes=("USD millions",),
    )
    table = app.state.repository.create_table(actor.id, doc.id, content, extraction_manifest={})
    response = request(client, table, "csv_package")
    assert response.status_code == 202
    record, blob = finish_requested_export_via_worker(app, client, response.json()["id"])
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert set(archive.namelist()) == {
            "table.csv",
            "manifest.json",
            "cells.json",
            "changes.json",
            "context.json",
            "timeline.json",
        }
        import csv

        rows = list(csv.reader(io.StringIO(archive.read("table.csv").decode("utf-8"))))
        assert [r[0] for r in rows[:5]] == ["[text] " + s for s in literals[:5]]
        assert [r[0] for r in rows[5:]] == literals[5:]
        cells = json.loads(archive.read("cells.json"))
        assert [c["text"] for c in cells] == literals
        assert [c["raw_text"] for c in cells] == literals
        assert sum(c["csv_escaped"] for c in cells) == 5
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["unit_notes"] == ["USD millions"]
        assert manifest["revision_id"] == record["manifest"]["revision_id"]


def test_late_export_does_not_publish_after_deletion(app, client, owned_table):  # noqa: F811
    _, doc, table = owned_table
    response = request(client, table)
    assert response.status_code == 202
    export_id = response.json()["id"]
    from datetime import UTC, datetime

    from table_review.jobs.types import TaskOutcome

    assert run_once(app.state.queue, "prepare-first")
    lease = app.state.queue.claim(datetime.now(UTC))
    assert str(lease.task_id) == export_id
    tombstone(app, doc.id)
    assert not app.state.queue.complete(
        lease, TaskOutcome(status="succeeded", result={"untrusted": True})
    )
    with app.state.session_factory() as session:
        row = session.scalar(select(TaskRow).where(TaskRow.id == export_id))
        assert row.result == {}


def test_workbook_types_spans_and_no_formulas(tmp_path):
    from openpyxl import load_workbook

    from table_review.exports.types import ExportInput
    from table_review.exports.workbook import write_workbook

    literals = [
        "=1+1",
        "００１",
        "00123",
        "1234567890123456",
        "1٢",
        "(0)",
        "1.20",
        "12.50%",
        "1.2(a)",
    ]
    reference = SourceReference(page=2, precision="page")
    content = TableContent(
        rows=2,
        columns=len(literals),
        cells=(
            CellContent(
                id="header",
                row=0,
                col=0,
                col_span=len(literals),
                text="Revenue",
                raw_text="Revenue",
                role="column_header",
                reference=reference,
            ),
        )
        + tuple(
            CellContent(
                id=f"v{i}",
                row=1,
                col=i,
                text=s,
                raw_text=s,
                interpretation="percent" if s.endswith("%") else "en_us_number",
                role="value",
                reference=reference,
            )
            for i, s in enumerate(literals)
        ),
        header_rows=(0,),
        unit_notes=("USD millions",),
    )
    artifact = write_workbook(
        ExportInput(
            content=content, revision_id=uuid4(), source_manifest={"source_sha256": "a" * 64}
        ),
        tmp_path / "types.xlsx",
    )
    wb = load_workbook(artifact.path)
    sheet = wb["Table"]
    assert "A1:I1" in sheet.merged_cells
    assert [sheet.cell(2, i + 1).value for i in range(6)] == literals[:6]
    assert sheet["G2"].value == 1.2 and sheet["G2"].data_type == "n"
    assert sheet["H2"].value == 0.125 and sheet["H2"].number_format == "0.00%"
    for ws in wb:
        assert all(c.data_type != "f" and c.hyperlink is None for row in ws for c in row)
    assert wb["Source"]["E2"].value == "Revenue"


def test_requested_snapshot_cannot_be_mutated_in_database(app, client, owned_table):  # noqa: F811
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _, _, table = owned_table
    export_id = request(client, table).json()["id"]
    with pytest.raises(DBAPIError), app.state.session_factory.begin() as session:
        session.execute(text("UPDATE tasks SET input='{}'::jsonb WHERE id=:id"), {"id": export_id})


def test_export_download_rejects_tampered_bytes(app, client, owned_table):  # noqa: F811
    actor, _, table = owned_table
    export_id = request(client, table).json()["id"]
    finish_requested_export_via_worker(app, client, export_id)
    task = app.state.queue.get(actor.id, __import__("uuid").UUID(export_id))
    path = app.state.blobs._path(actor.id, task.result["blob_key"])
    path.write_bytes(b"private tampered content")
    response = client.get(f"/api/v1/exports/{export_id}/download")
    assert response.status_code == 503
    assert response.json()["code"] == "export_integrity"
    assert b"private tampered" not in response.content


def test_workbook_same_snapshot_generates_same_bytes(tmp_path):
    from table_review.exports.types import ExportInput
    from table_review.exports.workbook import write_workbook
    from tests.fixtures.tables import simple_table

    data = ExportInput(content=simple_table(), revision_id=uuid4())
    first = write_workbook(data, tmp_path / "a.xlsx")
    import time

    time.sleep(2.1)  # ZIP timestamps have two-second resolution.
    second = write_workbook(data, tmp_path / "b.xlsx")
    assert first.sha256 == second.sha256


@pytest.mark.parametrize(
    "literal",
    [
        "\t\n＝SUM(A1)",
        "\r+42",
        "＠external",
        "-0",
        "(0)",
        "00123",
        "1٢",
        '=HYPERLINK("http://example.invalid")',
    ],
)
def test_workbook_literal_text_remains_exact_and_inert(tmp_path, literal):
    from openpyxl import load_workbook

    from table_review.exports.types import ExportInput
    from table_review.exports.workbook import write_workbook

    content = TableContent(
        rows=1,
        columns=1,
        cells=(
            CellContent(
                id="literal",
                row=0,
                col=0,
                raw_text=literal,
                text=literal,
                reference=SourceReference(page=1, precision="page"),
            ),
        ),
    )
    path = write_workbook(
        ExportInput(content=content, revision_id=uuid4()), tmp_path / "literal.xlsx"
    ).path
    workbook = load_workbook(path)
    assert workbook["Table"]["A1"].value == literal
    assert workbook["Table"]["A1"].data_type == "s"
    assert workbook["Source"]["E2"].value == literal
    assert workbook["Source"]["F2"].value == literal
    assert workbook["Source"]["F2"].data_type == "s"
    with zipfile.ZipFile(path) as archive:
        assert all(
            b"<f>" not in archive.read(name)
            for name in archive.namelist()
            if name.startswith("xl/worksheets/")
        )


def test_export_and_edit_serialize_without_deadlock(app, owned_table, monkeypatch):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from table_review.storage.repository import RevisionConflict
    from table_review.tables.grid import parse_edit

    actor, _, table = owned_table
    edit_saved, export_capturing = Event(), Event()
    after_save = app.state.tables._after_save
    context = app.state.reviews._context

    def paused_save(*args):
        edit_saved.set()
        export_capturing.wait(0.5)
        return after_save(*args)

    def observed_context(*args, **kwargs):
        export_capturing.set()
        return context(*args, **kwargs)

    monkeypatch.setattr(app.state.tables, "_after_save", paused_save)
    monkeypatch.setattr(app.state.reviews, "_context", observed_context)
    with ThreadPoolExecutor(2) as pool:
        edited = pool.submit(
            app.state.tables.edit,
            actor,
            table.id,
            table.current_revision_id,
            parse_edit({"kind": "set_text", "cell_id": "r1c1", "text": "42"}),
        )
        assert edit_saved.wait(3)
        exporting = pool.submit(
            app.state.exports.request,
            actor,
            table.id,
            table.current_revision_id,
            "workbook",
            uuid4().hex,
        )
        assert edited.result(timeout=5).content.cells[4].text == "42"
        try:
            exporting.result(timeout=5)
        except RevisionConflict:
            pass  # The edit won. A safe stale-revision conflict is expected.


def test_tampered_source_aborts_export(app, client, owned_table):  # noqa: F811
    actor, doc, table = owned_table
    export_id = request(client, table).json()["id"]
    assert run_once(app.state.queue, "prepare-source")
    app.state.blobs._path(actor.id, doc.blob_key).write_bytes(b"tampered source")
    assert run_once(app.state.queue, "export-source-integrity")
    record = client.get(f"/api/v1/exports/{export_id}").json()
    assert record["state"] == "failed"
    assert record["failure_code"] == "source_changed"
    assert record["sha256"] is None
    assert client.get(f"/api/v1/exports/{export_id}/download").status_code == 409


def test_mismatched_approval_aborts_writer(client, owned_table, tmp_path):  # noqa: F811
    from pydantic import ValidationError

    from table_review.exports.types import ExportInput
    from tests.fixtures.tables import simple_table

    _, _, table = owned_table
    check_all(client, table)
    snapshot = approve(client, table.current_revision_id).json()
    snapshot["content_checksum"] = "0" * 64
    with pytest.raises(ValidationError, match="Approval does not match"):
        ExportInput(
            content=simple_table(), revision_id=table.current_revision_id, review_snapshot=snapshot
        )


@pytest.mark.parametrize(
    "literal,expected_format",
    [
        ("+1,234.50", '"+"#,##0.00;-#,##0.00;"+"#,##0.00'),
        ("(1,234.50)", "#,##0.00;(#,##0.00)"),
        ("(12.50%)", "0.00%;(0.00%)"),
    ],
)
def test_numeric_formats_preserve_authored_signs_and_grouping(tmp_path, literal, expected_format):
    from openpyxl import load_workbook

    from table_review.exports.types import ExportInput
    from table_review.exports.workbook import write_workbook

    content = TableContent(
        rows=1,
        columns=1,
        cells=(
            CellContent(
                id="signed",
                row=0,
                col=0,
                raw_text=literal,
                text=literal,
                interpretation="percent" if "%" in literal else "en_us_number",
                reference=SourceReference(page=1, precision="page"),
            ),
        ),
    )
    path = write_workbook(
        ExportInput(content=content, revision_id=uuid4()), tmp_path / "signed.xlsx"
    ).path
    workbook = load_workbook(path)
    assert workbook["Table"]["A1"].number_format == expected_format
    assert workbook["Source"]["F2"].value == literal
