import hashlib
import json

import pytest

from table_review.documents.types import SourceReference
from table_review.tables.types import CellContent, TableContent, content_checksum


def contextual_table():
    reference = SourceReference(page=2, precision="page")
    cells = [
        CellContent(
            id="corner",
            row=0,
            col=0,
            row_span=2,
            text="Metric",
            raw_text="Metric",
            reference=reference,
        )
    ]
    for col, year in ((1, "2024"), (3, "2023")):
        cells.append(
            CellContent(
                id=f"year-{year}",
                row=0,
                col=col,
                col_span=2,
                text=year,
                raw_text=year,
                role="column_header",
                reference=reference,
            )
        )
    for col in range(1, 5):
        text = "Amount" if col % 2 else "% of revenue"
        cells.append(
            CellContent(
                id=f"header-{col}",
                row=1,
                col=col,
                text=text,
                raw_text=text,
                role="column_header",
                reference=reference,
            )
        )
    for row, label in ((2, "Revenue"), (3, "Expense"), (4, "Earnings per share")):
        for col in range(5):
            text = label if col == 0 else "(12.50)" if col % 2 else "24%"
            cells.append(
                CellContent(
                    id=f"r{row}c{col}",
                    row=row,
                    col=col,
                    text=text,
                    raw_text=text,
                    reference=reference,
                )
            )
    return TableContent(
        rows=5,
        columns=5,
        cells=tuple(cells),
        header_rows=(0, 1),
        row_label_columns=(0,),
        source_reference=reference,
    )


def test_nested_header_path_is_canonical_and_does_not_use_other_period():
    from table_review.tables.context import ContextIndex

    context = ContextIndex(contextual_table()).cell("r2c2")
    assert [source.text for source in context.column_headers] == ["2024", "% of revenue"]
    assert [source.text for source in context.row_headers] == ["Revenue"]
    periods = [item for item in context.proposals if item.kind == "period"]
    assert [item.value for item in periods] == ["2024"]
    assert periods[0].source_ids == ("cell:year-2024",)
    assert context.confirmed == ()


def test_later_repeated_headers_never_describe_earlier_values():
    from table_review.tables.context import ContextIndex

    table = contextual_table()
    reference = table.source_reference
    extra = tuple(
        CellContent(
            id=f"later-{r}-{c}",
            row=r,
            col=c,
            text="2022" if r == 5 else "7",
            raw_text="7",
            reference=reference,
        )
        for r in (5, 6)
        for c in range(5)
    )
    table = table.model_copy(
        update={"rows": 7, "cells": (*table.cells, *extra), "header_rows": (0, 1, 5)}
    )
    index = ContextIndex(table)
    assert [source.text for source in index.cell("r2c1").column_headers] == ["2024", "Amount"]
    assert [source.text for source in index.cell("later-6-1").column_headers] == ["2022"]


def test_context_source_qualifiers_are_preserved_and_dollar_is_not_usd():
    from table_review.tables.context import ContextIndex
    from table_review.tables.context_types import ContextSource

    table = contextual_table()
    caption = ContextSource(
        id="caption:units",
        text="($ in millions, except per share amounts)",
        raw_text="($ in millions, except per share amounts)",
        references=(table.source_reference,),
        origin="extracted",
    )
    table = table.model_copy(update={"context_sources": (caption,)})
    context = ContextIndex(table).cell("r2c1")
    assert any(item.kind == "currency" and item.value == "$" for item in context.proposals)
    assert not any(item.value == "USD" for item in context.proposals)
    scale = next(item for item in context.proposals if item.kind == "scale")
    assert "except per share" in scale.value
    assert table.cells[-2].text == "(12.50)"


def test_legacy_checksum_does_not_change_for_empty_context_extensions():
    table = contextual_table()
    legacy = table.model_dump(mode="json")
    legacy.pop("context_sources", None)
    legacy.pop("context_bindings", None)
    legacy.pop("subtotal_groups", None)
    digest = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert content_checksum(TableContent.model_validate(legacy)) == digest


def test_unmapped_cell_is_rejected_instead_of_guessed():
    from table_review.tables.context import ContextIndex

    with pytest.raises(ValueError, match="Unknown cell"):
        ContextIndex(contextual_table()).cell("missing")


