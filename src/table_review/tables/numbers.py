import re
from decimal import Decimal
from typing import Literal

from table_review.documents.types import ImmutableModel
from table_review.tables.types import CellRole, ValueInterpretation


class InterpretedValue(ImmutableModel):
    kind: Literal["text", "blank", "number", "percent"]
    decimal_value: Decimal | None = None
    currency_symbol: str | None = None
    raw_text: str
    reason: str


_US_UNSIGNED = r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?"
_US_SIGNED = re.compile(rf"[+-]?{_US_UNSIGNED}\Z")

_EU_UNSIGNED = r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:[.\u00a0 ][0-9]{3})+)(?:,[0-9]+)?"
_EU_SIGNED = re.compile(rf"[+-]?{_EU_UNSIGNED}\Z")

_INDIAN_UNSIGNED = r"(?:0|[1-9][0-9]*|[1-9][0-9]?(?:,[0-9]{2})*,[0-9]{3})(?:\.[0-9]+)?"
_INDIAN_SIGNED = re.compile(rf"[+-]?{_INDIAN_UNSIGNED}\Z")

_DASH_ZERO = frozenset({"-", "–", "—", "−", "--"})

_CURRENCY_NORMALIZATION = {
    "US$": "$",
    "$": "$",
    "CA$": "CA$",
    "A$": "A$",
    "HK$": "HK$",
    "S$": "S$",
    "NZ$": "NZ$",
    "€": "€",
    "EUR": "€",
    "£": "£",
    "GBP": "£",
    "₹": "₹",
    "INR": "₹",
    "Rs.": "₹",
    "Rs": "₹",
    "¥": "¥",
    "JPY": "¥",
    "USD": "USD",
    "CAD": "CAD",
    "AUD": "AUD",
    "CHF": "CHF",
    "CNY": "CNY",
}
_CURRENCY_TOKENS = tuple(sorted(_CURRENCY_NORMALIZATION, key=len, reverse=True))


def _match_prefix_currency(value: str) -> tuple[str, str | None]:
    for token in _CURRENCY_TOKENS:
        if not value.startswith(token):
            continue
        rest = value[len(token) :]
        if token[-1].isalpha() and (
            not rest or (not rest[0].isspace() and rest[0] not in "(+-−0123456789")
        ):
            continue
        return rest.lstrip(" \t\u00a0"), _CURRENCY_NORMALIZATION[token]
    return value, None


def _match_suffix_currency(value: str) -> tuple[str, str | None]:
    for token in _CURRENCY_TOKENS:
        if not value.endswith(token):
            continue
        head = value[: -len(token)]
        if not head:
            continue
        if token[0].isalpha() and not head[-1].isspace():
            continue
        return head.rstrip(" \t\u00a0"), _CURRENCY_NORMALIZATION[token]
    return value, None


def _strip_single_currency(value: str) -> tuple[str, str | None]:
    stripped, currency = _match_prefix_currency(value)
    if currency is not None:
        return stripped, currency
    return _match_suffix_currency(value)


def _extract_sign_and_currency(text: str) -> tuple[str, bool, str | None] | None:
    """Parse optional currency and outer sign/parentheses without altering numeric digits."""
    candidate = text.strip(" \t\u00a0")
    if not candidate:
        return None
    if candidate.startswith("(") and candidate.endswith(")"):
        inner = candidate[1:-1].strip(" \t\u00a0")
        if inner.startswith(("+", "-", "−")):
            return None
        inner, currency = _strip_single_currency(inner)
        if inner.startswith(("+", "-", "−")):
            return None
        return inner, True, currency

    lead_sign = ""
    if candidate[0] in ("+", "-", "−"):
        lead_sign = "-" if candidate[0] == "−" else candidate[0]
        candidate = candidate[1:].lstrip(" \t\u00a0")
        if not candidate:
            return None

    candidate, currency = _match_prefix_currency(candidate)
    if not candidate:
        return None

    if not lead_sign and candidate.startswith("(") and candidate.endswith(")"):
        inner = candidate[1:-1].strip(" \t\u00a0")
        if inner.startswith(("+", "-", "−")):
            return None
        return inner, True, currency

    if not lead_sign and candidate[0] in ("+", "-", "−"):
        lead_sign = "-" if candidate[0] == "−" else candidate[0]
        candidate = candidate[1:].lstrip(" \t\u00a0")
    elif lead_sign and candidate[0] in ("+", "-", "−"):
        return None

    if currency is None:
        candidate, currency = _match_suffix_currency(candidate)

    if not candidate:
        return None
    return (lead_sign + candidate), False, currency


