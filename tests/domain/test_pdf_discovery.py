import tempfile
from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle

from table_review.documents.discovery import extract_outline, scan_table_candidates, search_document
from table_review.documents.pdf_child import process as pdf_child_process
from tests.fixtures.pdf_factory import two_page_report


def _financial_report_pdf(path: Path) -> Path:
    canvas = Canvas(str(path), pagesize=(612, 792), invariant=1)

    # Page 1: Cover prose + nested TOC bookmark root
    canvas.bookmarkPage("cover")
    canvas.addOutlineEntry("Annual Report Overview", "cover", level=0, closed=False)
    canvas.drawString(60, 720, "Acme Financial Holdings — Annual Filing")
    canvas.drawString(60, 695, "Prepared on January 15, 2026 for management review.")
    canvas.showPage()

    # Page 2: Balance Sheet table
    canvas.bookmarkPage("bs")
    canvas.addOutlineEntry("Consolidated Balance Sheets", "bs", level=1, closed=False)
    canvas.drawString(60, 745, "Consolidated Balance Sheets (in $ millions)")
    bs_table = Table(
        [
            ["Assets", "2025", "2024"],
            ["Cash and cash equivalents", "$450.00", "$380.00"],
            ["Accounts receivable", "$210.00", "$190.00"],
            ["Total assets", "$660.00", "$570.00"],
        ],
        colWidths=[220, 110, 110],
        rowHeights=[28] * 4,
    )
    bs_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 11),
            ]
        )
    )
    bs_table.wrapOn(canvas, 500, 500)
    bs_table.drawOn(canvas, 60, 560)
    canvas.showPage()

    # Page 3: Income Statement table
    canvas.bookmarkPage("is")
    canvas.addOutlineEntry("Statements of Operations", "is", level=1, closed=False)
    canvas.drawString(60, 745, "Consolidated Statements of Operations (in thousands)")
    is_table = Table(
        [
            ["Metric", "FY2025", "FY2024"],
            ["Revenue", "1,500", "1,200"],
            ["Cost of goods", "(600)", "(500)"],
            ["Gross profit", "900", "700"],
            ["Operating expense", "(300)", "(250)"],
            ["Total operating income", "600", "450"],
        ],
        colWidths=[220, 110, 110],
        rowHeights=[26] * 6,
    )
    is_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 11),
            ]
        )
    )
    is_table.wrapOn(canvas, 500, 500)
    is_table.drawOn(canvas, 60, 520)
    canvas.showPage()

    # Page 4: Prose appendix (no tables)
    canvas.bookmarkPage("notes")
    canvas.addOutlineEntry("Accounting Notes", "notes", level=0, closed=False)
    canvas.drawString(60, 720, "Note 1: Significant accounting policies and estimates.")
    canvas.drawString(60, 700, "Management prepares statements in conformity with GAAP.")
    canvas.showPage()

    canvas.save()
    return path


def test_extract_outline_nested_hierarchy_and_empty_pdf(tmp_path):
    report = _financial_report_pdf(tmp_path / "annual.pdf")
    outline = extract_outline(report)
    assert outline.total_count == 4
    assert [item.title for item in outline.items] == ["Annual Report Overview", "Accounting Notes"]
    assert outline.items[0].page_no == 1
    assert [child.title for child in outline.items[0].children] == [
        "Consolidated Balance Sheets",
        "Statements of Operations",
    ]
    assert [child.page_no for child in outline.items[0].children] == [2, 3]
    assert outline.items[1].page_no == 4

    plain = two_page_report(tmp_path / "plain.pdf")
    empty_outline = extract_outline(plain)
    assert empty_outline.items == ()
    assert empty_outline.total_count == 0


def test_search_document_returns_snippets_boxes_and_truncation(tmp_path):
    report = _financial_report_pdf(tmp_path / "search.pdf")
    result = search_document(report, "consolidated", case_sensitive=False)
    assert result.total_matches == 2
    assert not result.truncated
    assert [m.page_no for m in result.matches] == [2, 3]
    for match in result.matches:
        matched_slice = match.snippet[match.match_start : match.match_end]
        assert matched_slice.lower() == "consolidated"
        assert len(match.boxes) >= 1
        assert match.boxes[0].page == match.page_no
        assert match.boxes[0].left < match.boxes[0].right

    limited = search_document(report, "total", limit=1, case_sensitive=False)
    assert limited.total_matches >= 2
    assert limited.truncated
    assert len(limited.matches) == 1

    case_exact = search_document(report, "Consolidated", case_sensitive=True)
    assert case_exact.total_matches == 2
    case_miss = search_document(report, "CONSOLIDATED", case_sensitive=True)
    assert case_miss.total_matches == 0


def test_scan_table_candidates_finds_financial_pages_and_excludes_prose(tmp_path):
    report = _financial_report_pdf(tmp_path / "candidates.pdf")
    scanned = scan_table_candidates(report)
    assert scanned.page_count == 4
    pages = [c.page_no for c in scanned.candidates]
    assert pages == [2, 3]
    p2, p3 = scanned.candidates
    assert "balance_sheet" in p2.signals
    assert "totals" in p2.signals
    assert "currency" in p2.signals
    assert "multi_period" in p2.signals
    assert p2.heading and "Balance Sheets" in p2.heading
    assert "income_statement" in p3.signals
    assert "unit_scale" in p3.signals
    assert p2.score >= 0.5 and p3.score >= 0.5


def test_pdf_child_handles_discovery_kinds():
    with tempfile.TemporaryDirectory() as td:
        report = _financial_report_pdf(Path(td) / "child.pdf")
        outline = pdf_child_process({"kind": "outline", "source_path": str(report)})
        assert outline["total_count"] == 4
        search = pdf_child_process(
            {"kind": "search", "source_path": str(report), "query": "Revenue"}
        )
        assert search["total_matches"] == 1
        assert search["matches"][0]["page_no"] == 3
        candidates = pdf_child_process({"kind": "table_candidates", "source_path": str(report)})
        assert [c["page_no"] for c in candidates["candidates"]] == [2, 3]


def test_pick_heading_skips_garbled_cid_text():
    from table_review.documents.discovery import _pick_heading

    assert _pick_heading(["I M % K J K L N O P", "QWRTY+XCVBN%$#@+"]) is None
    assert (
        _pick_heading(["I M % K J K L N O", "Consolidated Balance Sheets"])
        == "Consolidated Balance Sheets"
    )
