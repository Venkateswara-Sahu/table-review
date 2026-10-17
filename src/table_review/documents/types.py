from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PageBox(ImmutableModel):
    """Top-left coordinates in points on the displayed, cropped and rotated PDF page."""

    page: int = Field(ge=1)
    left: float = Field(ge=0, allow_inf_nan=False)
    top: float = Field(ge=0, allow_inf_nan=False)
    right: float = Field(ge=0, allow_inf_nan=False)
    bottom: float = Field(ge=0, allow_inf_nan=False)
    page_width: float = Field(gt=0, allow_inf_nan=False)
    page_height: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if not (self.left <= self.right <= self.page_width + 0.01):
            raise ValueError("Invalid horizontal page coordinates")
        if not (self.top <= self.bottom <= self.page_height + 0.01):
            raise ValueError("Invalid vertical page coordinates")
        return self


class SourceReference(ImmutableModel):
    page: int = Field(ge=1)
    precision: Literal["cell", "table", "page"]
    origin: Literal["extractor", "user"] = "extractor"
    box: PageBox | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.box is not None and self.box.page != self.page:
            raise ValueError("Reference page differs from box page")
        if self.precision in ("cell", "table") and self.box is None:
            raise ValueError("Precise references require a source box")
        return self


class PdfInfo(ImmutableModel):
    page_count: int
    byte_count: int
    encrypted: bool


class PageInfo(ImmutableModel):
    page_no: int
    width: float
    height: float
    digital_text_present: bool
    requires_ocr: bool = False


class RenderedPage(ImmutableModel):
    image_path: Path
    page_box: PageBox
    pixel_width: int
    pixel_height: int