def test_only_nearby_aligned_caption_tokens_are_captured():
    from table_review.documents.types import PageBox
    from table_review.extraction.context import caption_sources
    from table_review.extraction.types import SourceToken

    region = PageBox(
        page=2, left=60, top=150, right=500, bottom=350, page_width=612, page_height=792
    )

    def token(identity, text, left, top):
        box = region.model_copy(
            update={"left": left, "right": left + 80, "top": top, "bottom": top + 10}
        )
        ref = SourceReference(page=2, precision="cell", box=box)
        return SourceToken(id=identity, text=text, box=box, provenance=ref)

    sources = caption_sources(
        region,
        (
            token("a", "USD millions", 60, 130),
            token("b", "except per share", 150, 130),
            token("distant", "GBP billions", 60, 20),
            token("other-column", "EUR thousands", 520, 130),
        ),
    )
    assert len(sources) == 1 and sources[0].text == "USD millions except per share"
    assert len(sources[0].references) == 2
    assert all(ref.precision == "cell" for ref in sources[0].references)


@pytest.mark.parametrize("wording", ["US$", "US$ in millions"])
def test_explicit_us_dollar_literal_is_preserved(wording):
    from table_review.tables.context import ContextIndex
    from table_review.tables.context_types import ContextSource

    table = contextual_table()
    source = ContextSource(
        id="caption:currency",
        text=wording,
        raw_text=wording,
        references=(table.source_reference,),
        origin="extracted",
    )
    context = ContextIndex(table.model_copy(update={"context_sources": (source,)})).cell("r2c1")
    assert [item.value for item in context.proposals if item.kind == "currency"] == ["US$"]


def test_wrapped_caption_keeps_qualifier_and_all_source_locations():
    from table_review.documents.types import PageBox
    from table_review.extraction.context import caption_sources
    from table_review.extraction.types import SourceToken

    region = PageBox(
        page=2, left=60, top=150, right=500, bottom=350, page_width=612, page_height=792
    )
    tokens = []
    for identity, text, top in (
        ("unit", "($ in millions,", 120),
        ("exception", "except per share amounts)", 134),
    ):
        box = region.model_copy(update={"left": 60, "right": 240, "top": top, "bottom": top + 10})
        tokens.append(
            SourceToken(
                id=identity,
                text=text,
                box=box,
                provenance=SourceReference(page=2, precision="cell", box=box),
            )
        )
    sources = caption_sources(region, tokens)
    assert len(sources) == 1
    assert sources[0].text == "($ in millions, except per share amounts)"
    assert len(sources[0].references) == 2


def test_below_table_unit_note_is_captured_but_neighbour_table_is_excluded():
    from table_review.documents.types import PageBox
    from table_review.extraction.context import caption_sources
    from table_review.extraction.types import SourceToken

    region = PageBox(
        page=2, left=60, top=150, right=500, bottom=350, page_width=612, page_height=792
    )
    neighbour = region.model_copy(update={"top": 365, "bottom": 450})

    def token(identity, text, top):
        box = region.model_copy(update={"top": top, "bottom": top + 8, "right": 240})
        return SourceToken(
            id=identity,
            text=text,
            box=box,
            provenance=SourceReference(page=2, precision="cell", box=box),
        )

    sources = caption_sources(
        region,
        (token("unit", "USD millions; except per share", 354), token("other", "EUR billions", 365)),
        excluded_regions=(neighbour,),
    )
    assert len(sources) == 1
    assert sources[0].text == "USD millions; except per share"


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "delete_row", "index": 2},
        {"kind": "delete_column", "index": 1},
        {"kind": "insert_row", "index": 2},
        {"kind": "set_headers", "header_rows": [0], "row_label_columns": [0]},
        {"kind": "set_notes", "unit_notes": ["USD millions"]},
    ],
)
def test_structural_and_note_edits_clear_context_without_dangling_bindings(operation):
    from table_review.tables.grid import apply_edit, parse_edit
    from tests.domain.test_context_exports import confirmed_table

    table = confirmed_table()
    changed = apply_edit(table, parse_edit(operation))
    assert not changed.content.context_bindings
    assert table.context_bindings
