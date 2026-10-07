from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


def test_task_metrics_keep_outcomes_and_timings_without_report_strings(
    app, owner, tmp_path, caplog
):
    import io
    import json
    import logging
    from uuid import uuid4

    from table_review.accounts.types import Actor
    from table_review.jobs.worker import run_once
    from tests.fixtures.pdf_factory import two_page_report

    report = two_page_report(tmp_path / "confidential-report.pdf")
    document = app.state.documents.upload(
        Actor(id=owner.id), io.BytesIO(report.read_bytes()), "confidential-report.pdf", uuid4().hex
    )

    def fail(*args):
        raise RuntimeError("secret-report-text confidential-report.pdf password=private")

    app.state.queue.handlers["prepare"] = fail
    with caplog.at_level(logging.INFO, logger="table_review.jobs.worker"):
        assert run_once(app.state.queue, "metrics-fixture")
    records = [
        record.message for record in caplog.records if record.message.startswith("task_metric ")
    ]
    assert len(records) == 1
    value = json.loads(records[0].removeprefix("task_metric "))
    assert value["kind"] == "prepare" and value["state"] in {"queued", "failed"}
    assert value["duration_seconds"] >= 0 and value["queue_age_seconds"] >= 0
    assert "secret-report-text" not in caplog.text and "confidential-report.pdf" not in caplog.text
    assert "password=" not in caplog.text and str(document.id) not in caplog.text


def test_readiness_distinguishes_database_worker_models_and_journal(app, tmp_path):
    app.state.settings = app.state.settings.model_copy(
        update={"model_path": tmp_path / "absent-models"}
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["components"] == {
        "database": True,
        "journal": True,
        "worker": False,
        "models": False,
    }
    assert "request_id" not in response.text or response.headers["Cache-Control"] == "no-store"


def test_worker_heartbeat_runs_while_native_processing_is_busy(tmp_path):
    from table_review.runtime import WorkerPulse, worker_available

    path = tmp_path / "pulse.json"
    with WorkerPulse(path, interval=0.02):
        assert worker_available(path, datetime.now(UTC))
        assert not worker_available(path, datetime.now(UTC) + timedelta(seconds=121))
    assert not worker_available(path, datetime.now(UTC))


def test_model_readiness_requires_the_recorded_files_not_an_empty_directory(tmp_path):
    from table_review.runtime import models_available, write_model_manifest

    assert not models_available(tmp_path)
    (tmp_path / "weights.safetensors").write_bytes(b"fixture")
    write_model_manifest(tmp_path)
    assert models_available(tmp_path)
    (tmp_path / "weights.safetensors").unlink()
    assert not models_available(tmp_path)


def test_cgroup_memory_contract_does_not_claim_a_sampled_limit_is_hard(tmp_path):
    from table_review.jobs.runner import memory_contract

    assert memory_contract(tmp_path)["hard_memory_enforcement"] is False
    (tmp_path / "memory.max").write_text(str(6 * 1024**3))
    contract = memory_contract(tmp_path)
    assert contract["hard_memory_enforcement"] is True
    assert contract["container_memory_limit_bytes"] == 6 * 1024**3


def test_cgroup_v1_hard_limit_is_reported_without_confusing_unlimited_sentinel(tmp_path):
    from table_review.jobs.runner import memory_contract

    folder = tmp_path / "memory"
    folder.mkdir()
    (folder / "memory.limit_in_bytes").write_text(str(6 * 1024**3))
    assert memory_contract(tmp_path)["container_memory_limit_bytes"] == 6 * 1024**3
    (folder / "memory.limit_in_bytes").write_text(str(2**63 - 4096))
    assert memory_contract(tmp_path)["hard_memory_enforcement"] is False
