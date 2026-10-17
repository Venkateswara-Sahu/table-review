"""Bounded region proposals for numeric tables missed by the layout model.

Alignment is proposal evidence, not a table or accuracy guarantee. A proposal
must still pass the ordinary table parser and source-geometry adapter.
"""

import re
from statistics import median

from table_review.documents.types import ImmutableModel, PageBox, PageInfo
from table_review.extraction.types import SourceToken

_NUMBER = re.compile(r"[+$€£−-]?\(?\d+(?:,\d{3})*(?:\.\d+)?%?\)?\Z")


class RegionCandidate(ImmutableModel):
    region: PageBox
    numeric_rows: int
    numeric_columns: int
    numeric_tokens: int


def intersects(left, right):
    return (
        left.page == right.page
        and left.left < right.right
        and right.left < left.right
        and left.top < right.bottom
        and right.top < left.bottom
    )


def candidate_regions(
    tokens: tuple[SourceToken, ...], page: PageInfo, existing: tuple[PageBox, ...], *, limit=2
) -> tuple[RegionCandidate, ...]:
    if not 0 <= limit <= 2:
        raise ValueError("At most two automatic recovery regions per page")
    if not limit or len(tokens) > 20_000:
        return ()
    available = [
        token
        for token in tokens
        if token.box.page == page.page_no
        and token.box.right > token.box.left
        and token.box.bottom > token.box.top
        and not any(intersects(token.box, box) for box in existing)
    ]
    if not available:
        return ()
    height = median(token.box.bottom - token.box.top for token in available)
    rows = []
    for token in sorted(available, key=lambda t: (t.box.top + t.box.bottom) / 2):
        centre = (token.box.top + token.box.bottom) / 2
        if rows and abs(centre - rows[-1][0]) <= max(3, height * 0.45):
            rows[-1][1].append(token)
        else:
            rows.append((centre, [token]))
    numeric = [
        (index, centre, [t for t in items if _NUMBER.fullmatch(re.sub(r"\s+", "", t.text))])
        for index, (centre, items) in enumerate(rows)
    ]
    numeric = [row for row in numeric if row[2]]
    runs = []
    for row in numeric:
        if runs and row[1] - runs[-1][-1][1] <= max(24, height * 2.5):
            runs[-1].append(row)
        else:
            runs.append([row])
    candidates = []
    for run in runs:
        if len(run) < 3:
            continue
        tracks = []
        for row_index, token in sorted(
            ((row_index, token) for row_index, _, items in run for token in items),
            key=lambda item: item[1].box.right,
        ):
            if tracks and token.box.right - tracks[-1][0] <= max(4, min(9, height * 0.65)):
                tracks[-1][1].add(row_index)
                tracks[-1][2].append(token)
            else:
                tracks.append((token.box.right, {row_index}, [token]))
        stable = [track for track in tracks if len(track[1]) >= 3]
        if len(stable) < 2:
            continue
        first_numeric = min(token.box.left for _, _, items in stable for token in items)
        labelled = sum(
            any(
                token.box.right <= first_numeric + 4
                and sum(character.isalpha() for character in token.text) >= 3
                for token in rows[row_index][1]
            )
            for row_index, _, _ in run
        )
        if labelled < 2:
            continue
        first, last = run[0][0], run[-1][0]
        # Include the adjacent header row when it has multiple text fragments.
        if (
            first
            and rows[first][0] - rows[first - 1][0] <= height * 1.8
            and len(rows[first - 1][1]) >= 2
        ):
            first -= 1
        selected = [token for _, items in rows[first : last + 1] for token in items]
        box = PageBox(
            page=page.page_no,
            left=max(0, min(t.box.left for t in selected) - 8),
            top=max(0, min(t.box.top for t in selected) - 8),
            right=min(page.width, max(t.box.right for t in selected) + 8),
            bottom=min(page.height, max(t.box.bottom for t in selected) + 8),
            page_width=page.width,
            page_height=page.height,
        )
        if any(intersects(box, prior) for prior in existing):
            continue
        candidates.append(
            RegionCandidate(
                region=box,
                numeric_rows=len(run),
                numeric_columns=len(stable),
                numeric_tokens=sum(len(items) for _, _, items in run),
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.region.top)[:limit])
