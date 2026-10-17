import json
import subprocess
import sys

import pytest

from tests.fixtures.pdf_factory import two_page_report


@pytest.mark.parametrize("kind", ["info", "render"])
def test_preview_child_does_not_load_application_or_full_pdf_reader(tmp_path, kind):
    source = two_page_report(tmp_path / "source.pdf")
    payload = {
        "kind": kind,
        "source_path": str(source),
        "page_no": 2,
        "scale": 1.5,
        "target": str(tmp_path / "page.png"),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; from table_review.documents.pdf_child import process; "
                "result=process(json.loads(sys.argv[1])); "
                "print(json.dumps({'result':result,'heavy_modules':"
                "[name for name in ('pydantic','pypdf','table_review.settings') if name in sys.modules]}))"
            ),
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    output = json.loads(completed.stdout)
    assert output["heavy_modules"] == []
    if kind == "info":
        assert output["result"]["page_no"] == 2
        assert output["result"]["digital_text_present"]
    else:
        assert (tmp_path / "page.png").is_file()
        assert output["result"]["page_box"]["page"] == 2
