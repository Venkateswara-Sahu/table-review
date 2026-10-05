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
