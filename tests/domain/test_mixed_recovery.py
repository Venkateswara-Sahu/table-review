from types import SimpleNamespace

from table_review.extraction.docling_adapter import DoclingExtractor
from tests.domain.test_conversion_outcomes import extract_with


def test_coarse_native_header_and_single_value_are_not_a_second_table():
    from table_review.extraction.mixed import select_mixed_candidates

    native = recovery_table(
        "coarse",
        values=(("Item Value", ""), ("Revenue 100", "")),
        region_known=False,
        header=True,
    )
    candidate = recovery_table(
        "headed",
        values=(("Item", "Value"), ("Revenue", "100")),
        header=True,
    )
    accepted, decisions = select_mixed_candidates([candidate], [native], 2)
    assert not accepted
    assert decisions == [{"table_id": "headed", "decision": "coarse_native_text_overlap"}]


def test_coarse_native_duplicate_is_rejected_when_ocr_splits_the_same_literals():
    from table_review.extraction.mixed import select_mixed_candidates

    native = recovery_table(
        "coarse", values=(("Revenue 100", ""), ("Expense 20", "")), region_known=False
    )
    candidate = recovery_table("split", values=(("Revenue", "100"), ("Expense", "20")))
    accepted, decisions = select_mixed_candidates([candidate], [native], 2)
    assert not accepted
    assert decisions[0]["decision"] == "coarse_native_text_overlap"


def test_coarse_native_contained_rows_are_ambiguous_but_different_amounts_are_new():
    from table_review.extraction.mixed import select_mixed_candidates

    native = recovery_table(
        "coarse",
        values=(("Revenue", "100"), ("Expense", "20"), ("Profit", "80")),
        region_known=False,
    )
    subset = recovery_table("subset", values=(("Revenue", "100"), ("Expense", "20")))
    different = recovery_table("different", values=(("Revenue", "200"), ("Expense", "30")))
    accepted, decisions = select_mixed_candidates([subset, different], [native], 2)
    assert [t.id for t in accepted] == ["different"]
    assert decisions[0]["decision"] == "coarse_native_text_overlap"


def recovery_table(identity, top=300, *, values=None, region_known=True, page=2, header=False):
    from table_review.documents.types import PageBox, SourceReference
    from table_review.extraction.types import ExtractedTable
    from table_review.tables.types import CellContent, TableContent

    values = values or (("Revenue", "100"), ("Expense", "20"))
    region = (
        PageBox(
            page=page,
            left=20,
            right=400,
            top=top,
            bottom=top + 80,
            page_width=612,
            page_height=792,
        )
        if region_known
        else None
    )
    reference = SourceReference(page=page, precision="table" if region else "page", box=region)
    return ExtractedTable(
        id=identity,
        page_no=page,
        region=region,
        source_tokens=(),
        content=TableContent(
            rows=len(values),
            columns=len(values[0]),
            source_reference=reference,
            header_rows=(0,) if header else (),
            cells=tuple(
                CellContent(
                    id=f"{identity}:{row}:{col}",
                    row=row,
                    col=col,
                    text=value,
                    raw_text=value,
                    reference=reference,
                    role="column_header" if header and row == 0 else "data",
                )
                for row, items in enumerate(values)
                for col, value in enumerate(items)
            ),
        ),
    )


def run_mixed_pass(tmp_path, monkeypatch, originals, candidates):
    import table_review.extraction.docling_adapter as adapter
    from table_review.documents.pdf import page_info
    from table_review.extraction.types import ExtractionRequest
    from tests.fixtures.pdf_factory import two_page_report

    source = two_page_report(tmp_path / "mixed.pdf")
    monkeypatch.setattr(DoclingExtractor, "_converter", lambda *args: object())
    monkeypatch.setattr(
        DoclingExtractor,
        "_convert",
        staticmethod(
            lambda *args: (
                object(),
                {
                    "status": "success",
                    "errors": [],
                    "input_sha256": "fixture",
                },
            )
        ),
    )
    monkeypatch.setattr(adapter, "ocr_tokens", lambda *args: ((), ()))
    monkeypatch.setattr(
        DoclingExtractor,
        "_adapt_page",
        staticmethod(lambda *args, **kwargs: (candidates, [])),
    )
    return DoclingExtractor()._recover_mixed(
        ExtractionRequest(source_path=source, pages=(2,)), page_info(source, 2), originals, "hash"
    )


