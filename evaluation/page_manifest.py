"""Enforce complete page/reference bindings and explicit execution identities."""

import hashlib
from pathlib import Path

from evaluation.manifest import payload_digest


def validate_page_bindings(corpus, plan):
    reports = {report.id for report in corpus.reports}
    cases = {case.id: case for case in corpus.cases}
    pages, used = set(), set()
    for entry in plan["pages"]:
        identity = entry["report_id"], entry["page"]
        if (
            identity in pages
            or identity[0] not in reports
            or type(identity[1]) is not int
            or identity[1] < 1
        ):
            raise ValueError("Unknown report, duplicate page or invalid original page number")
        if type(entry["adjudicated"]) is not bool:
            raise ValueError("Page scope must be explicitly resolved or unresolved")
        pages.add(identity)
        for case_id in entry["case_ids"]:
            case = cases.get(case_id)
            if case is None or case_id in used or (case.report_id, case.page) != identity:
                raise ValueError("Page references must resolve once on their own report/page")
            if not entry["adjudicated"]:
                raise ValueError("Unresolved table scope cannot contain scored references")
            used.add(case_id)
    if used != set(cases):
        raise ValueError("Page plan must cover every corpus case exactly once")


def verify_execution_identity(frozen, root, runtime, model_manifest_sha256):
    if payload_digest(frozen) != frozen["frozen_payload_sha256"]:
        raise ValueError("Execution identity record hash differs")
    for name, expected in frozen["files"].items():
        path = (Path(root) / name).resolve()
        if not path.is_relative_to(Path(root).resolve()):
            raise ValueError("Execution identity path escapes the evaluation root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Evaluator source changed after execution identity freeze")
    for name in ("code_revision", "dependency_lock_sha256", "versions"):
        if runtime.get(name) != frozen["runtime"].get(name):
            raise ValueError("Extraction runtime differs from execution identity freeze")
    if model_manifest_sha256 != frozen["model_manifest_sha256"]:
        raise ValueError("Model manifest differs from execution identity freeze")
