"""Durable fan-out keeps each layout engine in its own retryable Activity."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.activities.layout_benchmark import (
        docling_layout_activity,
        google_layout_activity,
        inspect_layout_input_activity,
        pymupdf_layout_activity,
    )
    from src.layout_models import LayoutBenchmarkInput


@workflow.defn
class LayoutBenchmarkWorkflow:
    """Freeze/validate first, then run independently cached provider Activities."""

    @workflow.run
    async def run(self, request: LayoutBenchmarkInput) -> list[str]:
        await workflow.execute_activity(
            inspect_layout_input_activity,
            request,
            start_to_close_timeout=timedelta(minutes=2),
        )

        async def extract(
            provider: Callable[[LayoutBenchmarkInput], Awaitable[str]],
        ) -> str:
            try:
                return await workflow.execute_activity(
                    provider,
                    request,
                    start_to_close_timeout=timedelta(hours=1),
                    heartbeat_timeout=timedelta(minutes=3),
                    retry_policy=RetryPolicy(
                        maximum_attempts=2,
                        non_retryable_error_types=["ValueError", "FileNotFoundError"],
                    ),
                )
            except ActivityError as error:
                return f"FAILED {provider.__name__}: {error}"

        available = {
            "docling": docling_layout_activity,
            "pymupdf": pymupdf_layout_activity,
            "google": google_layout_activity,
        }
        return await asyncio.gather(
            *(extract(available[name]) for name in request.providers)
        )
