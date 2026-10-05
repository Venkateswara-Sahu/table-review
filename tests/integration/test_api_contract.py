from uuid import uuid4

from tests.fixtures.pdf_factory import two_page_report


def test_document_dto_excludes_storage_paths_and_openapi_is_typed(client, app, tmp_path):
    path = two_page_report(tmp_path / "contract.pdf")
    response = client.post(
        "/api/v1/documents",
        files={"file": ("contract.pdf", path.read_bytes())},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 201
    assert "blob_key" not in response.json()
    schema = app.openapi()
    response_schema = schema["paths"]["/api/v1/documents"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/DocumentView"}
    assert "blob_key" not in schema["components"]["schemas"]["DocumentView"]["properties"]
    assert "TableView" in schema["components"]["schemas"]
    assert "ReviewState" in schema["components"]["schemas"]
