"""Bounded, source-linked page grouping through Temporal's official Agents plugin."""

from datetime import timedelta
from typing import cast

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from agents import Agent, ModelSettings, RunConfig, Runner
    from agents.items import TResponseInputItem
    from openai.types.shared import Reasoning

    from src.activities.semantic_layout import (
        cached_semantic_reply_activity,
        load_semantic_request_activity,
        save_semantic_reply_activity,
    )
    from src.agent_models import AgentReply
    from src.quality.page_grouping import PagePlan, PanelTranslations
    from src.semantic_models import GroupingRequest


@workflow.defn(failure_exception_types=[Exception])
class PageGroupingAgentWorkflow:
    """Return only panel membership/ruby links or source-paired panel translations."""

    @workflow.run
    async def run(self, request: GroupingRequest) -> AgentReply:
        request_path = request.request_path
        if request_path:
            cached = await workflow.execute_activity(
                cached_semantic_reply_activity,
                request_path,
                start_to_close_timeout=timedelta(minutes=1),
            )
            if cached is not None:
                return cached
            request = await workflow.execute_activity(
                load_semantic_request_activity,
                request_path,
                start_to_close_timeout=timedelta(minutes=1),
            )
        schema = PagePlan if request.stage == "plan" else PanelTranslations
        result = await Runner.run(
            Agent[None](
                name="Manual page " + request.stage,
                instructions=request.instructions,
                model=request.model,
                output_type=schema,
                model_settings=ModelSettings(
                    max_tokens=request.max_output_tokens,
                    reasoning=Reasoning(effort=request.reasoning_effort),
                    retry={"max_retries": 0},
                ),
            ),
            input=cast(list[TResponseInputItem], request.input_items),
            max_turns=1,
            run_config=RunConfig(tracing_disabled=True),
        )
        if not isinstance(result.final_output, schema):
            raise TypeError("Unexpected grouped-page output")
        reply = AgentReply(
            output_json=result.final_output.model_dump_json(),
            input_tokens=sum(
                response.usage.input_tokens for response in result.raw_responses
            ),
            output_tokens=sum(
                response.usage.output_tokens for response in result.raw_responses
            ),
        )
        if request_path:
            await workflow.execute_activity(
                save_semantic_reply_activity,
                args=[request_path, reply],
                start_to_close_timeout=timedelta(minutes=1),
            )
        return reply
