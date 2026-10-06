"""Literal, physical association and geometry diagnostics with failure-inclusive counts."""

import re
from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from evaluation.manifest import CaseTruth
from table_review.documents.types import ImmutableModel
from table_review.tables.grid import validate_grid
from table_review.tables.types import TableContent


class CaseResult(ImmutableModel):
    case_id: str
    truth: CaseTruth
    content: TableContent | None = None
    table_detected: bool = True
    failure_code: str | None = None
    provenance: Literal["natural", "injected"] = "natural"
    mode: Literal["full_page", "crop"] = "full_page"
    cell_matching: bool = True
    elapsed_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    peak_rss_bytes: int = Field(default=0, ge=0)
    issue_count: int = Field(default=0, ge=0)
    issue_affected_positions: tuple[tuple[int, int], ...] = ()

    @model_validator(mode="after")
    def valid(self):
        if self.case_id != self.truth.id:
            raise ValueError("Result identity differs from its truth")
        if self.content is not None:
            validate_grid(self.content)
        if self.failure_code and self.content is not None:
            raise ValueError("A failed result cannot also contain a successful table")
        return self


class CaseMetrics(ImmutableModel):
    literal_occurrence_correct: int
    literal_occurrence_total: int
    predicted_literal_occurrences: int
    literal_correct: int
    literal_total: int
    association_correct: int
    association_total: int
    geometry_correct: int
    geometry_total: int
    role_correct: int
    role_total: int
    unit_correct: bool
    structure_correct: bool
    exact_table: bool


class EvaluationSummary(ImmutableModel):
    literal_occurrence_correct: int
    literal_occurrence_total: int
    predicted_literal_occurrences: int
    literal_occurrence_recall: float | None
    case_count: int
    failure_count: int
    missing_count: int
    exact_table_count: int
    literal_correct: int
    literal_total: int
    literal_accuracy: float | None
    association_correct: int
    association_total: int
    association_accuracy: float | None
    geometry_correct: int
    geometry_total: int
    geometry_accuracy: float | None
    role_correct: int
    role_total: int
    unit_correct_count: int
    structure_correct_count: int
    natural_case_count: int
    injected_case_count: int
    elapsed_seconds: float
    peak_rss_bytes: int


def normalize(value):
    # Do not discard signs, punctuation, digits, footnotes or scale differences.
    return re.sub(r"[\t\n\r\f\v \u00a0]+", " ", value).strip()


def key(cell):
    return cell.row, cell.col, cell.row_span, cell.col_span


def associations(content, cell):
    headers = tuple(
        normalize(candidate.text)
        for candidate in sorted(content.cells, key=key)
        if candidate.row in content.header_rows
        and candidate.col < cell.col + cell.col_span
        and cell.col < candidate.col + candidate.col_span
    )
    labels = tuple(
        normalize(candidate.text)
        for candidate in sorted(content.cells, key=key)
        if candidate.col in content.row_label_columns
        and candidate.row <= cell.row
        and cell.row < candidate.row + candidate.row_span
    )
    return headers, labels


def overlap(a, b):
    if (
        a is None
        or b is None
        or a.page != b.page
        or abs(a.page_width - b.page_width) > 0.01
        or abs(a.page_height - b.page_height) > 0.01
    ):
        return 0.0
    intersection = max(0, min(a.right, b.right) - max(a.left, b.left)) * max(
        0, min(a.bottom, b.bottom) - max(a.top, b.top)
    )
    union = (
        (a.right - a.left) * (a.bottom - a.top)
        + (b.right - b.left) * (b.bottom - b.top)
        - intersection
    )
    return intersection / union if union else 0.0


