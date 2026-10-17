"""Bounded, nearby caption evidence; never apply a unit or scale automatically."""

import hashlib

from table_review.tables.context_types import ContextSource


def caption_sources(region, tokens, *, excluded_regions=()):
    if region is None:
        return ()
    candidates = [
        token
        for token in tokens
        if token.box.page == region.page
        and (
            region.top - 36 <= token.box.bottom <= region.top + 1
            or region.bottom <= token.box.top <= region.bottom + 24
        )
        and min(token.box.right, region.right) > max(token.box.left, region.left)
        and not any(
            other
            and other.page == token.box.page
            and min(token.box.right, other.right) > max(token.box.left, other.left)
            and min(token.box.bottom, other.bottom) > max(token.box.top, other.top)
            for other in excluded_regions
        )
    ]
    lines = []
    for token in sorted(candidates, key=lambda item: (item.box.top, item.box.left, item.id)):
        if not lines or abs(lines[-1][0].box.top - token.box.top) > 3:
            lines.append([])
        lines[-1].append(token)
    # Keep a wrapped caption together: an exception on its next line changes
    # the meaning of the unit wording. Separate blocks at a generous line gap.
    blocks = []
    for line in lines[-8:]:
        if not blocks or line[0].box.top - max(token.box.bottom for token in blocks[-1]) > 8:
            blocks.append([])
        blocks[-1].extend(sorted(line, key=lambda token: (token.box.left, token.id)))
    sources = []
    for line in blocks:
        text = " ".join(token.text for token in line)
        if not text.strip() or len(text) > 800 or len(line) > 32:
            continue
        identity = hashlib.sha256("\n".join(token.id for token in line).encode()).hexdigest()
        sources.append(
            ContextSource(
                id="caption:" + identity,
                text=text,
                raw_text=text,
                references=tuple(token.provenance for token in line),
                origin="extracted",
                truncated=text.count("(") > text.count(")") or text.rstrip().endswith(","),
            )
        )
    return tuple(sources)
