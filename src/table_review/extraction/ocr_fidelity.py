"""Image-grounded spacing and source bounds; never recognize or replace digits.

Original OCR literals/tokens stay immutable. Ambiguous pixel groups retain their
original coarse evidence rather than allocating boxes by character count.
"""

import math
import re
from statistics import median

from table_review.documents.types import SourceReference
from table_review.extraction.types import SourceToken

_AMOUNT = r"(?:\([0-9][0-9,]*(?:\.[0-9]+)?\)|-?[0-9][0-9,]*(?:\.[0-9]+)?)"
_PART = re.compile(rf"(?:\$|{_AMOUNT})\Z")
_PREFIX = re.compile(rf"\$\s+({_AMOUNT})\Z")
_SUFFIX = re.compile(r"[A-Za-z](?P<suffix>[0-9]{1,2})\Z")


def _components(image, box, scale):
    import cv2
    import numpy as np

    left, top = math.floor(box.left * scale), math.floor(box.top * scale)
    right, bottom = math.ceil(box.right * scale), math.ceil(box.bottom * scale)
    if not 0 < (right - left) * (bottom - top) <= 1_000_000:
        return ()
    with image.crop((left, top, right, bottom)).convert("L") as crop:
        pixels = (np.asarray(crop) < 180).astype("uint8")
    count, _, stats, _ = cv2.connectedComponentsWithStats(pixels, connectivity=8)
    components = []
    for x, y, width, height, area in stats[1:count]:
        if area < 2:
            continue
        if (
            width >= (right - left) * 0.7
            and height <= max(3, 1.5 * scale)
            and x <= scale
            and x + width >= right - left - scale
            and count > 2
        ):
            continue  # A clipped boundary rule alongside glyphs; keep isolated minus signs.
        if height >= max(20 * scale, (bottom - top) * 0.8) and width <= max(2, scale):
            continue
        components.append(
            (int(x + left), int(y + top), int(x + left + width), int(y + top + height))
        )
    return tuple(components)


def _box(original, components, scale):
    return original.model_copy(
        update={
            "left": max(original.left, min(c[0] for c in components) / scale),
            "top": max(original.top, min(c[1] for c in components) / scale),
            "right": min(original.right, max(c[2] for c in components) / scale),
            "bottom": min(original.bottom, max(c[3] for c in components) / scale),
        }
    )


def currency_pieces(token, image, scale):
    parts = token.text.split()
    if not parts or not all(_PART.fullmatch(p) for p in parts) or len(parts) > 3:
        return ()
    components = _components(image, token.box, scale)
    if not components:
        return ()
    height = median(c[3] - c[1] for c in components)
    groups = []
    for component in sorted(components):
        if groups and component[0] - max(c[2] for c in groups[-1]) <= max(2 * scale, height * 0.5):
            groups[-1].append(component)
        else:
            groups.append([component])
    if len(groups) != len(parts):
        return ()
    result = []
    for index, (text, group) in enumerate(zip(parts, groups, strict=True)):
        bounds = _box(token.box, group, scale)
        result.append(
            SourceToken(
                id=f"{token.id}:image-piece:{index}",
                text=text,
                box=bounds,
                provenance=SourceReference(page=bounds.page, precision="cell", box=bounds),
            )
        )
    return tuple(result)


def ground_ocr_content(content, tokens, image, scale):
    cells, changes, derived = [], [], {}
    pieces = {}
    for token in tokens:
        split = currency_pieces(token, image, scale)
        if split:
            pieces[token.id] = split
    for cell in content.cells:
        updated = cell
        bounds = cell.reference.box
        suffix = _SUFFIX.search(cell.text)
        components = (
            _components(image, bounds, scale)
            if bounds and cell.reference.precision == "cell"
            else ()
        )
        if (
            suffix
            and components
            and cell.role in {"row_header", "column_header", "label", "section"}
        ):
            ordered = sorted(components)
            count = len(suffix.group("suffix"))
            body, marker = ordered[:-count], ordered[-count:]
            if len(body) >= 4:
                height = median(c[3] - c[1] for c in body)
                baseline = median(c[3] for c in body if c[3] - c[1] >= height * 0.65)
                if all(
                    c[3] < baseline - height * 0.2 and c[3] - c[1] < height * 0.9 for c in marker
                ):
                    updated = updated.model_copy(
                        update={
                            "text": cell.text[: suffix.start("suffix")]
                            + " "
                            + suffix.group("suffix")
                        }
                    )
                    changes.append({"cell_id": cell.id, "reason": "image_raised_suffix_spacing"})
        # A regrouped currency cell carries its original two OCR-chunk boxes.
        # Only separate visible pieces can upgrade that coarse association.
        match = _PREFIX.fullmatch(cell.text)
        if match and len(cell.source_references) == 2 and cell.reference.precision == "table":
            parents = [[t for t in tokens if t.box == ref.box] for ref in cell.source_references]
            if all(len(p) == 1 for p in parents):
                previous, own = (pieces.get(p[0].id, ()) for p in parents)
                amounts = [p for p in own if p.text == match.group(1)]
                dollars = [p for p in previous if p.text == "$"]
                if len(amounts) == len(dollars) == 1 and dollars[0].box.right < amounts[0].box.left:
                    evidence = (dollars[0], amounts[0])
                    union = _box(
                        bounds,
                        [
                            (
                                p.box.left * scale,
                                p.box.top * scale,
                                p.box.right * scale,
                                p.box.bottom * scale,
                            )
                            for p in evidence
                        ],
                        scale,
                    )
                    updated = updated.model_copy(
                        update={
                            "reference": SourceReference(
                                page=union.page, precision="cell", box=union
                            ),
                            "source_references": tuple(p.provenance for p in evidence),
                        }
                    )
                    derived.update({p.id: p for p in evidence})
                    changes.append(
                        {
                            "cell_id": cell.id,
                            "reason": "image_separated_currency_bounds",
                            "source_token_ids": [p.id for p in evidence],
                        }
                    )
        elif components and cell.text.strip():
            tight = _box(bounds, components, scale)
            if tight != bounds:
                updated = updated.model_copy(
                    update={
                        "reference": SourceReference(page=tight.page, precision="cell", box=tight)
                    }
                )
                changes.append({"cell_id": cell.id, "reason": "image_ink_bounds"})
        cells.append(updated)
    return (
        content.model_copy(update={"cells": tuple(cells)}),
        tuple(changes),
        tuple(derived.values()),
    )