def _normalize_decimal(body: str, negative: bool, mode: ValueInterpretation) -> Decimal | None:
    if mode == "en_us_number":
        if not _US_SIGNED.fullmatch(body):
            return None
        raw = body.replace(",", "")
    elif mode == "eu_number":
        if not _EU_SIGNED.fullmatch(body):
            return None
        raw = body.replace(".", "").replace(" ", "").replace("\u00a0", "").replace(",", ".")
    elif mode == "indian_number":
        if not _INDIAN_SIGNED.fullmatch(body):
            return None
        raw = body.replace(",", "")
    else:
        return None
    parsed = Decimal(raw)
    if negative:
        parsed = parsed.copy_negate()
    if len(parsed.as_tuple().digits) > 15:
        return None
    return parsed


def interpret_value(
    text: str, role: CellRole, interpretation: ValueInterpretation = "text"
) -> InterpretedValue:
    def literal(reason: str) -> InterpretedValue:
        return InterpretedValue(kind="blank" if not text else "text", raw_text=text, reason=reason)

    if role not in ("data", "value"):
        return literal("Header and label cells remain text")
    if interpretation in ("text", "uncertain", "auto"):
        return literal("No explicit numeric interpretation")
    if not text:
        return literal("Blank source value")

    if interpretation == "dash_zero":
        if text.strip(" \t\u00a0") in _DASH_ZERO:
            return InterpretedValue(
                kind="number",
                decimal_value=Decimal(0),
                raw_text=text,
                reason="Explicit dash-as-zero financial interpretation",
            )
        effective_mode: ValueInterpretation = "en_us_number"
    else:
        effective_mode = interpretation

    if effective_mode == "percent":
        candidate = text.strip(" \t\u00a0")
        negative = candidate.startswith("(") and candidate.endswith(")")
        if negative:
            candidate = candidate[1:-1].strip(" \t\u00a0")
            if candidate.startswith(("+", "-", "−")):
                return literal("Ambiguous accounting sign")
        if not candidate.endswith("%"):
            return literal("Percent interpretation requires a percent sign")
        body = candidate[:-1].rstrip(" \t\u00a0")
        if body.startswith("−"):
            body = "-" + body[1:]
        parsed = _normalize_decimal(body, negative, "en_us_number")
        if parsed is None:
            parsed = _normalize_decimal(body, negative, "eu_number")
        if parsed is None:
            return literal(
                "Ambiguous formatting, identifier, footnote or unsupported numeric convention"
            )
        return InterpretedValue(
            kind="percent",
            decimal_value=parsed / Decimal(100),
            raw_text=text,
            reason="Explicit validated percentage interpretation",
        )

    extracted = _extract_sign_and_currency(text)
    if extracted is None:
        return literal("Ambiguous accounting sign or blank value")
    body, negative, currency_symbol = extracted
    parsed = _normalize_decimal(body, negative, effective_mode)
    if parsed is None:
        return literal(
            "Ambiguous formatting, identifier, footnote or unsupported numeric convention"
        )
    return InterpretedValue(
        kind="number",
        decimal_value=parsed,
        currency_symbol=currency_symbol,
        raw_text=text,
        reason=f"Explicit validated {effective_mode} numeric interpretation",
    )


def infer_interpretation(text: str, role: CellRole = "data") -> ValueInterpretation:
    """Infer the most specific safe interpretation matching a cell's text and role."""
    stripped = text.strip(" \t\u00a0")
    if not stripped:
        return "uncertain"
    if role not in ("data", "value"):
        return "text"
    if stripped in _DASH_ZERO:
        return "dash_zero"
    for candidate in ("percent", "en_us_number", "indian_number", "eu_number"):
        parsed = interpret_value(stripped, "data", candidate)
        if parsed.kind in ("number", "percent") and parsed.decimal_value is not None:
            return candidate
    return "text"
