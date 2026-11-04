"""Cleanup workflow - Improve translation quality with 3-stage pipeline."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from src.activities.cleanup import (
        ftfy_cleanup_activity,
        rule_based_cleanup_activity,
    )
    from src.workflows.agent_workflows import AgentCleanupWorkflow


@dataclass
class CleanupInput:
    """Input for cleanup workflow."""

    json_path: str
    product_name: str
    product_description: str
    skip_openai: bool = False
    glossary_path: str = ""


@dataclass
class CleanupOutput:
    """Output from cleanup workflow."""

    ftfy_fixes: int
    rule_removals: int
    openai_corrections: int
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
    1. ftfy - Normalize English encoding (deterministic)
    2. Rule-based - Normalize formatting without deleting source labels
    3. OpenAI - AI corrections + tagging (non-deterministic, optional)
    """

    @workflow.run
    async def run(self, input: CleanupInput) -> CleanupOutput:
        workflow.logger.info("[Cleanup Workflow] Starting 3-stage cleanup")

        # Stage 1: ftfy (deterministic)
        workflow.logger.info("[Cleanup 1/3] ftfy - Normalizing English encoding...")
        ftfy_result = await workflow.execute_activity(
            ftfy_cleanup_activity,
            input.json_path,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=QUICK_RETRY,
        )

        warnings: list[str] = []
        ftfy_fixes = 0
        if ftfy_result.get("success"):
            ftfy_fixes = ftfy_result["fixes"]
            workflow.logger.info(f"[Cleanup 1/3] ✓ Fixed {ftfy_fixes} encoding issues")
        else:
            warnings.append(f"Encoding cleanup unavailable: {ftfy_result.get('error')}")

        # Stage 2: Rule-based (deterministic)
        workflow.logger.info("[Cleanup 2/3] Rule-based - Normalizing formatting...")
        rules_result = await workflow.execute_activity(
            rule_based_cleanup_activity,
            input.json_path,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=QUICK_RETRY,
        )

        rule_removals = 0
        if rules_result.get("success"):
            rule_removals = rules_result["removals"]
            workflow.logger.info(
                f"[Cleanup 2/3] ✓ Applied {rule_removals} formatting corrections"
            )
        else:
            warnings.append(
                f"Formatting cleanup unavailable: {rules_result.get('error')}"
            )

        # Stage 3: OpenAI (non-deterministic, optional)
        openai_corrections = 0
        corrected_name = None
        tags = None
        openai_error = None

        if not input.skip_openai:
            workflow.logger.info("[Cleanup 3/3] OpenAI - Applying AI corrections...")
            try:
                openai_result = await workflow.execute_child_workflow(
                    AgentCleanupWorkflow.run,
                    args=[
                        input.json_path,
                        input.product_name,
                        input.product_description,
                        input.glossary_path,
                    ],
                    id=f"{workflow.info().workflow_id}-agent",
                )
            except ChildWorkflowError:
                openai_result = {
                    "success": False,
                    "corrections": 0,
                    "error": "Agent cleanup unavailable; draft retained.",
                }

            if openai_result.get("success"):
                openai_corrections = openai_result["corrections"]
                corrected_name = openai_result.get("corrected_product_name")
                tags = openai_result.get("tags")
                workflow.logger.info(
                    f"[Cleanup 3/3] ✓ Applied {openai_corrections} AI corrections"
                )
                if corrected_name:
                    workflow.logger.info(
                        f"[Cleanup 3/3] Updated product name: {corrected_name}"
                    )
                if tags:
                    workflow.logger.info(f"[Cleanup 3/3] Applied tags: {tags}")
            else:
                # Non-fatal - OpenAI can fail, warn but continue
                openai_error = openai_result.get("error", "Unknown error")
                workflow.logger.warning(f"[Cleanup 3/3] Warning: {openai_error}")
        else:
            workflow.logger.info("[Cleanup 3/3] Skipped (--skip-cleanup flag)")

        if openai_error:
            warnings.append(openai_error)
        workflow.logger.info("[Cleanup] Complete")

        return CleanupOutput(
            ftfy_fixes=ftfy_fixes,
            rule_removals=rule_removals,
            openai_corrections=openai_corrections,
            corrected_product_name=corrected_name,
            tags=tags,
            success=True,  # Cleanup never fails entirely (graceful degradation)
            error="; ".join(warnings) or None,
        )
