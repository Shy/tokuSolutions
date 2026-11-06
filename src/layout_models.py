"""Credential-free models for resumable layout-engine comparisons."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from src.quality.models import Artifact


class LayoutRegion(Artifact):
    """One ordered region in normalized top-left page coordinates."""

    id: str
    kind: str
    text: str = ""
    bbox: tuple[float, float, float, float]


class UnlocatedLayoutRegion(Artifact):
    """A real extracted block whose provider supplied no usable coordinates."""

    id: str
    kind: str
    text: str = ""


class LayoutCell(Artifact):
    """Cell topology is independent of OCR's paragraph grouping."""

    row: int
    column: int
    row_span: int = 1
    column_span: int = 1
    text: str = ""
    bbox: tuple[float, float, float, float] | None = None
    bbox_kind: Literal["provider_cell", "text_extent"] = "provider_cell"


class LayoutTable(Artifact):
    """An extracted table, including merged cells and its source extent."""

    id: str
    bbox: tuple[float, float, float, float] | None = None
    rows: int
    columns: int
    cells: list[LayoutCell] = Field(default_factory=list)


class LayoutPage(Artifact):
    """A normalized view alongside the preserved unmodified provider response."""

    page: int
    provider: str
    source_kind: Literal["image_only", "native_text", "ocr"] = "ocr"
    regions: list[LayoutRegion] = Field(default_factory=list)
    unlocated_regions: list[UnlocatedLayoutRegion] = Field(default_factory=list)
    tables: list[LayoutTable] = Field(default_factory=list)
    elapsed_seconds: float = 0


@dataclass
class LayoutBenchmarkInput:
    """Only local artifact paths and public provider configuration enter history."""

    manifest_path: str
    output_dir: str
    docling_python: str
    providers: tuple[str, ...] = ("docling", "pymupdf", "google")
    google_processor: str = ""
    google_version: str = "pretrained-layout-parser-v1.0-2024-06-03"
