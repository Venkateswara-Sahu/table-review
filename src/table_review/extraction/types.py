from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from table_review.documents.types import ImmutableModel, PageBox, PageInfo, SourceReference
from table_review.tables.types import TableContent

ExtractionBackend = Literal["auto", "docling", "native_fast", "vlm"]


class ExtractorConfig(ImmutableModel):
    backend: ExtractionBackend = "auto"
    device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    num_threads: int = Field(default=4, ge=1, le=16)
    mode: Literal["accurate", "fast"] = "accurate"
    cell_matching: bool = True
    ocr_mode: Literal["auto", "never"] = "auto"
    table_recovery: bool = True
    mark_recovery: bool = True
    artifacts_path: Path = Path("models")


class ExtractionRequest(ImmutableModel):
    source_path: Path
    pages: tuple[int, ...]
    region: PageBox | None = None
    config: ExtractorConfig = ExtractorConfig()

    @model_validator(mode="after")
    def selected(self):
        if not 1 <= len(self.pages) <= 20 or min(self.pages) < 1:
            raise ValueError("Select 1 to 20 original page numbers")
        if len(set(self.pages)) != len(self.pages):
            raise ValueError("Duplicate pages are not allowed")
        if self.region and self.pages != (self.region.page,):
            raise ValueError("Region requests must select exactly its original page")
        return self


class SourceToken(ImmutableModel):
    id: str
    text: str
    box: PageBox
    provenance: SourceReference


class ExtractedTable(ImmutableModel):
    id: str
    page_no: int
    region: PageBox | None
    content: TableContent
    source_tokens: tuple[SourceToken, ...]


class ExtractionResult(ImmutableModel):
    tables: tuple[ExtractedTable, ...]
    page_info: tuple[PageInfo, ...]
    warnings: tuple[str, ...]
    manifest: dict[str, Any]


class ProbeSummary(ImmutableModel):
    case_count: int
    failures: int
    results_path: Path