def test_recovers_a_two_by_two_label_value_table(tmp_path, monkeypatch):
    recovered, _, attempts, _ = run_mixed_pass(tmp_path, monkeypatch, [], [recovery_table("small")])
    assert [table.id for table in recovered] == ["small"]
    assert attempts[0]["accepted_table_ids"] == ["small"]


def test_recovers_a_two_by_two_header_and_value_table(tmp_path, monkeypatch):
    candidate = recovery_table(
        "headed", values=(("Item", "Value"), ("Revenue", "100")), header=True
    )
    recovered, _, _, _ = run_mixed_pass(tmp_path, monkeypatch, [], [candidate])
    assert [table.id for table in recovered] == ["headed"]


def test_slight_bbox_overlap_with_distinct_literals_does_not_discard_image_table(
    tmp_path,
    monkeypatch,
):
    native = recovery_table("native", 100)
    image = recovery_table("image", 175, values=(("Assets", "500"), ("Debt", "60")))
    recovered, _, _, _ = run_mixed_pass(tmp_path, monkeypatch, [native], [image])
    assert [table.id for table in recovered] == ["image"]
    assert native.content.cells[0].raw_text == "Revenue"


def test_coarse_native_geometry_does_not_skip_distinct_image_table(tmp_path, monkeypatch):
    native = recovery_table("native", region_known=False)
    image = recovery_table("image", values=(("Assets", "500"), ("Debt", "60")))
    duplicate = recovery_table("native-ocr", 100)
    recovered, _, attempts, transforms = run_mixed_pass(
        tmp_path, monkeypatch, [native], [duplicate, image]
    )
    assert [table.id for table in recovered] == ["image"]
    assert attempts[0]["accepted_table_ids"] == ["image"]
    assert len(transforms) == 1


def test_mixed_pass_rejects_prose_and_other_pages(tmp_path, monkeypatch):
    prose = recovery_table(
        "prose",
        values=(
            ("This report", "shows recent", "results clearly"),
            ("Please review", "the following", "notes carefully"),
            ("All figures", "are preliminary", "and unaudited"),
        ),
    )
    other_page = recovery_table(
        "other-page",
        page=1,
        values=(
            ("Item", "2024", "2025"),
            ("Revenue", "100", "200"),
            ("Expense", "20", "30"),
        ),
    )
    recovered, _, _, _ = run_mixed_pass(tmp_path, monkeypatch, [], [prose, other_page])
    assert recovered == []


def test_substantially_overlapping_distinct_content_stays_for_manual_region_review():
    from table_review.extraction.mixed import select_mixed_candidates

    native = recovery_table("native", 100)
    ambiguous = recovery_table("ambiguous", 110, values=(("Assets", "500"), ("Debt", "60")))
    recovered, decisions = select_mixed_candidates([ambiguous], [native], 2)
    assert recovered == []
    assert decisions == [{"table_id": "ambiguous", "decision": "substantial_overlap"}]


def test_equal_literals_in_separate_regions_are_distinct_source_tables():
    from table_review.extraction.mixed import select_mixed_candidates

    native = recovery_table("native", 100)
    separate = recovery_table("separate", 300)
    recovered, _ = select_mixed_candidates([separate], [native], 2)
    assert [table.id for table in recovered] == ["separate"]


def test_coarse_native_literal_fingerprint_ignores_whitespace_and_case():
    from table_review.extraction.mixed import select_mixed_candidates

    native = recovery_table("native", region_known=False)
    duplicate = recovery_table("duplicate", values=((" REVENUE ", " 100"), ("Expense", "20 ")))
    recovered, decisions = select_mixed_candidates([duplicate], [native], 2)
    assert recovered == []
    assert decisions == [{"table_id": "duplicate", "decision": "duplicate_literals"}]


def test_numeric_only_small_layout_and_unlocated_candidates_need_manual_selection():
    from table_review.extraction.mixed import select_mixed_candidates

    numbers = recovery_table("numbers", values=(("1", "2"), ("3", "4")))
    unlocated = recovery_table("unlocated", region_known=False)
    recovered, _ = select_mixed_candidates([numbers, unlocated], [], 2)
    assert recovered == []


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
