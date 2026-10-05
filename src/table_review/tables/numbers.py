import re
from decimal import Decimal
from typing import Literal

from table_review.documents.types import ImmutableModel
from table_review.tables.types import CellRole, ValueInterpretation


class InterpretedValue(ImmutableModel):
    kind: Literal["text", "blank", "number", "percent"]
    decimal_value: Decimal | None = None
    raw_text: str
    reason: str


_UNSIGNED = r"(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?"
_SIGNED = re.compile(rf"[+-]?{_UNSIGNED}\Z")


def interpret_value(
    text: str, role: CellRole, interpretation: ValueInterpretation = "text"
) -> InterpretedValue:
    def literal(reason):
        return InterpretedValue(kind="blank" if not text else "text", raw_text=text, reason=reason)

    if role not in ("data", "value"):
        return literal("Header and label cells remain text")
    if interpretation in ("text", "uncertain", "auto"):
        return literal("No explicit numeric interpretation")
    candidate = text
    if not candidate:
        return literal("Blank source value")
    negative = candidate.startswith("(") and candidate.endswith(")")
    if negative:
        candidate = candidate[1:-1]
        if candidate.startswith(("+", "-")):
            return literal("Ambiguous accounting sign")
    percentage = interpretation == "percent"
    if percentage:
        if not candidate.endswith("%"):
            return literal("Percent interpretation requires a percent sign")
        candidate = candidate[:-1]
    if not _SIGNED.fullmatch(candidate):
        return literal(
            "Ambiguous formatting, identifier, footnote or unsupported numeric convention"
        )
    parsed = Decimal(candidate.replace(",", ""))
    if negative:
        parsed = parsed.copy_negate()
    # Retain the authored significant digits, including trailing precision zeros.
    if len(parsed.as_tuple().digits) > 15:
        return literal("Exceeds initial spreadsheet numeric precision")
    value = parsed / Decimal(100) if percentage else parsed
    return InterpretedValue(
        kind="percent" if percentage else "number",
        decimal_value=value,
        raw_text=text,
        reason="Explicit validated English/US numeric interpretation",
    )
