from decimal import Decimal

import pytest


@pytest.mark.parametrize(
    "text",
    [
        "00123",
        "1234567890123456",
        "25*",
        "12,34",
        "1.234,50",
        "—",
        "-",
        "",
        "25%",
        "=1+1",
        "NaN",
        "1e6",
        "(1)-",
        "1 234",
        "0x10",
        "0.1234567890123456",
    ],
)
def test_ambiguous_values_remain_literal(text):
    from table_review.tables.numbers import interpret_value

    result = interpret_value(text, "value", "en_us_number")
    assert result.kind in ("text", "blank")
    assert result.raw_text == text and result.decimal_value is None


@pytest.mark.parametrize(
    "text,value",
    [
        ("(1,234.50)", "-1234.50"),
        ("-12.50", "-12.50"),
        ("1,234", "1234"),
        ("123456789012345", "123456789012345"),
        ("0", "0"),
        ("0.00125", "0.00125"),
        ("+12", "12"),
    ],
)
def test_explicit_safe_numbers(text, value):
    from table_review.tables.numbers import interpret_value

    result = interpret_value(text, "value", "en_us_number")
    assert result.kind == "number" and result.decimal_value == Decimal(value)
    assert result.raw_text == text


def test_percent_is_explicit_and_uses_fraction():
    from table_review.tables.numbers import interpret_value

    result = interpret_value("(12.50%)", "value", "percent")
    assert result.kind == "percent" and result.decimal_value == Decimal("-0.1250")
    assert interpret_value("12.50", "value", "percent").kind == "text"


@pytest.mark.parametrize("role", ["label", "header", "column_header", "row_header", "section"])
def test_headers_never_become_numbers(role):
    from table_review.tables.numbers import interpret_value

    assert interpret_value("2025", role, "en_us_number").kind == "text"


def test_uncertain_and_text_interpretation_preserve_source():
    from table_review.tables.numbers import interpret_value

    assert interpret_value("12.50", "value", "uncertain").kind == "text"
    assert interpret_value("00123", "label", "text").raw_text == "00123"


def test_accounting_negative_zero_retains_sign():
    from table_review.tables.numbers import interpret_value

    assert interpret_value("(0.00)", "value", "en_us_number").decimal_value.as_tuple().sign == 1
    assert interpret_value("($0.00)", "value", "en_us_number").decimal_value.as_tuple().sign == 1


@pytest.mark.parametrize(
    "text,mode,value,currency",
    [
        ("$1,234.50", "en_us_number", "1234.50", "$"),
        ("$ 1,234.50", "en_us_number", "1234.50", "$"),
        ("($1,234.50)", "en_us_number", "-1234.50", "$"),
        ("$(1,234.50)", "en_us_number", "-1234.50", "$"),
        ("-$1,234", "en_us_number", "-1234", "$"),
        ("$-1,234", "en_us_number", "-1234", "$"),
        ("1,234.00 USD", "en_us_number", "1234.00", "USD"),
        ("€ 1.234,50", "eu_number", "1234.50", "€"),
        ("1 234,50 €", "eu_number", "1234.50", "€"),
        ("(1.234,50)", "eu_number", "-1234.50", None),
        ("₹ 1,23,456.78", "indian_number", "123456.78", "₹"),
        ("Rs. 12,34,567", "indian_number", "1234567", "₹"),
        ("—", "dash_zero", "0", None),
        ("-", "dash_zero", "0", None),
        ("12,5%", "percent", "0.125", None),
    ],
)
def test_currency_and_locale_numbers(text, mode, value, currency):
    from table_review.tables.numbers import interpret_value

    result = interpret_value(text, "data", mode)
    expected_kind = "percent" if mode == "percent" else "number"
    assert result.kind == expected_kind
    assert result.decimal_value == Decimal(value)
    assert result.currency_symbol == currency


@pytest.mark.parametrize(
    "text,mode",
    [
        ("$€100", "en_us_number"),
        ("$100 USD", "en_us_number"),
        ("($-100)", "en_us_number"),
        ("123,456", "indian_number"),
        ("12.34", "eu_number"),
        ("$25%", "percent"),
    ],
)
def test_conflicting_currency_or_locale_mismatches_stay_literal(text, mode):
    from table_review.tables.numbers import interpret_value

    result = interpret_value(text, "data", mode)
    assert result.kind == "text"
    assert result.decimal_value is None
