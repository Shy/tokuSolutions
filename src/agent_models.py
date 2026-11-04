"""Credential-free payloads for durable model calls and file-backed agent plans."""

from typing import Literal

from pydantic import Field, JsonValue

from src.quality.models import Artifact
from src.quality.pipeline import ManualBlock


class AgentCleanupPlan(Artifact):
    """Small plan reference; images are loaded one batch at a time."""

    directory: str
    batch_count: int
    error: str = ""


class AgentCallInput(Artifact):
    """One bounded model request; provider credentials stay on the Worker."""

    kind: Literal["cleanup", "glossary", "instruction"]
    instructions: str
    prompt: str
    model: str
    input_items: list[dict[str, JsonValue]] = Field(default_factory=list)
    cleanup_plan: AgentCleanupPlan | None = None
    batch_index: int = 0
    request_phase: Literal["cleanup", "check", "confirm"] = "cleanup"


class AgentReply(Artifact):
    """A structured output and hosted-tool provenance recorded by Temporal."""

    output_json: str
    consulted_urls: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class GlossaryAgentInput(Artifact):
    """Original OCR context used to research a single manual's names."""

    product: str
    blocks: list[ManualBlock]


class GlossaryAgentPlan(Artifact):
    """A source snapshot reference and optional bounded research request."""

    directory: str
    request: AgentCallInput | None = None


class InstructionCheckResult(Artifact):
    """A filtered reply and whether a bounded repair needs its confirmation call."""

    reply: AgentReply
    needs_confirmation: bool = False
