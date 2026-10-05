from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle


def two_page_report(path: Path) -> Path:
    canvas = Canvas(str(path), pagesize=(612, 792), invariant=1)
    canvas.drawString(60, 700, "Table Review fixture cover")
    canvas.showPage()
    canvas.drawString(60, 740, "Annual report: financial table")
    table = Table(
        [
            ["Metric", "2025", "2024"],
            ["Revenue", "1,234.50", "1,100.00"],
            ["Expense", "(123.45)", "(100.00)"],
            ["Total", "1,111.05", "1,000.00"],
        ],
        colWidths=[180, 120, 120],
        rowHeights=[32] * 4,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 12),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    table.wrapOn(canvas, 500, 500)
    table.drawOn(canvas, 60, 540)
    canvas.drawString(60, 520, "USD millions; values are fixture data.")
    canvas.save()
    return path


def rotated_cropped_report(path: Path) -> Path:
    from pypdf import PdfReader, PdfWriter

    original = two_page_report(path.with_name("uncropped.pdf"))
    page = PdfReader(original).pages[1]
    page.cropbox.lower_left = (40, 500)
    page.cropbox.upper_right = (500, 760)
    page.rotate(90)
    writer = PdfWriter()
    writer.add_page(page)
    writer.write(path)
    return path
