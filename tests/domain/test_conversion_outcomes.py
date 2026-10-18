import hashlib
import json
from types import SimpleNamespace

import pytest

from table_review.extraction.docling_adapter import DoclingExtractor, model_hashes
from table_review.extraction.types import ExtractionRequest, ExtractorConfig
from tests.fixtures.pdf_factory import two_page_report


def extract_with(
    tmp_path,
    monkeypatch,
    statuses,
    tables=(),
    *,
    ocr_pages=(),
    mixed_pages=(),
    ocr_mode="auto",
    transform=None,
):
    source = two_page_report(tmp_path / "report.pdf")
    models = tmp_path / "models"
    models.mkdir()
    (models / "weights.pt").write_bytes(b"weights")

    class Converter:
        def convert(self, path, *, page_range, raises_on_error=True):
            page = page_range[0]
            status = statuses[page]
            if isinstance(status, Exception):
                raise status
            error = SimpleNamespace(
                component_type="model",
                module_name="table_model",
                category="unknown",
                error_message="secret report value /private/report.pdf",
                page_no=page,
            )
            result = SimpleNamespace(
                status=status,
                errors=[error] if status != "success" else [],
                confidence=SimpleNamespace(
                    model_dump=lambda **kw: {
                        "pages": {
                            str(page): {
                                "table_score": 0.7,
                                "ocr_score": float("nan"),
                                "mean_grade": "fair",
                            }
                        },
                    }
                ),
                document=SimpleNamespace(
                    pages={page: SimpleNamespace(size=SimpleNamespace(width=612, height=792))},
                    tables=list(tables),
                ),
            )
            return transform(page, result) if transform else result

    monkeypatch.setattr(DoclingExtractor, "_converter", lambda *args: Converter())
    if ocr_pages or mixed_pages:
        import table_review.extraction.docling_adapter as adapter

        real_page_info = adapter.page_info
        monkeypatch.setattr(
            adapter,
            "page_info",
            lambda path, number: real_page_info(path, number).model_copy(
                update={
                    "requires_ocr": number in ocr_pages,
                    "mixed_image_content": number in mixed_pages,
                }
            ),
        )
    return DoclingExtractor().extract(
        ExtractionRequest(
            source_path=source,
            pages=tuple(statuses),
            config=ExtractorConfig(artifacts_path=models, ocr_mode=ocr_mode),
        )
    )


def test_malformed_ocr_tokens_retain_other_page_outcomes(tmp_path, monkeypatch):
    def malformed(page, result):
        if page == 2:
            result.pages = [
                SimpleNamespace(
                    page_no=2,
                    parsed_page=SimpleNamespace(
                        textline_cells=[SimpleNamespace(from_ocr=True, text="value", rect=None)]
                    ),
                )
            ]
        return result

    result = extract_with(
        tmp_path,
        monkeypatch,
        {1: "success", 2: "success"},
        ocr_pages=(2,),
        transform=malformed,
    )
    assert result.manifest["conversion_outcome"] == "partial_success"
    assert result.manifest["page_outcomes"][0]["adapter_status"] == "success"
    assert result.manifest["page_outcomes"][1]["adapter_status"] == "failure"
    assert result.manifest["ocr_pages"] == [2]


def test_ocr_attempt_is_retained_even_when_parser_fails(tmp_path, monkeypatch):
    result = extract_with(tmp_path, monkeypatch, {2: "failure"}, ocr_pages=(2,))
    assert result.manifest["ocr"]
    assert result.manifest["ocr_pages"] == [2]


def test_empty_recognized_grid_is_rejected(tmp_path, monkeypatch):
    cell = SimpleNamespace(
        bbox=None,
        column_header=False,
        row_header=False,
        row_section=False,
        start_row_offset_idx=0,
        start_col_offset_idx=0,
        row_span=1,
        col_span=1,
        text="   ",
    )
    table = SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=None)],
        data=SimpleNamespace(num_rows=1, num_cols=1, table_cells=[cell]),
    )
    result = extract_with(tmp_path, monkeypatch, {2: "success"}, (table,))
    assert result.tables == ()
    assert result.manifest["conversion_outcome"] == "failure"
    assert result.manifest["page_outcomes"][0]["adapter_exception_type"] == "ValueError"


