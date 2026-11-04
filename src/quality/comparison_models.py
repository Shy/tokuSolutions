"""Frozen settings, metered usage, and paired assessment artifacts."""

from datetime import date
from typing import Literal

from pydantic import Field

from src.quality.models import Artifact, Block, Flag

Category = Literal[
    "meaning",
    "omission",
    "terminology",
    "quantity",
    "action",
    "warning",
    "ocr",
    "fluency",
    "style",
]


class Pricing(Artifact):
    """Dated standard USD list rates; estimates are not account invoices."""

    model: str = "gpt-5-mini"
    as_of: date = date(2026, 11, 3)
    input_per_million: float = Field(default=0.25, ge=0)
    cached_per_million: float = Field(default=0.025, ge=0)
    output_per_million: float = Field(default=2.0, ge=0)
    source: str = "https://developers.openai.com/api/docs/models/gpt-5-mini"


class MeteredCall(Artifact):
    """Record actual returned token counts and the server's resolved model name."""

    stage: Literal["cleanup", "assessment"]
    model: str
    response_id: str
    input_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)
    output_token_limit: int | None = Field(default=None, gt=0)

    def estimated_usd(self, pricing: Pricing) -> float | None:
        """Apply known model rates, without counting reasoning output twice."""
        if self.input_tokens is None or self.output_tokens is None:
            return None
        if self.model != pricing.model and not self.model.startswith(
            pricing.model + "-"
        ):
            return None
        if self.cached_tokens > self.input_tokens:
            raise ValueError("cached usage exceeds total input tokens")
        return (
            (self.input_tokens - self.cached_tokens) * pricing.input_per_million
            + self.cached_tokens * pricing.cached_per_million
            + self.output_tokens * pricing.output_per_million
        ) / 1_000_000


class Finding(Artifact):
    """One source-anchored AI judgment; A/B identities are hidden from the judge."""

    block_id: str
    related_ids: list[str] = Field(default_factory=list)
    category: Category
    severity: Literal["minor", "major", "critical"]
    a_wrong: bool
    b_wrong: bool
    source_quote: str
    expected_meaning: str
    explanation: str
    uncertainty: str


class Assessment(Artifact):
    """Every editable region must be explicitly covered once per assessment."""

    covered_ids: list[str]
    findings: list[Finding]


class ComparisonIssue(Artifact):
    """Conservative reconciliation of two independently requested assessments."""

    block_id: str
    related_ids: list[str] = Field(default_factory=list)
    category: Category
    severity: Literal["minor", "major", "critical"]
    status: Literal["fixed", "introduced", "unresolved"]
    source_quote: str
    expected_meaning: str
    explanation: str
    uncertainty: str

    @property
    def substantive(self) -> bool:
        """Keep fluency and stylistic preferences outside substantive error totals."""
        return self.category not in {"fluency", "style"}


class PageComparison(Artifact):
    """Resumable source-preserving page result, with runtime and assessment coverage."""

    case_id: str
    baseline: list[Block]
    candidate: list[Block]
    cleanup_seconds: float = Field(ge=0)
    assessment_seconds: float = Field(ge=0)
    issues: list[ComparisonIssue]
    diagnostics: list[Flag]
    calls: list[MeteredCall] = Field(default_factory=list)
    complete: bool = True
    error: str = ""
