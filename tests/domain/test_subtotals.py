from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.fixtures.subtotals import subtotal_table


def definition(content, **overrides):
    from table_review.tables.subtotal_types import SubtotalGroup, SubtotalTerm
    from table_review.tables.subtotals import confirm_group

    group = SubtotalGroup(
        id=uuid4(),
        name="Declared operating total",
        total_cell_id="r3c1",
        terms=(SubtotalTerm(cell_id="r1c1", sign=1), SubtotalTerm(cell_id="r2c1", sign=1)),
        **overrides,
    )
    return confirm_group(content, group).model_copy(
        update={"confirmed_by": uuid4(), "confirmed_at": datetime.now(UTC)}
    )


def test_signed_decimal_sum_and_absolute_tolerance_are_explicit():
    from table_review.tables.subtotals import evaluate_subtotals

    content = subtotal_table()
    group = definition(content)
    result = evaluate_subtotals(content.model_copy(update={"subtotal_groups": (group,)}))[0]
    assert result.state == "match" and result.input_sum == "8.05" and result.difference == "0.00"
    changed = content.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"text": "8.06"}) if c.id == "r3c1" else c
                for c in content.cells
            )
        }
    )
    loose = definition(changed, tolerance="0.01")
    result = evaluate_subtotals(changed.model_copy(update={"subtotal_groups": (loose,)}))[0]
    assert result.state == "match" and result.difference == "0.01"
    strict = definition(changed)
    assert (
        evaluate_subtotals(changed.model_copy(update={"subtotal_groups": (strict,)}))[0].state
        == "difference"
    )


@pytest.mark.parametrize(
    "value,interpretation",
    [
        ("", "en_us_number"),
        ("—", "uncertain"),
        ("12%", "percent"),
        ("1.2", "uncertain"),
        ("$12", "en_us_number"),
    ],
)
def test_unsupported_inputs_never_become_zero(value, interpretation):
    content = subtotal_table().model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"text": value, "interpretation": interpretation})
                if c.id == "r1c1"
                else c
                for c in subtotal_table().cells
            )
        }
    )
    with pytest.raises(ValueError):
        definition(content)


def test_context_must_be_current_confirmed_and_compatible():
    with pytest.raises(ValueError, match="context"):
        definition(subtotal_table(confirmed=False))
    content = subtotal_table()
    bad = content.model_copy(
        update={
            "context_bindings": tuple(
                b.model_copy(update={"value": "2024"})
                if b.cell_id == "r2c1" and b.kind == "period"
                else b
                for b in content.context_bindings
            )
        }
    )
    with pytest.raises(ValueError, match="context"):
        definition(bad)


def test_participating_edits_require_confirmation_and_unrelated_edits_do_not():
    from table_review.tables.subtotals import evaluate_subtotals

    content = subtotal_table()
    group = definition(content)
    content = content.model_copy(update={"subtotal_groups": (group,)})
    changed = content.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"text": "9.05"}) if c.id == "r3c1" else c
                for c in content.cells
            )
        }
    )
    assert evaluate_subtotals(changed)[0].state == "needs_confirmation"
    missing = content.model_copy(
        update={"cells": tuple(c for c in content.cells if c.id != "r1c1")}
    )
    assert evaluate_subtotals(missing)[0].state == "unavailable"
    extra = content.model_copy(update={"unit_notes": ("An unrelated note",)})
    assert evaluate_subtotals(extra)[0].state == "match"


def test_subtracting_a_negative_amount_is_not_silently_treated_as_addition():
    from table_review.tables.subtotal_types import SubtotalTerm
    from table_review.tables.subtotals import confirm_group, evaluate_subtotals

    content = subtotal_table()
    content = content.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"text": "12.15"}) if c.id == "r3c1" else c
                for c in content.cells
            )
        }
    )
    group = definition(content)
    group = confirm_group(
        content,
        group.model_copy(
            update={
                "terms": (
                    SubtotalTerm(cell_id="r1c1", sign=1),
                    SubtotalTerm(cell_id="r2c1", sign=-1),
                )
            }
        ),
    )
    result = evaluate_subtotals(content.model_copy(update={"subtotal_groups": (group,)}))[0]
    assert result.state == "match" and result.input_sum == "12.15"


def test_ambiguous_currency_symbol_and_per_share_headers_are_rejected():
    with pytest.raises(ValueError, match="currency"):
        definition(subtotal_table(units="$ millions"))
    content = subtotal_table()
    content = content.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"text": "Earnings per share"}) if c.id == "r1c0" else c
                for c in content.cells
            )
        }
    )
    with pytest.raises(ValueError, match="per-share"):
        definition(content)


def test_duplicate_inputs_and_the_total_as_input_are_rejected():
    from table_review.tables.subtotal_types import SubtotalGroup, SubtotalTerm

    for ids in (("r1c1", "r1c1"), ("r3c1",)):
        with pytest.raises(ValueError):
            SubtotalGroup(
                id=uuid4(),
                name="Bad relation",
                total_cell_id="r3c1",
                terms=tuple(SubtotalTerm(cell_id=identity, sign=1) for identity in ids),
            )


@pytest.mark.parametrize(
    "kind,word", [("period", "2024"), ("currency", "EUR"), ("scale", "thousands")]
)
def test_current_but_different_confirmed_scope_is_not_added(kind, word):
    from table_review.tables.context import ContextIndex
    from table_review.tables.context_types import ContextBinding

    content = subtotal_table()
    content = content.model_copy(
        update={
            "cells": tuple(
                cell.model_copy(update={"text": f"Expense {word}"}) if cell.id == "r2c0" else cell
                for cell in content.cells
            )
        }
    )
    proposal = next(
        item
        for item in ContextIndex(content).cell("r2c1").proposals
        if item.kind == kind and item.source_ids == ("cell:r2c0",)
    )
    prior = next(
        item for item in content.context_bindings if item.cell_id == "r2c1" and item.kind == kind
    )
    replacement = ContextBinding(
        **proposal.model_dump(),
        cell_id="r2c1",
        confirmed_by=prior.confirmed_by,
        confirmed_at=prior.confirmed_at,
    )
    content = content.model_copy(
        update={
            "context_bindings": tuple(
                replacement if item == prior else item for item in content.context_bindings
            )
        }
    )
    assert replacement in ContextIndex(content).cell("r2c1").confirmed
    with pytest.raises(ValueError, match="differs"):
        definition(content)


def test_merged_header_ancestry_is_captured_and_structural_change_reopens_confirmation():
    from table_review.tables.subtotals import evaluate_subtotals
    from table_review.tables.types import CellContent

    content = subtotal_table()
    original = definition(content)
    upper = CellContent(
        id="upper",
        row=0,
        col=0,
        col_span=2,
        text="Amounts",
        raw_text="Amounts",
        role="column_header",
        reference=content.source_reference,
    )
    changed = content.model_copy(
        update={
            "rows": 5,
            "header_rows": (0, 1),
            "cells": (
                upper,
                *(cell.model_copy(update={"row": cell.row + 1}) for cell in content.cells),
            ),
            "subtotal_groups": (original,),
        }
    )
    assert evaluate_subtotals(changed)[0].state == "needs_confirmation"
    changed = changed.model_copy(update={"subtotal_groups": (definition(changed),)})
    result = evaluate_subtotals(changed)[0]
    assert result.state == "match"
    assert [source["text"] for source in result.inputs[0]["column_headers"]] == ["Amounts", "2025"]
