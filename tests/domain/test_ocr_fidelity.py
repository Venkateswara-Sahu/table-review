from PIL import Image, ImageDraw, ImageFont

from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.types import SourceToken
from table_review.tables.types import CellContent, TableContent


def fixture(*, raised=True):
    image = Image.new("RGB", (480, 60), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=24)
    text = "Earnings per share"
    draw.text((8, 22), text, fill="black", font=font)
    x = 8 + draw.textlength(text, font=font) + 2
    draw.text(
        (x, 8 if raised else 22),
        "2",
        fill="black",
        font=ImageFont.load_default(size=14) if raised else font,
    )
    draw.line((0, 4, 479, 4), fill="black", width=2)
    bounds = PageBox(page=1, left=0, top=0, right=480, bottom=60, page_width=480, page_height=60)
    ref = SourceReference(page=1, precision="cell", box=bounds)
    cell = CellContent(
        id="label",
        row=0,
        col=0,
        role="row_header",
        text="Earnings per share2",
        raw_text="Earnings per share2",
        reference=ref,
    )
    return image, TableContent(rows=1, columns=1, cells=(cell,)), ref


def test_only_visibly_raised_label_suffix_is_separated_and_raw_text_is_preserved():
    from table_review.extraction.ocr_fidelity import ground_ocr_content

    image, content, _ = fixture()
    updated, changes, _ = ground_ocr_content(content, (), image, 1)
    assert updated.cells[0].text == "Earnings per share 2"
    assert updated.cells[0].raw_text == "Earnings per share2"
    assert changes[0]["reason"] == "image_raised_suffix_spacing"


def test_same_baseline_digit_is_not_treated_as_a_footnote():
    from table_review.extraction.ocr_fidelity import ground_ocr_content

    image, content, _ = fixture(raised=False)
    updated, changes, _ = ground_ocr_content(content, (), image, 1)
    assert updated.cells[0].text == content.cells[0].text
    assert not any(c["reason"] == "image_raised_suffix_spacing" for c in changes)


def test_currency_chunk_geometry_is_split_only_at_visible_whitespace():
    from table_review.extraction.ocr_fidelity import currency_pieces

    image = Image.new("RGB", (300, 45), "white")
    draw = ImageDraw.Draw(image)
    draw.text((8, 10), "1,234", fill="black", font=ImageFont.load_default(size=22))
    draw.text((250, 10), "$", fill="black", font=ImageFont.load_default(size=22))
    box = PageBox(page=1, left=0, right=280, top=0, bottom=40, page_width=300, page_height=45)
    token = SourceToken(
        id="line",
        text="1,234 $",
        box=box,
        provenance=SourceReference(page=1, precision="cell", box=box),
    )
    pieces = currency_pieces(token, image, 1)
    assert [p.text for p in pieces] == ["1,234", "$"]
    assert pieces[0].box.right < 100 and pieces[1].box.left > 240
    # Original OCR evidence is immutable; the split has separate derived identities.
    assert token.box == box and token.text == "1,234 $"
    assert all(p.id.startswith("line:image-piece:") for p in pieces)


def test_ambiguous_currency_pixels_do_not_produce_precise_parts():
    from table_review.extraction.ocr_fidelity import currency_pieces

    image = Image.new("RGB", (300, 45), "white")
    box = PageBox(page=1, left=0, right=280, top=0, bottom=40, page_width=300, page_height=45)
    token = SourceToken(
        id="empty",
        text="1,234 $",
        box=box,
        provenance=SourceReference(page=1, precision="cell", box=box),
    )
    assert currency_pieces(token, image, 1) == ()


def test_short_cell_boundary_rule_is_excluded_from_digit_evidence():
    from table_review.extraction.ocr_fidelity import ground_ocr_content

    image = Image.new("RGB", (70, 50), "white")
    draw = ImageDraw.Draw(image)
    draw.text((8, 8), "2025", fill="black", font=ImageFont.load_default(size=20))
    draw.line((0, 43, 69, 43), fill="black", width=2)
    box = PageBox(page=1, left=0, right=17.5, top=0, bottom=12.5, page_width=17.5, page_height=12.5)
    cell = CellContent(
        id="year",
        row=0,
        col=0,
        text="2025",
        raw_text="2025",
        reference=SourceReference(page=1, precision="cell", box=box),
    )
    updated, _, _ = ground_ocr_content(TableContent(rows=1, columns=1, cells=(cell,)), (), image, 4)
    assert updated.cells[0].reference.box.bottom < 10
    assert updated.cells[0].reference.box.left > 0


def test_an_isolated_minus_is_not_removed_as_a_table_rule():
    from table_review.extraction.ocr_fidelity import ground_ocr_content

    image = Image.new("RGB", (40, 25), "white")
    ImageDraw.Draw(image).line((0, 12, 39, 12), fill="black", width=2)
    box = PageBox(page=1, left=0, right=10, top=0, bottom=6.25, page_width=10, page_height=6.25)
    cell = CellContent(
        id="dash",
        row=0,
        col=0,
        text="-",
        raw_text="-",
        reference=SourceReference(page=1, precision="cell", box=box),
    )
    updated, changes, _ = ground_ocr_content(
        TableContent(rows=1, columns=1, cells=(cell,)), (), image, 4
    )
    assert updated.cells[0].text == updated.cells[0].raw_text == "-"
    assert changes and updated.cells[0].reference.box.top > 0


def test_regrouped_currency_has_precise_boxes_only_for_separately_visible_pieces():
    from table_review.extraction.ocr_fidelity import ground_ocr_content

    image = Image.new("RGB", (400, 60), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    for x, text in [(8, "$"), (100, "1,234"), (350, "$")]:
        draw.text((x, 15), text, fill="black", font=font)
    region = PageBox(page=1, left=0, right=400, top=0, bottom=60, page_width=400, page_height=60)
    boxes = [region.model_copy(update={"right": 40}), region.model_copy(update={"left": 90})]
    tokens = tuple(
        SourceToken(
            id=str(i), text=text, box=b, provenance=SourceReference(page=1, precision="cell", box=b)
        )
        for i, (text, b) in enumerate(zip(("$", "1,234 $"), boxes))
    )
    cell = CellContent(
        id="amount",
        row=0,
        col=0,
        text="$ 1,234",
        raw_text="1,234 $",
        reference=SourceReference(page=1, precision="table", box=region),
        source_references=tuple(
            t.provenance.model_copy(update={"precision": "table"}) for t in tokens
        ),
    )
    content = TableContent(rows=1, columns=1, cells=(cell,))
    updated, changes, derived = ground_ocr_content(content, tokens, image, 1)
    assert updated.cells[0].raw_text == cell.raw_text and updated.cells[0].text == cell.text
    assert updated.cells[0].reference.precision == "cell"
    assert updated.cells[0].reference.box.right < 200
    assert len(derived) == 2 and all(t.provenance.precision == "cell" for t in derived)
    assert any(c["reason"] == "image_separated_currency_bounds" for c in changes)
    blank = Image.new("RGB", image.size, "white")
    ungrounded, _, derived = ground_ocr_content(content, tokens, blank, 1)
    assert ungrounded == content and not derived