@pytest.mark.parametrize("ocr", [False, True])
def test_native_literal_reconciliation_is_optional_and_never_applied_to_ocr(
    tmp_path, monkeypatch, ocr
):
    import table_review.extraction.docling_adapter as adapter

    cell = SimpleNamespace(
        bbox=None,
        column_header=False,
        row_header=False,
        row_section=False,
        start_row_offset_idx=0,
        start_col_offset_idx=0,
        row_span=1,
        col_span=1,
        text="123.45",
    )
    table = SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=None)],
        data=SimpleNamespace(num_rows=1, num_cols=1, table_cells=[cell]),
    )

    def refuse(*args):
        raise ValueError("unsupported source evidence")

    monkeypatch.setattr(adapter, "reconcile_native_literals", refuse)

    def parsed(page, result):
        result.pages = []
        return result

    result = extract_with(
        tmp_path,
        monkeypatch,
        {2: "success"},
        (table,),
        ocr_pages=(2,) if ocr else (),
        transform=parsed,
    )
    assert result.tables[0].content.cells[0].text == "123.45"
    outcome = result.manifest["page_outcomes"][0]
    if ocr:
        assert "native_literal_reconciliation" not in outcome
    else:
        assert outcome["native_literal_reconciliation"]["status"] == "failure"
        assert result.manifest["conversion_outcome"] == "partial_success"


def test_missing_ocr_positions_remain_editable_with_coarse_evidence(tmp_path, monkeypatch):
    cell = SimpleNamespace(
        bbox=None,
        column_header=False,
        row_header=False,
        row_section=False,
        start_row_offset_idx=0,
        start_col_offset_idx=0,
        row_span=1,
        col_span=1,
        text="1,234.50",
    )
    table = SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=None)],
        data=SimpleNamespace(num_rows=1, num_cols=2, table_cells=[cell]),
    )

    def parsed(page, result):
        result.pages = []
        return result

    result = extract_with(
        tmp_path, monkeypatch, {2: "success"}, (table,), ocr_pages=(2,), transform=parsed
    )
    cells = result.tables[0].content.cells
    assert len(cells) == 2
    assert cells[0].text == "1,234.50"
    assert cells[1].row == 0 and cells[1].col == 1 and cells[1].text == ""
    assert cells[1].raw_text == "" and cells[1].reference.precision == "page"
    assert any("1 table positions" in warning for warning in result.warnings)


def test_optional_ocr_currency_failure_retains_primary_table_and_parser_outcome(
    tmp_path, monkeypatch
):
    import table_review.extraction.docling_adapter as adapter

    cell = SimpleNamespace(
        bbox=None,
        column_header=False,
        row_header=False,
        row_section=False,
        start_row_offset_idx=0,
        start_col_offset_idx=0,
        row_span=1,
        col_span=1,
        text="123.45",
    )
    table = SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=None)],
        data=SimpleNamespace(num_rows=1, num_cols=1, table_cells=[cell]),
    )

    def refuse(*args):
        raise ValueError("unsupported OCR chunk evidence")

    def parsed(page, result):
        result.pages = []
        return result

    monkeypatch.setattr(adapter, "reconcile_ocr_currency_rows", refuse)
    result = extract_with(
        tmp_path, monkeypatch, {2: "success"}, (table,), ocr_pages=(2,), transform=parsed
    )
    assert result.tables[0].content.cells[0].text == "123.45"
    assert result.manifest["conversion_outcome"] == "partial_success"
    assert result.manifest["page_outcomes"][0]["ocr_currency_reconciliation"]["status"] == "failure"
    assert any("OCR currency reconciliation unavailable" in warning for warning in result.warnings)


@pytest.mark.parametrize("fault", ["render", "recognizer"])
def test_optional_mark_failure_preserves_primary_table(tmp_path, monkeypatch, fault):
    import table_review.extraction.docling_adapter as adapter

    cell = SimpleNamespace(
        bbox=None,
        column_header=False,
        row_header=False,
        row_section=False,
        start_row_offset_idx=0,
        start_col_offset_idx=0,
        row_span=1,
        col_span=1,
        text="123.45",
    )
    table = SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=None)],
        data=SimpleNamespace(num_rows=1, num_cols=2, table_cells=[cell]),
    )

    def refuse(*args):
        raise ValueError("Page render exceeds the pixel budget")

    def parsed(page, result):
        result.pages = []
        return result

    if fault == "render":
        monkeypatch.setattr(adapter, "render_page", refuse)
    else:
        from table_review.documents.types import PageBox
        from table_review.extraction.marks import MarkCandidate

        box = PageBox(page=2, left=10, top=10, right=20, bottom=12, page_width=612, page_height=792)
        monkeypatch.setattr(
            adapter,
            "dash_candidates",
            lambda content, *args, **kwargs: (
                [MarkCandidate(cell_id=content.cells[1].id, box=box, window=box)],
                1,
            ),
        )
        monkeypatch.setattr(adapter, "mark_reader", refuse)
    result = extract_with(
        tmp_path, monkeypatch, {2: "success"}, (table,), ocr_pages=(2,), transform=parsed
    )
    assert len(result.tables) == 1
    assert result.tables[0].content.cells[0].text == "123.45"
    assert result.manifest["page_outcomes"][0]["mark_recovery"]["status"] == "failure"
    assert result.manifest["conversion_outcome"] == "partial_success"


