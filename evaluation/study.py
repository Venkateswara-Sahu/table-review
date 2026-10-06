"""Failure-inclusive study summaries; no external benefit from rehearsals."""

import argparse
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from table_review.documents.types import ImmutableModel
from table_review.tables.types import TableContent

Condition = Literal["spreadsheet_baseline", "review_without_checks", "review_with_checks"]
CONDITIONS = ("spreadsheet_baseline", "review_without_checks", "review_with_checks")


class FinalAudit(ImmutableModel):
    values_correct: int = Field(ge=0)
    values_total: int = Field(ge=0)
    headers_correct: int = Field(ge=0)
    headers_total: int = Field(ge=0)
    units_correct: int = Field(ge=0)
    units_total: int = Field(ge=0)
    grader: str = Field(min_length=1, max_length=100)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def bounded(self):
        for name in ("values", "headers", "units"):
            if getattr(self, name + "_correct") > getattr(self, name + "_total"):
                raise ValueError("Correct counts exceed the audited denominator")
        return self


class StudyTrial(ImmutableModel):
    participant_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    document_case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    matched_block: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    condition: Condition
    start: datetime
    end: datetime
    processing_time: float = Field(ge=0, allow_inf_nan=False)
    final_result: TableContent | None = None
    final_audit: FinalAudit | None = None
    outcome: Literal["completed", "failed", "abandoned"]
    experience: Literal["frequent", "occasional", "novice", "automated_fixture"]
    evidence_kind: Literal["developer_rehearsal", "external"]
    extraction_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    final_revision_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    final_artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    clock: str = "operator_recorded"

    @model_validator(mode="after")
    def timing(self):
        if self.start.tzinfo is None or self.end.tzinfo is None or self.end < self.start:
            raise ValueError("Use ordered timezone-aware timestamps")
        if self.processing_time > (self.end - self.start).total_seconds() + 0.001:
            raise ValueError("Processing time must be included in full task time")
        if self.condition != "spreadsheet_baseline" and not self.extraction_sha256:
            raise ValueError("Review conditions require a frozen extraction identity")
        if self.evidence_kind == "external" and self.experience == "automated_fixture":
            raise ValueError("An automated fixture is not an external participant")
        return self


class Assignment(ImmutableModel):
    participant_id: str
    document_case_id: str
    matched_block: str
    condition: Condition
    order: int


def assign_conditions(participants, cases, matched_block):
    if len(cases) != 3 or len(set(cases)) != 3 or len(set(participants)) != len(participants):
        raise ValueError("Use three distinct matched cases and unique participants")
    # Latin-square rotation balances case-condition and condition-position when
    # cohort size is a multiple of three. No person sees a case twice.
    return tuple(
        Assignment(
            participant_id=p,
            document_case_id=c,
            matched_block=matched_block,
            condition=CONDITIONS[(i + j) % 3],
            order=j + 1,
        )
        for i, p in enumerate(participants)
        for j, c in enumerate(cases)
    )


class StudyGroup(ImmutableModel):
    evidence_kind: str
    condition: str
    task_count: int
    failed_count: int
    ungraded_count: int
    median_full_seconds: float
    processing_seconds: float
    audited_counts: dict[str, int]


class StudySummary(ImmutableModel):
    groups: tuple[StudyGroup, ...]
    external_participant_count: int
    developer_participant_count: int
    claim_status: str
    median_savings: float | None
    exploratory_bootstrap_interval: tuple[float, float] | None
    target_status: str
    matched_external_blocks: int


def summarize_study(trials: tuple[StudyTrial, ...]) -> StudySummary:
    seen, frozen, grouped, matched = set(), {}, defaultdict(list), defaultdict(dict)
    for trial in trials:
        key = (trial.participant_id, trial.document_case_id)
        if key in seen:
            raise ValueError("Same-person same-table reuse invalidates the comparison")
        seen.add(key)
        if trial.condition != "spreadsheet_baseline":
            previous = frozen.setdefault(trial.document_case_id, trial.extraction_sha256)
            if previous != trial.extraction_sha256:
                raise ValueError("Conditions must share the same frozen extraction")
        grouped[trial.evidence_kind, trial.condition].append(trial)
        if trial.evidence_kind == "external":
            block = matched[trial.participant_id, trial.matched_block]
            if trial.condition in block:
                raise ValueError("One trial per condition in a matched participant block")
            block[trial.condition] = trial
    groups = []
    for (kind, condition), rows in sorted(grouped.items()):
        counts = {
            name + suffix: sum(getattr(r.final_audit, name + suffix) for r in rows if r.final_audit)
            for name in ("values", "headers", "units")
            for suffix in ("_correct", "_total")
        }
        groups.append(
            StudyGroup(
                evidence_kind=kind,
                condition=condition,
                task_count=len(rows),
                failed_count=sum(r.outcome != "completed" for r in rows),
                ungraded_count=sum(r.final_audit is None for r in rows),
                median_full_seconds=statistics.median(
                    (r.end - r.start).total_seconds() for r in rows
                ),
                processing_seconds=sum(r.processing_time for r in rows),
                audited_counts=counts,
            )
        )
    complete = [(key, block) for key, block in matched.items() if set(block) == set(CONDITIONS)]
    # Paired comparisons include failed-task durations; report accuracy/failure
    # alongside time. Cluster bootstrap by participant, never independent cells.
    savings = defaultdict(list)
    for (participant, _), block in complete:
        baseline = (
            block["spreadsheet_baseline"].end - block["spreadsheet_baseline"].start
        ).total_seconds()
        assisted = (
            block["review_with_checks"].end - block["review_with_checks"].start
        ).total_seconds()
        if baseline > 0:
            savings[participant].append((baseline - assisted) / baseline)
    values = [statistics.median(v) for v in savings.values()]
    median = statistics.median(values) if values else None
    interval = None
    if len(values) >= 2:
        rng = random.Random(20261006)
        draws = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(2000))
        interval = (draws[49], draws[1949])
    external = len({r.participant_id for r in trials if r.evidence_kind == "external"})
    status = (
        "exploratory_external"
        if complete
        else (
            "exploratory_external_incomplete"
            if external
            else "developer_rehearsal"
            if trials
            else "not_started"
        )
    )
    return StudySummary(
        groups=tuple(groups),
        external_participant_count=external,
        developer_participant_count=len(
            {r.participant_id for r in trials if r.evidence_kind == "developer_rehearsal"}
        ),
        claim_status=status,
        median_savings=median,
        exploratory_bootstrap_interval=interval,
        target_status="unmeasured"
        if median is None
        else "met_in_this_sample"
        if median >= 0.2
        else "not_met_in_this_sample",
        matched_external_blocks=len(complete),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("records", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.records.read_text(encoding="utf-8"))
    trials = tuple(StudyTrial.model_validate(r) for r in (raw if isinstance(raw, list) else [raw]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(summarize_study(trials).model_dump_json(indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
