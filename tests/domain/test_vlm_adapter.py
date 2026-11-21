import json

import httpx
import pytest

from table_review.extraction.types import ExtractionRequest
from table_review.extraction.vlm_adapter import VlmConfig, VlmExtractor
from tests.fixtures.pdf_factory import two_page_report


def test_vlm_extractor_parses_markdown_json_grounds_cells_and_hides_api_key(tmp_path):
    source = two_page_report(tmp_path / "vlm.pdf")
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        payload = {
            "tables": [
                {
                    "title": "Financial Summary",
                    "units": "USD millions",
                    "header_row_count": 1,
                    "rows": [
                        ["Metric", "2025", "2024"],
                        ["Revenue", "1,234.50", "1,100.00"],
                        ["Expense", "(123.45)", "(100.00)"],
                        ["Total", "1,111.05", "1,000.00"],
                    ],
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": f"```json\n{json.dumps(payload)}\n```",
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    secret_key = "sk-secret-do-not-leak"
    config = VlmConfig(
        base_url="https://api.example.com/v1",
        model="vision-finance-1",
        api_key=secret_key,
        deployment_mode="local",
    )
    result = VlmExtractor(config, client=client).extract(
        ExtractionRequest(source_path=source, pages=(2,))
    )

    assert len(captured_requests) == 1
    assert captured_requests[0].headers["Authorization"] == f"Bearer {secret_key}"
    assert secret_key not in result.model_dump_json()

    assert result.manifest["backend"] == "vlm"
    assert result.manifest["vlm_model"] == "vision-finance-1"
    assert result.manifest["vlm_endpoint_host"] == "api.example.com"
    assert result.manifest["remote_services"] is True
    assert len(result.tables) == 1

    table = result.tables[0]
    assert table.content.title == "Financial Summary"
    assert table.content.unit_notes == ("USD millions",)
    assert table.content.rows == 4 and table.content.columns == 3
    by_slot = {(c.row, c.col): c for c in table.content.cells}
    # Grounded against native digital tokens on page 2.
    assert by_slot[(1, 1)].reference.precision == "cell"
    assert by_slot[(1, 1)].reference.box is not None
    assert by_slot[(1, 1)].interpretation == "en_us_number"


def test_vlm_loopback_endpoint_marks_remote_services_false(tmp_path):
    source = two_page_report(tmp_path / "local_vlm.pdf")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "tables": [
                                        {
                                            "header_row_count": 1,
                                            "rows": [["Metric", "2025"], ["Revenue", "100"]],
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )
    )
    config = VlmConfig(base_url="http://localhost:11434/v1", model="qwen2.5-vl")
    result = VlmExtractor(config, client=httpx.Client(transport=transport)).extract(
        ExtractionRequest(source_path=source, pages=(2,))
    )
    assert result.manifest["remote_services"] is False
    assert result.manifest["conversion_outcome"] == "success"


def test_vlm_refuses_hosted_mode_unless_explicitly_enabled(tmp_path):
    source = two_page_report(tmp_path / "hosted.pdf")
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return httpx.Response(500)

    config = VlmConfig(
        base_url="https://api.example.com/v1",
        model="vision-1",
        deployment_mode="hosted",
        allow_vlm_in_hosted=False,
    )
    extractor = VlmExtractor(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="strict offline hosted mode"):
        extractor.extract(ExtractionRequest(source_path=source, pages=(2,)))
    assert not called


def test_vlm_malformed_json_reports_page_failure(tmp_path):
    source = two_page_report(tmp_path / "bad_vlm.pdf")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"choices": [{"message": {"content": "not valid json {"}}]}
        )
    )
    config = VlmConfig(base_url="http://127.0.0.1:8080/v1", model="local-vlm")
    result = VlmExtractor(config, client=httpx.Client(transport=httpx.MockTransport(lambda r: transport.handle_request(r)))).extract(
        ExtractionRequest(source_path=source, pages=(2,))
    )
    assert result.tables == ()
    assert result.manifest["conversion_outcome"] == "failure"
    assert result.manifest["page_outcomes"][0]["status"] == "failure"
