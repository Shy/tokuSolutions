"""Glossary-aware translation using the current durable-agent path."""

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.activities.translation import (
        OCRBlock,
        TranslatedPayload,
        translate_blocks_activity,
    )
    from src.agent_models import GlossaryAgentInput
    from src.quality.pipeline import ManualBlock
    from src.workflows.agent_workflows import GlossaryAgentWorkflow


@dataclass
class TranslationInput:
    """Stable source regions and product context."""

    blocks: list[OCRBlock]
    source_lang: str = "ja"
    target_lang: str = "en"
    product: str = ""


@dataclass
class TranslationOutput:
    """Translated regions plus frozen evidence and nonblocking research warnings."""

    translated_blocks: list[TranslatedPayload]
    success: bool
    error: str | None = None
    notes_path: str = ""
    glossary_path: str = ""
    warnings: list[str] = field(default_factory=list)


API_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    backoff_coefficient=2.0,
)


@workflow.defn
class TranslationWorkflow:
    """Research names automatically, freeze constraints, then translate."""

    @workflow.run
    async def run(self, input: TranslationInput) -> TranslationOutput:
        warnings: list[str] = []
        use_web_research = True
        if input.source_lang == "ja" and input.target_lang == "en":
            researched = await workflow.execute_child_workflow(
                GlossaryAgentWorkflow.run,
                GlossaryAgentInput(
                    product=input.product,
                    blocks=[
                        ManualBlock.model_validate(block) for block in input.blocks
                    ],
                ),
                id=f"{workflow.info().workflow_id}-glossary-agent",
            )
            use_web_research = False
            if not researched:
                warnings.append(
                    "Glossary agent research unavailable; existing verified terms retained"
                )
        result = await workflow.execute_activity(
            translate_blocks_activity,
            args=[
                input.blocks,
                input.source_lang,
                input.target_lang,
                input.product,
                use_web_research,
            ],
            start_to_close_timeout=timedelta(minutes=20),
            retry_policy=API_RETRY,
        )
        if not result.success:
            return TranslationOutput([], False, result.error, warnings=warnings)
        source_regions = [
            (
                block["text"],
                block["page"],
                block["x"],
                block["y"],
                block["width"],
                block["height"],
            )
            for block in input.blocks
        ]
        translated_regions = [
            (block.original, block.page, block.x, block.y, block.width, block.height)
            for block in result.blocks
        ]
        if translated_regions != source_regions:
            return TranslationOutput(
                [],
                False,
                "Translation changed or omitted source regions",
                warnings=warnings,
            )
        blocks: list[TranslatedPayload] = [
            {
                "original": block.original,
                "translated": block.translated,
                "page": block.page,
                "x": block.x,
                "y": block.y,
                "width": block.width,
                "height": block.height,
            }
            for block in result.blocks
        ]
        return TranslationOutput(
            blocks,
            True,
            notes_path=result.notes_path,
            glossary_path=result.glossary_path,
            warnings=warnings,
        )
