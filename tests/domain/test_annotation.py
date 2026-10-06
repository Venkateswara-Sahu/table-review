from evaluation.annotation import transcribe
from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.types import SourceToken


def test_descenders_do_not_move_a_word_to_the_end_of_a_visual_line(monkeypatch, tmp_path):
    words = [("For", 1, 10), ("the", 12, 10), ("year", 24, 12), ("ended", 36, 10)]
    tokens = []
    for index, (text, x, y) in enumerate(words):
        box = PageBox(
            page=1, left=x, right=x + 8, top=y, bottom=y + 4, page_width=100, page_height=100
        )
        tokens.append(
            SourceToken(
                id=str(index),
                text=text,
                box=box,
                provenance=SourceReference(page=1, precision="cell", box=box),
            )
        )
    monkeypatch.setattr("evaluation.annotation.source_tokens", lambda *args: tuple(tokens))
    layout = {
        "id": "fixture",
        "region": {
            "page": 1,
            "left": 0,
            "top": 0,
            "right": 100,
            "bottom": 50,
            "page_width": 100,
            "page_height": 100,
        },
        "columns": [0, 100],
        "headers": [{"row": 0, "col": 0, "top": 0, "bottom": 20}],
        "header_count": 1,
        "unit_notes": [],
    }
    content, audit = transcribe(layout, tmp_path / "fixture.pdf")
    assert content.cells[0].text == "For the year ended"
    assert audit["unassigned_tokens"] == 0
