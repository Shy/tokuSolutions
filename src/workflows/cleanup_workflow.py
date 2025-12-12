"""Cleanup workflow - Improve translation quality with 3-stage pipeline."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.activities import (
        ftfy_cleanup_activity,
        rule_based_cleanup_activity,
        gemini_cleanup_activity,
    )


@dataclass
class CleanupInput:
    """Input for cleanup workflow."""
    json_path: str
    product_name: str
    product_description: str
    skip_gemini: bool = False


@dataclass
class CleanupOutput:
    """Output from cleanup workflow."""
    ftfy_fixes: int
    rule_removals: int
    gemini_corrections: int
    corrected_product_name: str | None
    tags: list[str] | None
    success: bool
    error: str | None = None


QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)

LLM_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


@workflow.defn
class CleanupWorkflow:
    """
    Child workflow for cleanup phase.

    Three-stage pipeline:
    1. ftfy - Fix Unicode/OCR corruption (deterministic)
    2. Rule-based - Remove noise patterns (deterministic)
    3. Gemini - AI corrections + tagging (non-deterministic, optional)
    """

    @workflow.run
    async def run(self, input: CleanupInput) -> CleanupOutput:
        workflow.logger.info(f"[Cleanup Workflow] Starting 3-stage cleanup")

        # Stage 1: ftfy (deterministic)
        workflow.logger.info("[Cleanup 1/3] ftfy - Fixing Unicode/OCR corruption...")
        ftfy_result = await workflow.execute_activity(
            ftfy_cleanup_activity,
            input.json_path,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=QUICK_RETRY,
        )

        ftfy_fixes = 0
        if ftfy_result.get("success"):
            ftfy_fixes = ftfy_result["fixes"]
            workflow.logger.info(f"[Cleanup 1/3] ✓ Fixed {ftfy_fixes} encoding issues")
        else:
            workflow.logger.warning(f"[Cleanup 1/3] Warning: {ftfy_result.get('error')}")

        # Stage 2: Rule-based (deterministic)
        workflow.logger.info("[Cleanup 2/3] Rule-based - Removing artifacts...")
        rules_result = await workflow.execute_activity(
            rule_based_cleanup_activity,
            input.json_path,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=QUICK_RETRY,
        )

        rule_removals = 0
        if rules_result.get("success"):
            rule_removals = rules_result["removals"]
            workflow.logger.info(f"[Cleanup 2/3] ✓ Removed {rule_removals} noise blocks")
        else:
            workflow.logger.warning(f"[Cleanup 2/3] Warning: {rules_result.get('error')}")

        # Stage 3: Gemini (non-deterministic, optional)
        gemini_corrections = 0
        corrected_name = None
        tags = None
        gemini_error = None

        if not input.skip_gemini:
            workflow.logger.info("[Cleanup 3/3] Gemini - Applying AI corrections...")
            gemini_result = await workflow.execute_activity(
                gemini_cleanup_activity,
                args=[input.json_path, input.product_name, input.product_description],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=LLM_RETRY,
                heartbeat_timeout=timedelta(minutes=2),
            )

            if gemini_result.get("success"):
                gemini_corrections = gemini_result["corrections"]
                corrected_name = gemini_result.get("corrected_product_name")
                tags = gemini_result.get("tags")
                workflow.logger.info(f"[Cleanup 3/3] ✓ Applied {gemini_corrections} AI corrections")
                if corrected_name:
                    workflow.logger.info(f"[Cleanup 3/3] Updated product name: {corrected_name}")
                if tags:
                    workflow.logger.info(f"[Cleanup 3/3] Applied tags: {tags}")
            else:
                # Non-fatal - Gemini can fail, warn but continue
                gemini_error = gemini_result.get("error", "Unknown error")
                workflow.logger.warning(f"[Cleanup 3/3] Warning: {gemini_error}")
        else:
            workflow.logger.info("[Cleanup 3/3] Skipped (--skip-cleanup flag)")

        workflow.logger.info("[Cleanup] Complete")

        return CleanupOutput(
            ftfy_fixes=ftfy_fixes,
            rule_removals=rule_removals,
            gemini_corrections=gemini_corrections,
            corrected_product_name=corrected_name,
            tags=tags,
            success=True,  # Cleanup never fails entirely (graceful degradation)
            error=gemini_error,  # Track Gemini errors but don't fail workflow
        )
