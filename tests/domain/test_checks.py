from uuid import UUID

import pytest

from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.types import SourceToken
from table_review.tables.types import CellContent, TableContent


def box(left, top, right, bottom):
    return PageBox(
        page=2, left=left, top=top, right=right, bottom=bottom, page_width=600, page_height=800
    )


def token(identity, text, area):
    return SourceToken(
        id=identity,
        text=text,
        box=area,
        provenance=SourceReference(page=2, precision="cell", box=area),
    )


def cell(identity, row, col, text, area=None, role="data"):
    return CellContent(
        id=identity,
        row=row,
        col=col,
        text=text,
        raw_text=text,
        role=role,
        reference=SourceReference(page=2, precision="cell" if area else "page", box=area),
    )


@pytest.fixture
def clean():
    from table_review.checks.types import CheckContext

    cells = (
        cell("a", 0, 0, "100", box(10, 20, 40, 30)),
        cell("b", 1, 0, "100", box(10, 60, 40, 70)),
    )
    content = TableContent(rows=2, columns=1, cells=cells)
    context = CheckContext(
        source_tokens=(
            token("token-a", "100", cells[0].reference.box),
            token("token-b", "100", cells[1].reference.box),
        ),
        source_regions=(box(0, 0, 100, 100),),
        check_version="1",
    )
    return content, context


