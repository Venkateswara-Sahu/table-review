from types import SimpleNamespace
from uuid import uuid4

from table_review.reviews.verdicts import review_warnings


def test_blank_header_stub_is_structural_but_blank_data_still_needs_review():
    revision = SimpleNamespace(
        id=uuid4(),
        content={
            "header_rows": [0],
            "cells": [
                {
                    "id": "corner",
                    "row": 0,
                    "col": 0,
                    "text": "",
                    "reference": {"page": 1, "precision": "page"},
                },
                {
                    "id": "value",
                    "row": 1,
                    "col": 0,
                    "text": "",
                    "reference": {"page": 1, "precision": "page"},
                },
            ],
        },
    )
    issues = review_warnings(revision, {})
    assert [issue.affected_cell_ids for issue in issues] == [("value",)]
