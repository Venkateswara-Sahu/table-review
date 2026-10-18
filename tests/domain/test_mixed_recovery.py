from types import SimpleNamespace

from table_review.extraction.docling_adapter import DoclingExtractor
from tests.domain.test_conversion_outcomes import extract_with


def primary_table():
    return SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=None)],
        data=SimpleNamespace(
            num_rows=1,
            num_cols=1,
            table_cells=[
                SimpleNamespace(
                    bbox=None,
                    column_header=False,
                    row_header=False,
                    row_section=False,
                    start_row_offset_idx=0,
                    start_col_offset_idx=0,
                    row_span=1,
                    col_span=1,
                    text="Retained native literal",
                )
            ],
        ),
    )


def test_failed_optional_image_pass_retains_the_native_table_and_failure_record(
    tmp_path, monkeypatch
):
    def fail(*args):
        raise RuntimeError("private parser diagnostic")

    monkeypatch.setattr(DoclingExtractor, "_recover_mixed", fail, raising=False)
    result = extract_with(
        tmp_path, monkeypatch, {2: "success"}, (primary_table(),), mixed_pages=(2,)
    )
    assert result.tables[0].content.cells[0].raw_text == "Retained native literal"
    assert result.manifest["conversion_outcome"] == "partial_success"
    assert any("mixed image recovery failed" in warning for warning in result.warnings)
    attempt = result.manifest["page_outcomes"][0]["recovery_attempts"][0]
    assert attempt["status"] == "failure" and attempt["exception_type"] == "RuntimeError"
    assert "private parser diagnostic" not in str(result.manifest)


def test_successful_image_attempt_is_recorded_even_when_no_extra_table_is_found(
    tmp_path, monkeypatch
):
    def empty(*args):
        return (
            [],
            [],
            [{"stage": "mixed_page_ocr", "status": "success", "adapter_status": "success"}],
            [],
        )

    monkeypatch.setattr(DoclingExtractor, "_recover_mixed", empty, raising=False)
    result = extract_with(tmp_path, monkeypatch, {2: "success"}, mixed_pages=(2,))
    assert result.manifest["ocr_pages"] == [2]
    assert result.manifest["page_outcomes"][0]["recovery_attempts"][0]["stage"] == "mixed_page_ocr"


def test_digital_only_mode_never_starts_the_image_pass(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("OCR disabled")

    monkeypatch.setattr(DoclingExtractor, "_recover_mixed", forbidden, raising=False)
    result = extract_with(tmp_path, monkeypatch, {2: "success"}, mixed_pages=(2,), ocr_mode="never")
    assert result.manifest["ocr_pages"] == []
    assert result.manifest["conversion_outcome"] == "success"


def test_zero_sized_native_image_grid_does_not_prevent_the_ocr_attempt(tmp_path, monkeypatch):
    empty = SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=None)],
        data=SimpleNamespace(num_rows=0, num_cols=0, table_cells=[]),
    )

    def empty_recovery(*args):
        return (
            [],
            [],
            [{"stage": "mixed_page_ocr", "status": "success", "adapter_status": "success"}],
            [],
        )

    monkeypatch.setattr(DoclingExtractor, "_recover_mixed", empty_recovery, raising=False)
    result = extract_with(tmp_path, monkeypatch, {2: "success"}, (empty,), mixed_pages=(2,))
    assert result.manifest["ocr_pages"] == [2]
    assert result.manifest["page_outcomes"][0]["adapter_status"] == "success"
    assert not result.tables


def test_image_pass_keeps_native_content_and_accepts_only_nonoverlapping_tables(
    tmp_path, monkeypatch
):
    import table_review.extraction.docling_adapter as adapter
    from table_review.documents.pdf import page_info
    from table_review.documents.types import PageBox, SourceReference
    from table_review.extraction.types import ExtractedTable, ExtractionRequest
    from table_review.tables.types import CellContent, TableContent
    from tests.fixtures.pdf_factory import two_page_report

    source = two_page_report(tmp_path / "report.pdf")

    def table(identity, top):
        region = PageBox(
            page=2,
            left=20,
            right=400,
            top=top,
            bottom=top + 80,
            page_width=612,
            page_height=792,
        )
        reference = SourceReference(page=2, precision="table", box=region)
        return ExtractedTable(
            id=identity,
            page_no=2,
            region=region,
            source_tokens=(),
            content=TableContent(
                rows=3,
                columns=3,
                source_reference=reference,
                cells=tuple(
                    CellContent(
                        id=f"{identity}:{r}:{c}",
                        row=r,
                        col=c,
                        text="10",
                        raw_text="10",
                        reference=reference,
                    )
                    for r in range(3)
                    for c in range(3)
                ),
            ),
        )

    native, duplicate, added, overlap = (
        table("native", 100),
        table("duplicate", 100),
        table("added", 300),
        table("overlap", 310),
    )
    calls = []
    monkeypatch.setattr(DoclingExtractor, "_converter", lambda *args: object())

    def convert(*args):
        calls.append(args)
        return object(), {"status": "success", "errors": [], "input_sha256": "fixture"}

    monkeypatch.setattr(DoclingExtractor, "_convert", staticmethod(convert))
    monkeypatch.setattr(adapter, "ocr_tokens", lambda *args: ((), ()))
    monkeypatch.setattr(
        DoclingExtractor,
        "_adapt_page",
        staticmethod(lambda *args, **kwargs: ([duplicate, added, overlap], [])),
    )
    result, _, attempts, transforms = DoclingExtractor()._recover_mixed(
        ExtractionRequest(source_path=source, pages=(2,)), page_info(source, 2), [native], "hash"
    )
    assert [t.id for t in result] == ["added"]
    assert native.content.cells[0].raw_text == "10"
    assert attempts[0]["accepted_table_ids"] == ["added"]
    assert len(calls) == 1 and len(transforms) == 1
    assert transforms[0]["original_page"] == 2
