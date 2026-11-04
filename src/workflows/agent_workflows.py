"""Durable agent orchestration with I/O confined to Activities."""

from datetime import timedelta
from typing import cast

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from agents import Agent, ModelSettings, RunConfig, Runner, WebSearchTool
    from agents.items import TResponseInputItem

    from src.activities.agent_pipeline import (
        apply_agent_cleanup_activity,
        load_agent_request_activity,
        load_instruction_request_activity,
        merge_glossary_agent_activity,
        prepare_agent_cleanup_activity,
        prepare_glossary_agent_activity,
        save_agent_reply_activity,
    )
    from src.activities.cleanup import CleanupResult
    from src.agent_models import AgentCallInput, AgentReply, GlossaryAgentInput
    from src.cleanup import OpenAICleanupResponse
    from src.quality.instruction_check import InstructionReview
    from src.quality.web_research import (
        DOMAINS,
        GLOSSARY_MAX_OUTPUT_TOKENS,
        ResearchFindings,
        SearchAction,
        glossary_reasoning,
    )


IO_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    non_retryable_error_types=["ValueError", "ValidationError", "FileNotFoundError"],
)


@workflow.defn(failure_exception_types=[Exception])
class DurableAgentWorkflow:
    """Run a structured Agent whose model calls become plugin-managed Activities."""

    @workflow.run
    async def run(self, input: AgentCallInput) -> AgentReply:
        if input.cleanup_plan is not None:
            if input.request_phase == "cleanup":
                input = await workflow.execute_activity(
                    load_agent_request_activity,
                    args=[input.cleanup_plan, input.batch_index],
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=IO_RETRY,
                )
            else:
                input = await workflow.execute_activity(
                    load_instruction_request_activity,
                    args=[input.cleanup_plan, input.batch_index, input.request_phase],
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=IO_RETRY,
                )
        schema = (
            OpenAICleanupResponse
            if input.kind == "cleanup"
            else InstructionReview
            if input.kind == "instruction"
            else ResearchFindings
        )
        agent = Agent[None](
            name="Toku " + input.kind,
            instructions=input.instructions,
            model=input.model,
            output_type=schema,
            tools=[WebSearchTool(filters={"allowed_domains": list(DOMAINS)})]
            if input.kind == "glossary"
            else [],
            model_settings=ModelSettings(
                max_tokens=32000
                if input.kind == "cleanup"
                else GLOSSARY_MAX_OUTPUT_TOKENS,
                reasoning=glossary_reasoning(input.model)
                if input.kind in {"glossary", "instruction"}
                else None,
                tool_choice="required" if input.kind == "glossary" else None,
                response_include=["web_search_call.action.sources"]
                if input.kind == "glossary"
                else None,
                extra_body={"max_tool_calls": 6} if input.kind == "glossary" else None,
                retry={"max_retries": 0},
            ),
        )
        result = await Runner.run(
            agent,
            input=cast(list[TResponseInputItem], input.input_items)
            if input.input_items
            else input.prompt,
            max_turns=3,
            run_config=RunConfig(tracing_disabled=True),
        )
        output = result.final_output
        if not isinstance(output, schema):
            raise ApplicationError(
                "agent returned an unexpected output type", non_retryable=True
            )
        urls: set[str] = set()
        for response in result.raw_responses:
            for item in response.output:
                if item.type != "web_search_call" or item.action is None:
                    continue
                action = SearchAction.model_validate(item.action.model_dump())
                urls.update(source.url for source in action.sources or [])
                if action.url and action.type in {"open_page", "find_in_page"}:
                    urls.add(action.url)
        return AgentReply(
            output_json=output.model_dump_json(),
            consulted_urls=sorted(urls),
            input_tokens=sum(
                response.usage.input_tokens for response in result.raw_responses
            ),
            output_tokens=sum(
                response.usage.output_tokens for response in result.raw_responses
            ),
        )


@workflow.defn(failure_exception_types=[Exception])
class AgentCleanupWorkflow:
    """Prepare bounded batches, durably run each, then apply the complete result."""

    @workflow.run
    async def run(
        self,
        json_path: str,
        product: str,
        description: str,
        glossary_path: str,
        block_ids: list[str] | None = None,
    ) -> CleanupResult:
        try:
            plan = await workflow.execute_activity(
                prepare_agent_cleanup_activity,
                args=[json_path, product, description, glossary_path, block_ids],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=IO_RETRY,
            )
            if plan.error:
                return {"success": False, "corrections": 0, "error": plan.error}
            for index in range(plan.batch_count):
                request = AgentCallInput(
                    kind="cleanup",
                    instructions="",
                    prompt="",
                    model="",
                    cleanup_plan=plan,
                    batch_index=index,
                )
                reply = await workflow.execute_child_workflow(
                    DurableAgentWorkflow.run,
                    request,
                    id=f"{workflow.info().workflow_id}-batch-{index}",
                )
                await workflow.execute_activity(
                    save_agent_reply_activity,
                    args=[plan, index, reply],
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=IO_RETRY,
                )
            return await workflow.execute_activity(
                apply_agent_cleanup_activity,
                plan,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=IO_RETRY,
            )
        except (ActivityError, ChildWorkflowError) as error:
            workflow.logger.warning(
                f"Durable cleanup unavailable: {type(error).__name__}"
            )
            return {
                "success": False,
                "corrections": 0,
                "error": "AI cleanup unavailable; translated draft retained.",
            }


@workflow.defn(failure_exception_types=[Exception])
class GlossaryAgentWorkflow:
    """Research and verify terminology without requiring a human decision."""

    @workflow.run
    async def run(self, input: GlossaryAgentInput) -> bool:
        try:
            plan = await workflow.execute_activity(
                prepare_glossary_agent_activity,
                input,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=IO_RETRY,
            )
            if plan.request is not None:
                reply = await workflow.execute_child_workflow(
                    DurableAgentWorkflow.run,
                    plan.request,
                    id=f"{workflow.info().workflow_id}-research",
                )
                await workflow.execute_activity(
                    merge_glossary_agent_activity,
                    args=[plan, reply],
                    start_to_close_timeout=timedelta(minutes=15),
                    retry_policy=IO_RETRY,
                )
            return True
        except (ActivityError, ChildWorkflowError) as error:
            workflow.logger.warning(
                f"Durable glossary research unavailable: {type(error).__name__}"
            )
            return False