@pytest.mark.parametrize("fault", ["preparation", "converter"])
def test_optional_region_failure_preserves_primary_table(tmp_path, monkeypatch, fault):
    from docling_core.types.doc import BoundingBox

    import table_review.extraction.docling_adapter as adapter
    from tests.domain.test_table_region_candidates import tokens

    bbox = BoundingBox(l=50, t=100, r=200, b=150, coord_origin="TOPLEFT")
    cell = SimpleNamespace(
        bbox=bbox,
        column_header=False,
        row_header=False,
        row_section=False,
        start_row_offset_idx=0,
        start_col_offset_idx=0,
        row_span=1,
        col_span=1,
        text="primary",
    )
    table = SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=bbox)],
        data=SimpleNamespace(num_rows=1, num_cols=1, table_cells=[cell]),
    )

    def parsed(page, result):
        lines = []
        for token in tokens():
            box = BoundingBox(
                l=token.box.left,
                t=token.box.top + 400,
                r=token.box.right,
                b=token.box.bottom + 400,
                coord_origin="TOPLEFT",
            )
            lines.append(
                SimpleNamespace(
                    from_ocr=True,
                    text=token.text,
                    rect=SimpleNamespace(to_bounding_box=lambda box=box: box),
                )
            )
        result.pages = [
            SimpleNamespace(page_no=2, parsed_page=SimpleNamespace(textline_cells=lines))
        ]
        return result

    def malformed(*args):
        raise ValueError("malformed normalized region")

    statuses = {2: "success"}
    if fault == "preparation":
        monkeypatch.setattr(adapter, "_vector_page", malformed)
    else:
        statuses[1] = "failure"
    result = extract_with(
        tmp_path, monkeypatch, statuses, (table,), ocr_pages=(2,), transform=parsed
    )
    assert len(result.tables) == 1
    retry = result.manifest["page_outcomes"][0]["recovery_attempts"][0]
    assert retry["status"] == "failure"
    if fault == "preparation":
        assert retry["exception_type"] == "ValueError"
    assert result.manifest["conversion_outcome"] == "partial_success"
    assert any("region retry failed" in warning for warning in result.warnings)


@pytest.mark.parametrize("status", ["failure", "skipped", "pending", "started"])
def test_unsuccessful_conversion_is_not_a_successful_empty_page(tmp_path, monkeypatch, status):
    result = extract_with(tmp_path, monkeypatch, {2: status})
    assert result.manifest["conversion_outcome"] == "failure"
    outcome = result.manifest["page_outcomes"][0]
    assert outcome["status"] == status and outcome["original_page"] == 2
    assert (
        outcome["errors"][0]["message_sha256"]
        == hashlib.sha256(b"secret report value /private/report.pdf").hexdigest()
    )
    assert "secret report" not in result.model_dump_json()
    assert not any("no tables detected" in warning for warning in result.warnings)


def test_partial_conversion_retains_grades_and_json_safe_unavailable_scores(tmp_path, monkeypatch):
    result = extract_with(tmp_path, monkeypatch, {1: "success", 2: "partial_success"})
    assert result.manifest["conversion_outcome"] == "partial_success"
    scores = result.manifest["page_outcomes"][1]["confidence"]["pages"]["2"]
    assert scores == {"table_score": 0.7, "ocr_score": None, "mean_grade": "fair"}
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    assert any("partial_success" in warning for warning in result.warnings)


def test_parser_exception_retains_safe_page_failure_and_attempts_other_pages(tmp_path, monkeypatch):
    result = extract_with(tmp_path, monkeypatch, {1: RuntimeError("private"), 2: "success"})
    assert result.manifest["conversion_outcome"] == "partial_success"
    assert result.manifest["page_outcomes"][0]["exception_type"] == "RuntimeError"
    assert result.manifest["page_outcomes"][1]["status"] == "success"
    assert "private" not in result.model_dump_json()


def test_adapter_rejection_retains_parser_outcome_and_publishes_no_page_tables(
    tmp_path, monkeypatch
):
    result = extract_with(tmp_path, monkeypatch, {2: "success"}, (SimpleNamespace(prov=[]),))
    assert result.tables == ()
    assert result.manifest["conversion_outcome"] == "failure"
    page = result.manifest["page_outcomes"][0]
    assert page["status"] == "success" and page["adapter_status"] == "failure"
    assert page["adapter_exception_type"] == "ValueError"


