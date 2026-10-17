from types import SimpleNamespace

import pytest

from table_review.documents.types import PageInfo
from table_review.extraction.ocr import ocr_tokens


@pytest.mark.parametrize("width", [1224, float("nan"), float("inf")])
def test_scaled_parser_page_does_not_publish_precise_ocr_tokens(width):
    result = SimpleNamespace(
        document=SimpleNamespace(
            pages={1: SimpleNamespace(size=SimpleNamespace(width=width, height=792))}
        ),
        pages=[SimpleNamespace(page_no=1, parsed_page=None)],
    )
    page = PageInfo(page_no=1, width=612, height=792, digital_text_present=False)
    tokens, warnings = ocr_tokens(result, page, 1, "hash", 612, 792, 0, 0)
    assert not tokens
    assert "precise tokens omitted" in warnings[0]
