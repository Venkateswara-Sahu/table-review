"""Additive layout diagnostics; never replace strict metrics or confirm source scope.

The projection retains each literal at its top-left slot and treats the other
slots covered by its span as empty. It exposes merged-blank alternatives without
claiming semantic equivalence of headers, roles, source boxes or financial units.
"""

from evaluation.metrics import normalize
from table_review.tables.grid import validate_grid


def _expanded(content):
    if content is None:
        return {}
    validate_grid(content)
    return {
        (row, col): normalize(cell.text) if (row, col) == (cell.row, cell.col) else ""
        for cell in content.cells
        for row in range(cell.row, cell.row + cell.row_span)
        for col in range(cell.col, cell.col + cell.col_span)
    }


def diagnose_case(truth, content):
    expected, observed = _expanded(truth.content), _expanded(content)
    anchors = {
        (cell.row, cell.col): normalize(cell.text)
        for cell in truth.content.cells
        if cell.text.strip()
    }
    predicted_anchors = (
        {(cell.row, cell.col): normalize(cell.text) for cell in content.cells}
        if content is not None
        else {}
    )
    correct = sum(
        position in observed and observed[position] == text for position, text in expected.items()
    )
    notes = tuple(normalize(note) for note in truth.content.unit_notes if note.strip())
    captions = (
        tuple(
            normalize(source.text)
            for source in content.context_sources
            if source.origin == "extracted" and not source.truncated
        )
        if content is not None
        else ()
    )
    return {
        "method": "anchor_text_blank_span_projection_v1",
        "expanded_slot_literal_correct": correct,
        "expanded_slot_literal_total": len(expected),
        "expanded_slot_literals_exact": content is not None
        and (content.rows, content.columns) == (truth.content.rows, truth.content.columns)
        and expected == observed,
        "nonblank_anchor_literal_correct": sum(
            predicted_anchors.get(position) == text for position, text in anchors.items()
        ),
        "nonblank_anchor_literal_total": len(anchors),
        "source_caption_literal_supported": sum(
            any(note in caption for caption in captions) for note in notes
        ),
        "source_caption_literal_total": len(notes),
    }
