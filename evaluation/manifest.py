"""Source-frozen, company-disjoint evaluation inputs. No model output is ground truth."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from table_review.documents.types import ImmutableModel, PageBox
from table_review.tables.grid import validate_grid
from table_review.tables.types import TableContent


class Report(ImmutableModel):
    id: str
    company: str
    family: str
    year: int
    split: Literal["development", "heldout"]
    source_url: str
    source_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accessed: str
    access_note: str
    pretrained_exposure: str = "unknown"


class CaseTruth(ImmutableModel):
    id: str
    report_id: str
    split: Literal["development", "heldout"]
    page: int = Field(ge=1)
    title: str = ""
    region: PageBox
    content: TableContent
    annotation_method: str
    annotation_status: Literal["complete"]
    annotation_limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        validate_grid(self.content)
        if self.region.page != self.page or not self.content.cells:
            raise ValueError("Complete case truth requires cells on its original page")
        if any(cell.reference.page != self.page for cell in self.content.cells):
            raise ValueError("Truth cells must refer to the case source page")
        return self


class CorpusManifest(ImmutableModel):
    version: str = "1"
    frozen_at: datetime
    frozen_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reports: tuple[Report, ...]
    cases: tuple[CaseTruth, ...]
    annotation_protocol: str
    limitations: tuple[str, ...]
    annotation_provenance: dict[str, str] = Field(default_factory=dict)
    manifest_path: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def grouped(self):
        reports = {report.id: report for report in self.reports}
        if len(reports) != len(self.reports) or len({case.id for case in self.cases}) != len(
            self.cases
        ):
            raise ValueError("Duplicate case/report identity")
        families, companies = {}, {}
        for report in self.reports:
            if report.family in families and families[report.family] != report.split:
                raise ValueError("Company family appears in both evaluation splits")
            families[report.family] = report.split
            company = report.company.strip().casefold()
            if company in companies and companies[company] != report.split:
                raise ValueError("Company appears in both evaluation splits")
            companies[company] = report.split
        for case in self.cases:
            if case.report_id not in reports or case.split != reports[case.report_id].split:
                raise ValueError("Case differs from its report split")
        if self.frozen_at.tzinfo is None:
            raise ValueError("Manifest freeze time must include its timezone")
        return self


def payload_digest(data):
    data = {
        key: value
        for key, value in data.items()
        if key not in ("frozen_payload_sha256", "manifest_path")
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def load_manifest(path: Path) -> CorpusManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload_digest(data) != data.get("frozen_payload_sha256"):
        raise ValueError("Frozen manifest content hash differs")
    return CorpusManifest.model_validate({**data, "manifest_path": Path(path).absolute()})
