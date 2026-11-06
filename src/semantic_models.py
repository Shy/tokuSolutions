"""Credential-free semantic layout requests and local file references."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, JsonValue

from src.quality.models import Artifact


class GroupingRequest(Artifact):
    """A bounded structured request or reference to a frozen source-image request."""

    stage: Literal["plan", "translate"] = "plan"
    instructions: str = ""
    input_items: list[dict[str, JsonValue]] = Field(default_factory=list)
    model: str = ""
    max_output_tokens: int = Field(default=24000, gt=0)
    reasoning_effort: Literal["low", "medium"] = "low"
    request_path: str = ""


@dataclass
class SemanticLayoutInput:
    """One staged manual and optional original PDF and frozen glossary."""

    json_path: str
    run_dir: str
    page_count: int
    pdf_path: str = ""
    glossary_path: str = ""
    layout_index: str = ""
