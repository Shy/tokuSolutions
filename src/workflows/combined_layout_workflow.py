"""Compose parsing, source-grounded fusion and semantic layout as durable stages."""

import asyncio
from dataclasses import replace
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.activities.combined_layout import build_combined_index_activity
    from src.activities.layout_benchmark import (
        docling_layout_activity,
        google_layout_activity,
        inspect_layout_input_activity,
    )
    from src.combined_models import CombinedLayoutInput
    from src.layout_models import LayoutBenchmarkInput
    from src.workflows.semantic_layout_workflow import SemanticLayoutWorkflow


@workflow.defn(failure_exception_types=[Exception])
class CombinedLayoutWorkflow:
    """Require both structural engines; isolate their retries and model-worker capacity."""

    @workflow.run
    async def run(self, request: CombinedLayoutInput) -> list[str]:
        if not request.parser_queue:
            raise ValueError("Combined layout requires an explicit parser worker queue")
        slots = asyncio.Semaphore(2)

        async def parse(job: LayoutBenchmarkInput) -> None:
            async with slots:
                if len(job.providers) != 1 or job.providers[0] not in {
                    "docling",
                    "google",
                }:
                    raise ValueError("Unsupported combined parser job")
                await workflow.execute_activity(
                    inspect_layout_input_activity,
                    job,
                    task_queue=request.parser_queue or None,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(
                        maximum_attempts=2,
                        non_retryable_error_types=["ValueError", "FileNotFoundError"],
                    ),
                )
                function = (
                    docling_layout_activity
                    if job.providers[0] == "docling"
                    else google_layout_activity
                )
                await workflow.execute_activity(
                    function,
                    job,
                    task_queue=request.parser_queue or None,
                    start_to_close_timeout=timedelta(hours=1),
                    heartbeat_timeout=timedelta(minutes=3),
                    retry_policy=RetryPolicy(
                        maximum_attempts=2,
                        non_retryable_error_types=[
                            "ValueError",
                            "FileNotFoundError",
                            "PermissionDenied",
                            "Unauthenticated",
                            "InvalidArgument",
                        ],
                    ),
                )

        await asyncio.gather(*(parse(job) for job in request.parsers))
        index = await workflow.execute_activity(
            build_combined_index_activity,
            request,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(
                maximum_attempts=2,
                non_retryable_error_types=["ValueError", "FileNotFoundError"],
            ),
        )
        return await workflow.execute_child_workflow(
            SemanticLayoutWorkflow.run,
            replace(request.semantic, layout_index=index),
            id=f"{workflow.info().workflow_id}-semantic",
        )
