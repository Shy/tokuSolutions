"""Translation workflow - Translate OCR'd text blocks."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.activities import TranslationResult, translate_blocks_activity


@dataclass
class TranslationInput:
    """Input for translation workflow."""
    blocks: list[dict]  # OCR blocks to translate
    source_lang: str = "ja"
    target_lang: str = "en"


@dataclass
class TranslationOutput:
    """Output from translation workflow."""
    translated_blocks: list[dict]
    success: bool
    error: str | None = None


API_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    backoff_coefficient=2.0,
)


@workflow.defn
class TranslationWorkflow:
    """
    Child workflow for translation phase.

    Translates all OCR'd text blocks using Google Translate API.
    """

    @workflow.run
    async def run(self, input: TranslationInput) -> TranslationOutput:
        workflow.logger.info(f"[Translation Workflow] Translating {len(input.blocks)} blocks")

        result: TranslationResult = await workflow.execute_activity(
            translate_blocks_activity,
            args=[input.blocks, input.source_lang, input.target_lang],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=API_RETRY,
        )

        if not result.success:
            return TranslationOutput(
                translated_blocks=[],
                success=False,
                error=result.error,
            )

        # Convert TranslatedBlock dataclasses to dicts
        translated_dict = [
            {
                "original": b.original,
                "translated": b.translated,
                "page": b.page,
                "x": b.x,
                "y": b.y,
                "width": b.width,
                "height": b.height,
            }
            for b in result.blocks
        ]

        workflow.logger.info(f"[Translation] Complete: {len(translated_dict)} blocks")

        return TranslationOutput(
            translated_blocks=translated_dict,
            success=True,
        )
