import pytest

from table_review.documents.types import PageBox, PageInfo, SourceReference
from table_review.extraction.recovery import candidate_regions
from table_review.extraction.types import SourceToken

PAGE = PageInfo(page_no=1, width=612, height=792, digital_text_present=False, requires_ocr=True)


def tokens(*, columns=2, labels=True):
    values = []
    for row in range(3):
        top = 70 + row * 14
        texts = (["Revenue", "Expense", "Total"][row], "1,234", "(100.50)")
        for col in range(columns + 1):
            if col == 0 and not labels:
                continue
            left = 20 if col == 0 else 160 + col * 70
            box = PageBox(
                page=1,
                left=left,
                top=top,
                right=left + 40,
                bottom=top + 10,
                page_width=612,
                page_height=792,
            )
            values.append(
                SourceToken(
                    id=f"{row}:{col}",
                    text=texts[col],
                    box=box,
                    provenance=SourceReference(page=1, precision="cell", box=box),
                )
            )
    return tuple(values)


def test_aligned_values_with_labels_propose_region_but_existing_table_is_excluded():
    proposed = candidate_regions(tokens(), PAGE, ())
    assert len(proposed) == 1
    assert proposed[0].numeric_rows == 3 and proposed[0].numeric_columns == 2
    assert proposed[0].region.top == 62 and proposed[0].region.bottom == 116
    assert candidate_regions(tokens(), PAGE, (proposed[0].region,)) == ()


@pytest.mark.parametrize("columns,labels", [(1, True), (2, False)])
def test_numbered_lists_and_unlabelled_axes_do_not_propose_tables(columns, labels):
    assert candidate_regions(tokens(columns=columns, labels=labels), PAGE, ()) == ()


def test_proposal_limit_is_explicit_and_bounded():
    assert candidate_regions(tokens(), PAGE, (), limit=0) == ()
    with pytest.raises(ValueError, match="two"):
        candidate_regions(tokens(), PAGE, (), limit=3)