def score_case(truth: CaseTruth, result: CaseResult) -> CaseMetrics:
    available = result.content if not result.failure_code and result.table_detected else None
    predicted = {key(cell): cell for cell in available.cells} if available else {}
    literals = roles = geometric = association_correct = association_total = geometry_total = 0
    for cell in truth.content.cells:
        candidate = predicted.get(key(cell))
        literals += bool(candidate and normalize(candidate.text) == normalize(cell.text))
        roles += bool(candidate and candidate.role == cell.role)
        if cell.reference.precision == "cell" and cell.reference.box:
            geometry_total += 1
            geometric += bool(
                candidate
                and candidate.reference.precision == "cell"
                and candidate.reference.page == cell.reference.page
                and overlap(candidate.reference.box, cell.reference.box) >= 0.5
            )
        expected = associations(truth.content, cell)
        if (
            cell.role in ("data", "value")
            and cell.row not in truth.content.header_rows
            and cell.col not in truth.content.row_label_columns
            and cell.text.strip()
            and any(expected)
        ):
            association_total += 1
            association_correct += bool(
                candidate and associations(available, candidate) == expected
            )
    structure = bool(
        available
        and available.rows == truth.content.rows
        and available.columns == truth.content.columns
        and set(predicted) == {key(cell) for cell in truth.content.cells}
    )
    units = bool(
        available
        and tuple(map(normalize, available.unit_notes))
        == tuple(map(normalize, truth.content.unit_notes))
    )
    exact = (
        structure
        and units
        and literals == len(truth.content.cells)
        and roles == len(truth.content.cells)
        and association_correct == association_total
    )
    return CaseMetrics(
        literal_occurrence_correct=sum(
            (
                Counter(normalize(c.text) for c in truth.content.cells if c.text.strip())
                & Counter(normalize(c.text) for c in available.cells if c.text.strip())
            ).values()
        )
        if available
        else 0,
        literal_occurrence_total=sum(bool(c.text.strip()) for c in truth.content.cells),
        predicted_literal_occurrences=sum(bool(c.text.strip()) for c in available.cells)
        if available
        else 0,
        literal_correct=literals,
        literal_total=len(truth.content.cells),
        association_correct=association_correct,
        association_total=association_total,
        geometry_correct=geometric,
        geometry_total=geometry_total,
        role_correct=roles,
        role_total=len(truth.content.cells),
        unit_correct=units,
        structure_correct=structure,
        exact_table=exact,
    )


def summarize_cases(results: tuple[CaseResult, ...]) -> EvaluationSummary:
    if len({(r.mode, r.cell_matching, r.provenance) for r in results}) > 1:
        raise ValueError("Do not mix configuration or provenance groups in one summary")
    if len({result.case_id for result in results}) != len(results):
        raise ValueError("Do not mix repeated cases/configurations in one summary")
    metrics = [score_case(result.truth, result) for result in results]
    counts = {
        name: sum(getattr(metric, name) for metric in metrics)
        for name in (
            "literal_occurrence_correct",
            "literal_occurrence_total",
            "predicted_literal_occurrences",
            "literal_correct",
            "literal_total",
            "association_correct",
            "association_total",
            "geometry_correct",
            "geometry_total",
            "role_correct",
            "role_total",
        )
    }
    ratios = {
        name + "_accuracy": counts[name + "_correct"] / counts[name + "_total"]
        if counts[name + "_total"]
        else None
        for name in ("literal", "association", "geometry")
    }
    return EvaluationSummary(
        case_count=len(results),
        failure_count=sum(result.failure_code is not None for result in results),
        missing_count=sum(
            not result.table_detected and result.failure_code is None for result in results
        ),
        exact_table_count=sum(metric.exact_table for metric in metrics),
        literal_occurrence_recall=counts["literal_occurrence_correct"]
        / counts["literal_occurrence_total"]
        if counts["literal_occurrence_total"]
        else None,
        **counts,
        **ratios,
        unit_correct_count=sum(metric.unit_correct for metric in metrics),
        structure_correct_count=sum(metric.structure_correct for metric in metrics),
        natural_case_count=sum(result.provenance == "natural" for result in results),
        injected_case_count=sum(result.provenance == "injected" for result in results),
        elapsed_seconds=sum(result.elapsed_seconds for result in results),
        peak_rss_bytes=max((result.peak_rss_bytes for result in results), default=0),
    )
