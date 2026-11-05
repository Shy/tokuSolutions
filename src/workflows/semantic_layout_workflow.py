"""Durable semantic-page planning, independent crop translation and application."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from src.activities.semantic_layout import (
        apply_semantic_manual_activity,
        check_semantic_translation_activity,
        finish_semantic_page_activity,
        plan_semantic_page_activity,
        prepare_semantic_page_activity,
    )
    from src.semantic_models import GroupingRequest, SemanticLayoutInput
    from src.workflows.page_grouping_workflow import PageGroupingAgentWorkflow

IO_RETRY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["ValueError", "ValidationError", "FileNotFoundError"],
)


@workflow.defn(failure_exception_types=[Exception])
class SemanticPageWorkflow:
    """Keep unsafe panels as original translations while independently fixing others."""

    async def _translate(self, path: str, index: int) -> None:
        try:
            reply = await workflow.execute_child_workflow(
                PageGroupingAgentWorkflow.run,
                GroupingRequest(request_path=path),
                id=f"{workflow.info().workflow_id}-panel-{index}",
            )
            repair_path = await workflow.execute_activity(
                check_semantic_translation_activity,
                args=[path, reply, False],
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=IO_RETRY,
            )
            if repair_path:
                repaired = await workflow.execute_child_workflow(
                    PageGroupingAgentWorkflow.run,
                    GroupingRequest(request_path=repair_path),
                    id=f"{workflow.info().workflow_id}-panel-{index}-repair",
                )
                await workflow.execute_activity(
                    check_semantic_translation_activity,
                    args=[repair_path, repaired, True],
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=IO_RETRY,
                )
        except ChildWorkflowError:
            workflow.logger.warning(
                "Panel model unavailable; source translation retained: %s", path
            )

    @workflow.run
    async def run(self, request: SemanticLayoutInput, page_index: int) -> str:
        directory = f"{request.run_dir}/semantic/page-{page_index:04d}"
        plan_path = await workflow.execute_activity(
            prepare_semantic_page_activity,
            args=[
                request.json_path,
                page_index,
                directory,
                request.pdf_path,
                request.glossary_path,
            ],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=IO_RETRY,
        )
        reply = await workflow.execute_child_workflow(
            PageGroupingAgentWorkflow.run,
            GroupingRequest(request_path=plan_path),
            id=f"{workflow.info().workflow_id}-plan",
        )
        paths = await workflow.execute_activity(
            plan_semantic_page_activity,
            args=[plan_path, reply],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=IO_RETRY,
        )
        # Bounded page-local fan-out; worker capacity limits model requests across pages.
        for offset in range(0, len(paths), 3):
            await asyncio.gather(
                *(
                    self._translate(path, offset + index)
                    for index, path in enumerate(paths[offset : offset + 3])
                )
            )
        return await workflow.execute_activity(
            finish_semantic_page_activity,
            directory,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=IO_RETRY,
        )


@workflow.defn(failure_exception_types=[Exception])
class SemanticLayoutWorkflow:
    """Group and retranslate semantic panels before final publication, with no gate."""

    @workflow.run
    async def run(self, request: SemanticLayoutInput) -> list[str]:
        pages: list[str] = []
        for offset in range(0, request.page_count, 3):
            pages.extend(
                await asyncio.gather(
                    *(
                        workflow.execute_child_workflow(
                            SemanticPageWorkflow.run,
                            args=[request, page],
                            id=f"{workflow.info().workflow_id}-page-{page}",
                        )
                        for page in range(offset, min(offset + 3, request.page_count))
                    )
                )
            )
        return await workflow.execute_activity(
            apply_semantic_manual_activity,
            args=[request.json_path, pages],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=IO_RETRY,
        )
