from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.literals import reconcile_native_literals
from table_review.extraction.types import SourceToken
from table_review.tables.types import CellContent, TableContent


def test_unchanged_native_literal_uses_its_exact_visible_token_bounds():
    original = cell("amount", "120", 0, 100, 200)
    content = TableContent(rows=1, columns=1, cells=(original,))
    evidence = token("visible-number", "120", 130, 150)
    updated, changes = reconcile_native_literals(content, (evidence,))
    assert updated.cells[0].text == updated.cells[0].raw_text == "120"
    assert updated.cells[0].reference.box == evidence.box
    assert updated.cells[0].source_references == (evidence.provenance,)
    assert changes[0]["reason"] == "source_literal_bounds"
    assert reconcile_native_literals(updated, (evidence,)) == (updated, ())


def test_native_bounds_do_not_use_a_neighbouring_or_disagreeing_literal():
    original = cell("amount", "120", 0, 100, 200)
    content = TableContent(rows=1, columns=1, cells=(original,))
    evidence = (token("value", "120", 130, 150), token("neighbour", "90", 170, 190))
    assert reconcile_native_literals(content, evidence) == (content, ())


def box(left, right, top=20, bottom=30):
    return PageBox(
        page=1, left=left, right=right, top=top, bottom=bottom, page_width=600, page_height=800
    )


def token(identity, text, left, right, top=20, bottom=30):
    bounds = box(left, right, top, bottom)
    return SourceToken(
        id=identity,
        text=text,
        box=bounds,
        provenance=SourceReference(page=1, precision="cell", box=bounds),
    )


def cell(identity, text, col, left, right, *, row=0):
    return CellContent(
        id=identity,
        row=row,
        col=col,
        text=text,
        raw_text=text,
        reference=SourceReference(page=1, precision="cell", box=box(left, right)),
    )


def currency_row():
    content = TableContent(
        rows=1,
        columns=3,
        cells=(
            cell("a", "$ 47,941 $", 0, 100, 205),
            cell("b", "47,061 $", 1, 230, 305),
            cell("c", "45,754", 2, 330, 360),
        ),
    )
    tokens = (
        token("d1", "$", 100, 105),
        token("n1", "47,941", 130, 160),
        token("d2", "$", 200, 205),
        token("n2", "47,061", 230, 260),
        token("d3", "$", 300, 305),
        token("n3", "45,754", 330, 360),
    )
    return content, tokens


def test_currency_markers_follow_their_source_amount_and_keep_parser_raw_text():
    content, tokens = currency_row()
    updated, changes = reconcile_native_literals(content, tokens)
    assert [c.text for c in updated.cells] == ["$ 47,941", "$ 47,061", "$ 45,754"]
    assert [c.raw_text for c in updated.cells] == [c.raw_text for c in content.cells]
    assert updated.cells[1].reference.box.left == 200
    assert updated.cells[0].reference.box.right == 160
    assert tuple(r.box for r in updated.cells[1].source_references) == (
        tokens[2].box,
        tokens[3].box,
    )
    assert len(changes) == 3
    assert all(c["reason"] == "source_prefix_currency_row" for c in changes)


def test_postfix_currency_or_missing_prefix_evidence_is_not_reinterpreted():
    content, tokens = currency_row()
    postfix = tuple(
        token(t.id, t.text, t.box.left + 65, t.box.right + 65) if t.text == "$" else t
        for t in tokens
    )
    assert reconcile_native_literals(content, postfix)[0] == content
    assert reconcile_native_literals(content, tokens[1:])[0] == content


def test_numeric_disagreement_is_not_silently_repaired_from_nearby_values():
    content, tokens = currency_row()
    content = content.model_copy(
        update={
            "cells": (
                content.cells[0].model_copy(update={"text": "$ 47,942 $"}),
                *content.cells[1:],
            )
        }
    )
    assert reconcile_native_literals(content, tokens)[0] == content


def test_source_punctuation_is_restored_without_changing_numbers_or_inventing_text():
    content = TableContent(
        rows=1, columns=1, cells=(cell("label", "Martha's Table - net", 0, 10, 95),)
    )
    tokens = (
        token("a", "Martha’s", 10, 35),
        token("b", "Table", 40, 60),
        token("c", "—", 65, 70),
        token("d", "net", 75, 95),
    )
    updated, changes = reconcile_native_literals(content, tokens)
    assert updated.cells[0].text == "Martha’s Table — net"
    assert updated.cells[0].raw_text == "Martha's Table - net"
    assert changes[0]["reason"] == "source_typographic_literal"
    changed = (tokens[0].model_copy(update={"text": "Other"}), *tokens[1:])
    assert reconcile_native_literals(content, changed)[0] == content


def test_coarse_references_and_other_page_tokens_never_supply_literal_replacements():
    content, tokens = currency_row()
    coarse = content.model_copy(
        update={
            "cells": tuple(
                c.model_copy(
                    update={
                        "reference": SourceReference(page=1, precision="table", box=c.reference.box)
                    }
                )
                for c in content.cells
            )
        }
    )
    assert reconcile_native_literals(coarse, tokens)[0] == coarse
    wrong = tuple(
        t.model_copy(update={"box": t.box.model_copy(update={"page": 2})}) for t in tokens
    )
    assert reconcile_native_literals(content, wrong)[0] == content


def test_reconciliation_is_idempotent():
    content, tokens = currency_row()
    updated, _ = reconcile_native_literals(content, tokens)
    assert reconcile_native_literals(updated, tokens) == (updated, ())


def test_unrelated_leading_dollar_cannot_turn_postfix_currency_into_prefix_currency():
    content = TableContent(
        rows=1,
        columns=2,
        cells=(
            cell("a", "100 $", 0, 100, 165),
            cell("b", "200 $", 1, 200, 265),
        ),
    )
    tokens = (
        token("unrelated", "$", 60, 65),
        token("one", "100", 100, 130),
        token("postfix-one", "$", 160, 165),
        token("two", "200", 200, 230),
        token("postfix-two", "$", 260, 265),
    )
    assert reconcile_native_literals(content, tokens) == (content, ())
