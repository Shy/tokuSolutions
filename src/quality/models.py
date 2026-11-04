"""Validated artifacts for an isolated translation-quality benchmark."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Artifact(BaseModel):
    """Reject misspelled fields and non-finite numbers in benchmark artifacts."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Block(Artifact):
    """One source region; coordinates use normalized x, y, width, height."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    bbox: tuple[float, float, float, float]
    translation: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_box(self) -> Self:
        """Reject empty or out-of-page crops instead of silently clamping them."""
        left, top, width, height = self.bbox
        if (
            min(left, top) < 0
            or min(width, height) <= 0
            or left + width > 1.000001
            or top + height > 1.000001
        ):
            raise ValueError("bbox must be a nonempty normalized page region")
        return self


class Case(Artifact):
    """One explicit PDF page and its existing manual baseline."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    pdf: str
    manual: str
    page: int = Field(ge=1)
    features: list[str]


class Manifest(Artifact):
    """Source selection, separate from generated run data."""

    name: str
    dpi: int = Field(default=300, ge=72, le=600)
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        """Prevent duplicate identifiers and accidental repeated source pages."""
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate case id")
        if len({(case.pdf, case.page) for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate source page")
        return self


class FrozenCase(Case):
    """Frozen image and baseline with checksums and actual rendered dimensions."""

    image: str
    image_sha256: str
    snapshot: str
    snapshot_sha256: str
    pdf_sha256: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class Suite(Artifact):
    """Portable frozen input bundle; original PDFs are not needed for inference."""

    version: Literal[1] = 1
    name: str
    manifest_sha256: str
    dpi: int
    cases: list[FrozenCase]


class BaselineBlock(BaseModel):
    """Read existing manual fields without changing their persistence contract."""

    text: str
    translation: str
    bbox: tuple[float, float, float, float]


class BaselinePage(BaseModel):
    """Existing published page, which may have undergone destructive cleanup."""

    image: str
    blocks: list[BaselineBlock]


class Manual(BaseModel):
    """Read-only projection of an existing manual."""

    meta: dict[str, JsonValue]
    pages: list[BaselinePage]


class RunConfig(Artifact):
    """Public inference settings; never store keys or credential file contents."""

    provider: str
    model: str = ""
    device: str = "cpu"
    revision: str = ""
    precision: str = "default"
    endpoint: str = ""


class Run(Artifact):
    """Immutable configuration shared by per-page resumable results."""

    version: Literal[1] = 1
    stage: Literal["ocr", "translation"]
    suite_sha256: str
    config: RunConfig
    source_run: str | None = None
    glossary_sha256: str = ""
    glossary_policy: str = ""


class Flag(Artifact):
    """A review item, not an assertion that a translation is incorrect."""

    id: str
    block_id: str
    kind: str
    reason: str


class Result(Artifact):
    """One complete successful page with its exact input fingerprint."""

    case_id: str
    stage: Literal["ocr", "translation"]
    input_sha256: str
    blocks: list[Block] = Field(min_length=1)
    flags: list[Flag] = Field(default_factory=list)
    elapsed_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def unique_blocks(self) -> Self:
        """Keep block and flag references unambiguous."""
        if len({block.id for block in self.blocks}) != len(self.blocks):
            raise ValueError("duplicate block id")
        return self
