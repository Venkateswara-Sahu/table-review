import hashlib
from pathlib import Path

import pytest

from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.types import ExtractionRequest


@pytest.fixture(scope="module")
def recovered_report():
    source = Path("data/reports/intel-2023.pdf")
    if not source.exists():
        pytest.skip("Private original report is absent; this is a known-source regression")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "99da402fa6189ae00d2d86151a897f29ef9a96c9a400d4be98aa714917dbc28f"
    )
    return DoclingExtractor().extract(ExtractionRequest(source_path=source, pages=(109,)))


@pytest.mark.extraction
def test_full_page_recovers_both_image_tables_with_original_page_evidence(recovered_report):
    result = recovered_report
    assert len(result.tables) == 2
    cash_flow = next(table for table in result.tables if table.content.columns == 6)
    assert cash_flow.content.rows == 8
    assert any("11,471" in cell.text for cell in cash_flow.content.cells)
    assert all(cell.reference.page == 109 for cell in cash_flow.content.cells)
    amount = next(cell for cell in cash_flow.content.cells if "11,471" in cell.text)
    assert amount.reference.box is not None
    assert 420 < amount.reference.box.top < 450


@pytest.mark.extraction
def test_isolated_source_dash_is_recovered_as_uncertain_text(recovered_report):
    cash_flow = next(table for table in recovered_report.tables if table.content.columns == 6)
    dash = next(cell for cell in cash_flow.content.cells if cell.row == 3 and cell.col == 3)
    assert dash.text in ("—", "–", "-")
    assert dash.interpretation == "uncertain"
    assert dash.reference.precision == "cell" and dash.reference.box is not None
    assert 450 < dash.reference.box.top < 475