def test_equal_numbers_at_distinct_locations_are_not_duplicates(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    assert evaluate_checks(content, context) == ()


def test_missing_token_is_a_suspicion_with_original_location(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    missing = token("missing", "(123.45)", box(60, 60, 90, 70))
    context = context.model_copy(update={"source_tokens": context.source_tokens + (missing,)})
    issues = evaluate_checks(content, context)
    issue = next(i for i in issues if i.kind == "coverage_discrepancy")
    assert issue.evidence["token_ids"] == ["missing"]
    assert issue.reference.box == missing.box
    assert "may" in issue.reason.lower() and "financial error" not in issue.reason.lower()
    assert UUID(str(issue.id))


def test_same_source_identity_is_reused_not_equal_numeric_values(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    repeated = content.cells[1].model_copy(update={"reference": content.cells[0].reference})
    content = content.model_copy(update={"cells": (content.cells[0], repeated)})
    issue = next(i for i in evaluate_checks(content, context) if i.kind == "duplicate_source_token")
    assert issue.affected_cell_ids == ("a", "b")
    assert issue.evidence["token_ids"] == ["token-a"]


def test_duplicate_coordinates_still_identify_the_same_source_token(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    alias = context.source_tokens[0].model_copy(update={"id": "alias-id"})
    issues = evaluate_checks(
        content, context.model_copy(update={"source_tokens": context.source_tokens + (alias,)})
    )
    assert not any(i.kind == "coverage_discrepancy" for i in issues)


@pytest.mark.parametrize(
    "source, extracted",
    [("—", "-"), ("(123)", "123"), ("1,234.5*", "1,234.5"), ("−12", "12"), ("12.3", "123")],
)
def test_normalization_does_not_hide_meaningful_literal_differences(clean, source, extracted):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    changed = content.cells[0].model_copy(update={"text": extracted, "raw_text": extracted})
    content = content.model_copy(update={"cells": (changed, content.cells[1])})
    source_token = context.source_tokens[0].model_copy(update={"text": source})
    issues = evaluate_checks(
        content,
        context.model_copy(update={"source_tokens": (source_token, context.source_tokens[1])}),
    )
    assert any(i.kind == "coverage_discrepancy" for i in issues)
    assert content.cells[0].text == extracted  # No replacement.


def test_whitespace_only_difference_is_not_a_missing_value(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    content = content.model_copy(
        update={
            "cells": (content.cells[0].model_copy(update={"text": " \t100\n"}), content.cells[1])
        }
    )
    assert evaluate_checks(content, context) == ()


def test_reversed_row_geometry_is_ambiguous_not_a_known_wrong_value(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    cells = tuple(c.model_copy(update={"row": 1 - c.row}) for c in content.cells)
    issues = evaluate_checks(content.model_copy(update={"cells": cells}), context)
    assert any(
        i.kind == "placement_ambiguity" and set(i.affected_cell_ids) == {"a", "b"} for i in issues
    )


def test_coarse_geometry_is_limited_reference_not_placement_claim(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    cells = tuple(
        c.model_copy(update={"reference": SourceReference(page=2, precision="page")})
        for c in content.cells
    )
    issues = evaluate_checks(content.model_copy(update={"cells": cells}), context)
    assert any(i.kind == "limited_reference" for i in issues)
    assert not any(i.kind in ("placement_ambiguity", "duplicate_source_token") for i in issues)


def test_blank_cell_with_coarse_geometry_makes_no_placement_claim(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    blank = cell("a", 0, 0, "").model_copy(
        update={"reference": SourceReference(page=2, precision="table", box=box(0, 0, 100, 100))}
    )
    content = content.model_copy(update={"cells": (blank, *content.cells[1:])})
    assert not any(i.kind == "limited_reference" for i in evaluate_checks(content, context))


def test_header_role_association_and_missing_slots_are_structural_issues(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    content = content.model_copy(update={"columns": 2, "header_rows": (0,)})
    issues = evaluate_checks(content, context)
    assert any(i.kind == "structural_inconsistency" for i in issues)


def test_fingerprint_depends_on_relevant_content_and_associations(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    context = context.model_copy(
        update={
            "source_tokens": context.source_tokens + (token("missing", "42", box(60, 20, 80, 30)),)
        }
    )
    first = evaluate_checks(content, context)[0]
    assert evaluate_checks(content, context)[0].fingerprint == first.fingerprint
    unrelated = content.cells[1].model_copy(update={"text": "changed unrelated cell"})
    changed = content.model_copy(update={"cells": (content.cells[0], unrelated)})
    same = next(
        i for i in evaluate_checks(changed, context) if i.evidence.get("token_ids") == ["missing"]
    )
    assert same.fingerprint == first.fingerprint
    association = content.model_copy(update={"unit_notes": ("USD thousands",)})
    updated = next(
        i
        for i in evaluate_checks(association, context)
        if i.evidence.get("token_ids") == ["missing"]
    )
    assert updated.fingerprint != first.fingerprint


def test_no_region_no_coverage_judgment_for_full_page_tokens(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    issues = evaluate_checks(content, context.model_copy(update={"source_regions": ()}))
    assert any(i.kind == "coverage_unavailable" for i in issues)
    assert not any(i.kind == "coverage_discrepancy" for i in issues)


def test_an_omitted_blank_slot_alone_is_not_a_structural_error(clean):
    from table_review.checks.engine import evaluate_checks

    content, context = clean
    content = content.model_copy(update={"columns": 2})
    assert not any(i.kind == "structural_inconsistency" for i in evaluate_checks(content, context))


def test_date_caption_at_header_corner_and_section_labels_are_valid():
    from table_review.checks.engine import evaluate_checks
    from table_review.checks.types import CheckContext

    content = TableContent(
        rows=3,
        columns=2,
        header_rows=(0,),
        row_label_columns=(0,),
        cells=(
            cell("caption", 0, 0, "For the year ended December 31", role="data"),
            cell("year", 0, 1, "2025", role="column_header"),
            cell("section", 1, 0, "Revenue", role="section"),
            cell("total", 1, 1, "100", role="data"),
            cell("label", 2, 0, "Services", role="row_header"),
            cell("value", 2, 1, "50", role="data"),
        ),
    )
    context = CheckContext(source_tokens=(), source_regions=(), source_available=False)
    assert not any(i.kind == "structural_inconsistency" for i in evaluate_checks(content, context))