def test_model_identity_is_rehashed_when_artifacts_change(tmp_path):
    model = tmp_path / "weights.pt"
    model.write_bytes(b"one")
    first = model_hashes(str(tmp_path))
    model.write_bytes(b"two")
    assert model_hashes(str(tmp_path)) != first


def test_manifest_captures_parser_dependencies_and_code_even_without_runner(tmp_path, monkeypatch):
    result = extract_with(tmp_path, monkeypatch, {2: "success"})
    manifest = result.manifest
    for package in ("docling-parse", "torchvision", "pypdf", "numpy", "transformers"):
        assert manifest["versions"][package]
    assert manifest["adapter_contract_version"]
    assert manifest["environment"]["python"]
    assert manifest["code_revision_kind"] == "installed_python_source_sha256"
    assert manifest["dependency_lock_sha256"]
    assert manifest["page_outcomes"][0]["input_sha256"] == manifest["source_sha256"]


def test_extraction_retains_stage_timings_even_for_failed_conversion(tmp_path, monkeypatch):
    result = extract_with(tmp_path, monkeypatch, {2: "failure"})
    timings = result.manifest["timing"]
    assert timings["schema"] == "extraction_wall_stages_v1"
    for name in (
        "preflight",
        "model_identity",
        "source_identity",
        "page_metadata",
        "converter_setup",
        "conversion",
    ):
        assert timings["stages"][name]["seconds"] >= 0
        assert timings["stages"][name]["count"] >= 1
    assert timings["total_seconds"] >= timings["stages"]["conversion"]["seconds"]
    assert timings["semantics"] == "wall_durations_may_overlap_not_additive"


def test_parser_timing_metadata_is_allowlisted_finite_and_nonfatal(tmp_path, monkeypatch):
    def profile(page, result):
        result.timings = {
            "layout": SimpleNamespace(times=[0.2, 0.3]),
            "table_structure": SimpleNamespace(times=[1e308, 1e308]),
            "ocr": SimpleNamespace(times=None),
            "/private/report-literal": SimpleNamespace(times=[1.0]),
        }
        return result

    result = extract_with(tmp_path, monkeypatch, {2: "success"}, transform=profile)
    assert result.manifest["conversion_outcome"] == "success"
    assert result.manifest["page_outcomes"][0]["parser_timings"] == {
        "layout": {"seconds": 0.5, "count": 2}
    }
    assert "/private/report-literal" not in result.model_dump_json()


@pytest.mark.parametrize("timings", [None, [], {"ocr": SimpleNamespace(times=[10**1000])}])
def test_malformed_optional_timings_never_invalidate_conversion(tmp_path, monkeypatch, timings):
    def profile(page, result):
        result.timings = timings
        return result

    result = extract_with(tmp_path, monkeypatch, {2: "success"}, transform=profile)
    assert result.manifest["conversion_outcome"] == "success"
    assert result.manifest["page_outcomes"][0]["parser_timings"] == {}


@pytest.mark.parametrize("failure", [False, True])
def test_caption_capture_receives_other_table_regions_and_is_optional(
    tmp_path, monkeypatch, failure
):
    from docling_core.types.doc import BoundingBox

    import table_review.extraction.docling_adapter as adapter

    seen = []

    def capture(region, tokens, *, excluded_regions):
        seen.append((region, excluded_regions))
        if failure:
            raise ValueError("private caption text must not be logged")
        return ()

    monkeypatch.setattr(adapter, "caption_sources", capture)
    tables = []
    for top in (100, 160):
        box = BoundingBox(l=50, t=top, r=200, b=top + 40, coord_origin="TOPLEFT")
        cell = SimpleNamespace(
            bbox=box,
            column_header=False,
            row_header=False,
            row_section=False,
            start_row_offset_idx=0,
            start_col_offset_idx=0,
            row_span=1,
            col_span=1,
            text="123",
        )
        tables.append(
            SimpleNamespace(
                prov=[SimpleNamespace(page_no=2, bbox=box)],
                data=SimpleNamespace(num_rows=1, num_cols=1, table_cells=[cell]),
            )
        )
    result = extract_with(tmp_path, monkeypatch, {2: "success"}, tuple(tables))
    assert len(result.tables) == 2
    assert seen and len(seen[0][1]) == 1 and seen[0][0] != seen[0][1][0]
    if failure:
        assert any("caption context unavailable" in warning for warning in result.warnings)
        assert "private caption" not in result.model_dump_json()
