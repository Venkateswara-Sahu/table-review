"""Conservative acceptance for the single additional mixed-page OCR pass.

The layout model proposes tables; numeric structure and literal/geometry checks
filter those proposals. These checks do not establish extraction accuracy.
"""

import re
from collections import Counter

from table_review.extraction.recovery import intersects

_NUMBER = re.compile(r"[+$€£−-]?\(?\d+(?:,\d{3})*(?:\.\d+)?%?\)?\Z")


def _literal(text):
    return re.sub(r"\s+", "", text).casefold().replace("−", "-")


def _fingerprint(table):
    # Use cell literals, never table.source_tokens: a coarse native table can
    # carry every token on the page, including an unrelated image table.
    return Counter(_literal(cell.text) for cell in table.content.cells if cell.text.strip())


def _coarse_text_overlap(left, right):
    """Unknown native geometry cannot disambiguate equivalent text with new splits."""

    def terms(table):
        text = " ".join(
            cell.text for cell in sorted(table.content.cells, key=lambda c: (c.row, c.col))
        )
        return Counter(re.findall(r"[^\W\d_]+|[+-]?\d[\d,]*(?:\.\d+)?", text.casefold()))

    a, b = terms(left), terms(right)
    smaller, larger = (a, b) if sum(a.values()) <= sum(b.values()) else (b, a)
    # Candidate validation also supports a header plus one numeric body row.
    has_header = any(
        table.content.header_rows
        or any(cell.role == "column_header" for cell in table.content.cells)
        for table in (left, right)
    )
    return (
        sum(count for token, count in smaller.items() if token[0].isalpha()) >= 2
        and sum(count for token, count in smaller.items() if any(c.isdigit() for c in token))
        >= (1 if has_header else 2)
        and all(count <= larger[token] for token, count in smaller.items())
    )


def _numeric_structure(table):
    content = table.content
    if content.rows < 2 or content.columns < 2:
        return False
    numeric = [cell for cell in content.cells if _NUMBER.fullmatch(_literal(cell.text))]
    labels = [
        cell
        for cell in content.cells
        if any(character.isalpha() for character in cell.text) and len(cell.text.split()) <= 8
    ]
    for column in {cell.col for cell in numeric}:
        numeric_rows = {cell.row for cell in numeric if cell.col == column}
        labelled_rows = {
            cell.row for cell in labels if cell.col != column and cell.row in numeric_rows
        }
        if len(numeric_rows) >= 2 and (
            len(labelled_rows) >= 2 or (content.rows >= 3 and content.columns >= 3)
        ):
            return True
        # A 2x2 header plus one label/value row has only one numeric cell.
        if labelled_rows and any(
            cell.col == column
            and cell.row not in numeric_rows
            and (cell.role == "column_header" or cell.row in content.header_rows)
            for cell in labels
        ):
            return True
    return False


def _substantial_overlap(left, right):
    if not intersects(left, right):
        return False
    intersection = (min(left.right, right.right) - max(left.left, right.left)) * (
        min(left.bottom, right.bottom) - max(left.top, right.top)
    )
    smaller = min(
        (left.right - left.left) * (left.bottom - left.top),
        (right.right - right.left) * (right.bottom - right.top),
    )
    # Small edge overlaps are common in independently predicted bboxes. A
    # quarter of the smaller table is sufficiently ambiguous for manual review.
    return intersection > smaller * 0.25


def select_mixed_candidates(candidates, original_tables, page_no):
    """Return additional tables and content-free decisions; never mutate natives."""
    accepted, decisions = [], []
    for table in candidates:
        region = table.region
        reason = None
        if table.page_no != page_no or region is None or region.page != page_no:
            reason = "missing_or_other_page_geometry"
        elif region.right <= region.left or region.bottom <= region.top:
            reason = "empty_geometry"
        elif not _numeric_structure(table):
            reason = "insufficient_numeric_table_evidence"
        else:
            fingerprint = _fingerprint(table)
            for prior in (*original_tables, *accepted):
                if prior.page_no != page_no:
                    continue
                overlap = prior.region is not None and intersects(region, prior.region)
                if (prior.region is None or overlap) and fingerprint == _fingerprint(prior):
                    reason = "duplicate_literals"
                    break
                if prior.region is None and _coarse_text_overlap(table, prior):
                    reason = "coarse_native_text_overlap"
                    break
                if prior.region is not None and _substantial_overlap(region, prior.region):
                    reason = "substantial_overlap"
                    break
        if reason is None:
            accepted.append(table)
        decisions.append({"table_id": table.id, "decision": reason or "accepted"})
    return accepted, decisions
