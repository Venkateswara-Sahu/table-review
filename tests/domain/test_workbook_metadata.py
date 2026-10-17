import zipfile
from uuid import uuid4

from lxml import etree

from table_review.exports.types import ExportInput
from table_review.exports.workbook import write_workbook
from tests.fixtures.tables import simple_table


def test_workbook_core_properties_preserve_qname_namespace_bindings(tmp_path):
    data = ExportInput(content=simple_table(), revision_id=uuid4())
    artifact = write_workbook(data, tmp_path / "workbook.xlsx")
    with zipfile.ZipFile(artifact.path) as archive:
        root = etree.fromstring(archive.read("docProps/core.xml"))
    terms = "http://purl.org/dc/terms/"
    for name in ("created", "modified"):
        timestamp = root.find(f"{{{terms}}}{name}")
        assert timestamp is not None
        assert timestamp.text == "1980-01-01T00:00:00Z"
        qname = timestamp.get("{http://www.w3.org/2001/XMLSchema-instance}type")
        prefix, local_name = qname.split(":")
        assert timestamp.nsmap.get(prefix) == terms
        assert local_name == "W3CDTF"
