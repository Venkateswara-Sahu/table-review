from table_review.documents.types import SourceReference
from table_review.extraction.literals import reconcile_ocr_currency_rows
from table_review.tables.types import TableContent
from tests.domain.test_native_literals import box, cell, token


def fixture():
    region = box(20, 400, 10, 80)
    content = TableContent(
        rows=1,
        columns=3,
        source_reference=SourceReference(page=1, precision="table", box=region),
        cells=(
            cell("a", "$ 47,941 $", 0, 100, 205),
            cell("b", "47,061 $", 1, 230, 305),
            cell("c", "45,754", 2, 330, 360),
        ),
    )
    tokens = (
        token("first-prefix", "$", 100, 105),
        token("chunk-a", "47,941 $", 130, 205),
        token("chunk-b", "47,061 $", 230, 305),
        token("chunk-c", "45,754", 330, 360),
    )
    return content, tokens


def test_complete_ocr_prefix_sequence_repairs_currency_without_inventing_glyph_boxes():
    content, tokens = fixture()
    updated, changes = reconcile_ocr_currency_rows(content, tokens)
    assert [c.text for c in updated.cells] == ["$ 47,941", "$ 47,061", "$ 45,754"]
    assert [c.raw_text for c in updated.cells] == [c.raw_text for c in content.cells]
    assert all(c.reference.precision == "table" for c in updated.cells)
    assert all(r.precision == "table" for c in updated.cells for r in c.source_references)
    assert len(changes) == 3
    assert all(c["reason"] == "ocr_complete_prefix_currency_sequence" for c in changes)
    assert updated.cells[1].source_references[0].box == tokens[1].box


def test_uncertain_letter_s_is_not_repaired_into_a_currency_symbol():
    content, tokens = fixture()
    tokens = (tokens[0].model_copy(update={"text": "S"}), *tokens[1:])
    assert reconcile_ocr_currency_rows(content, tokens) == (content, ())


def test_postfix_sequence_extra_markers_or_numeric_disagreement_are_rejected():
    content, tokens = fixture()
    for evidence in (
        tokens[1:],
        (*tokens, token("extra", "$", 370, 375)),
        (tokens[0], tokens[1].model_copy(update={"text": "47,942 $"}), *tokens[2:]),
    ):
        assert reconcile_ocr_currency_rows(content, evidence) == (content, ())


def test_missing_table_or_precise_amount_evidence_cannot_supply_a_repair():
    content, tokens = fixture()
    missing = content.model_copy(update={"source_reference": None})
    assert reconcile_ocr_currency_rows(missing, tokens) == (missing, ())
    coarse = content.model_copy(
        update={
            "cells": (
                content.cells[0].model_copy(update={"reference": content.source_reference}),
                *content.cells[1:],
            )
        }
    )
    assert reconcile_ocr_currency_rows(coarse, tokens) == (coarse, ())
