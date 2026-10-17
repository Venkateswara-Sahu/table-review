"""Failure-inclusive whole-page detection with one-to-one region matching.

Keep unresolved table-scope pages visible but outside precision/recall. These
metrics describe authored references; they do not establish annotation validity.
"""

from evaluation.metrics import CaseResult, overlap, summarize_cases


def score_page(truths, extracted, *, page, failure_code=None, adjudicated=True):
    if failure_code and extracted is not None:
        raise ValueError("A failed page cannot contain successful predictions")
    parser_outcome = extracted.manifest.get("conversion_outcome") if extracted is not None else None
    if parser_outcome == "failure":
        failure_code, extracted = "parser_conversion_failure", None
    if extracted is None and failure_code is None:
        raise ValueError("Missing predictions require an explicit failure")
    if any(t.page != page for t in truths) or len({t.id for t in truths}) != len(truths):
        raise ValueError("Page truth must have unique identities on the original page")
    tables = extracted.tables if extracted is not None else ()
    base = {
        "page": page,
        "scored": adjudicated,
        "failed_pages": int(failure_code is not None),
        "failure_code": failure_code,
        "parser_conversion_outcome": parser_outcome,
        "partial_pages": int(parser_outcome == "partial_success"),
        "predicted_tables": len(tables),
        "expected_tables": len(truths) if adjudicated else None,
    }
    if not adjudicated:
        return {
            **base,
            "true_positive_tables": None,
            "false_positive_tables": None,
            "false_negative_tables": None,
            "exact_page": None,
            "cell_summary": None,
            "matches": [],
        }
    edges = [
        sorted(
            (
                i
                for i, table in enumerate(tables)
                if table.page_no == page and overlap(truth.region, table.region) >= 0.2
            ),
            key=lambda i: (-overlap(truth.region, tables[i].region), i),
        )
        for truth in truths
    ]
    # Augmenting paths maximize the number of matches. Every output can satisfy
    # only one reference; unmatched and duplicate outputs remain false positives.
    assigned = {}

    def match(truth_index, visited):
        for prediction_index in edges[truth_index]:
            if prediction_index in visited:
                continue
            visited.add(prediction_index)
            if prediction_index not in assigned or match(assigned[prediction_index], visited):
                assigned[prediction_index] = truth_index
                return True
        return False

    for index in range(len(truths)):
        match(index, set())
    selected = {truth_index: prediction_index for prediction_index, truth_index in assigned.items()}
    records, matches = [], []
    for index, truth in enumerate(truths):
        chosen = tables[selected[index]] if index in selected else None
        records.append(
            CaseResult(
                case_id=truth.id,
                truth=truth,
                content=chosen.content if chosen else None,
                table_detected=chosen is not None,
                failure_code=failure_code,
            )
        )
        matches.append(
            {
                "case_id": truth.id,
                "prediction_index": selected.get(index),
                "prediction_id": chosen.id if chosen else None,
                "region_iou": overlap(truth.region, chosen.region) if chosen else 0,
            }
        )
    summary = summarize_cases(tuple(records)).model_dump(mode="json")
    false_positive = len(tables) - len(selected)
    return {
        **base,
        "true_positive_tables": len(selected),
        "false_positive_tables": false_positive,
        "false_negative_tables": len(truths) - len(selected),
        "exact_page": not failure_code
        and parser_outcome != "partial_success"
        and false_positive == 0
        and summary["exact_table_count"] == len(truths),
        "cell_summary": summary,
        "matches": matches,
    }
