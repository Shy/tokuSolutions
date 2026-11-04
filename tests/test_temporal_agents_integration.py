"""Opt-in local integration: real plugin, durable retry, no paid model requests."""

import base64
import json
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest
from agents import Handoff, ModelSettings, Tool
from agents.agent_output import AgentOutputSchemaBase
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import Model, ModelProvider, ModelTracing
from agents.usage import Usage
from openai.types.responses import (
    ResponseOutputMessage,
    ResponseOutputText,
    ResponsePromptParam,
)
from pydantic import JsonValue, TypeAdapter
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from src.activities.ocr import PageOCRResult, TextBlock
from src.activities.translation import OCRBlock, TranslatedBlock, TranslationResult
from src.temporal_client import agents_plugin
from src.worker import ACTIVITIES, WORKFLOWS
from src.workflows.agent_workflows import (
    AgentCleanupWorkflow,
    DurableAgentWorkflow,
    GlossaryAgentWorkflow,
)
from src.workflows.cleanup_workflow import CleanupWorkflow
from src.workflows.ocr_workflow import OCRWorkflow
from src.workflows.pdf_translation_workflow import (
    PDFTranslationInput,
    PDFTranslationWorkflow,
)
from src.workflows.site_generation_workflow import SiteGenerationWorkflow
from src.workflows.translation_workflow import TranslationWorkflow

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_TEMPORAL_INTEGRATION") != "1",
    reason="Set RUN_TEMPORAL_INTEGRATION=1 to start an isolated local dev server",
)


def instruction_payload(value: JsonValue) -> dict[str, JsonValue] | None:
    """Find the user JSON inside typed multimedia SDK inputs."""
    if isinstance(value, dict):
        if "instruction_groups" in value:
            return value
        for child in value.values():
            found = instruction_payload(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = instruction_payload(child)
            if found:
                return found
    elif isinstance(value, str):
        try:
            return instruction_payload(TypeAdapter(JsonValue).validate_json(value))
        except ValueError:
            pass
    return None


class FixtureProvider(ModelProvider):
    """Fail the first model Activity and succeed on its Temporal retry."""

    def __init__(self) -> None:
        self.calls = 0

    def get_model(self, model_name: str | None) -> Model:
        """Return a deterministic local model for the official plugin to invoke."""
        return FixtureModel(self)


class FixtureModel(Model):
    """A model implementation reached exclusively through plugin model Activities."""

    def __init__(self, provider: FixtureProvider) -> None:
        self.provider = provider

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff[object]],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        """Return a valid typed cleanup reply after one retryable worker failure."""
        self.provider.calls += 1
        if self.provider.calls == 1:
            raise RuntimeError("Simulated transient model failure")
        if output_schema is not None and output_schema.name() == "ResearchFindings":
            payload = '{"findings": []}'
        elif output_schema is not None and output_schema.name() == "InstructionReview":
            request = instruction_payload(TypeAdapter(JsonValue).validate_python(input))
            assert request is not None
            groups = request["instruction_groups"]
            assert isinstance(groups, list)
            verdicts = []
            for group in groups:
                assert isinstance(group, dict)
                repair = request["mode"] == "check" and "レバー" in str(
                    group["japanese"]
                )
                verdicts.append(
                    {
                        "group_id": group["id"],
                        "decision": "repair" if repair else "accept",
                        "source_quote": group["japanese"],
                        "explanation": "Source-grounded fixture",
                        "corrections": [{"index": "0-0", "translation": "Lever"}]
                        if repair
                        else [],
                    }
                )
            payload = json.dumps({"groups": verdicts})
        else:
            payload = json.dumps(
                {
                    "remove": ["0-1"],
                    "corrections": [{"index": "0-0", "translation": "Lever"}],
                    "product_name": "",
                    "tags": [],
                }
            )
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="msg_fixture",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            type="output_text", text=payload, annotations=[]
                        )
                    ],
                )
            ],
            usage=Usage(
                requests=1, input_tokens=100, output_tokens=20, total_tokens=120
            ),
            response_id="resp_fixture",
        )

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff[object]],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        """Streaming is intentionally unused by this durable batch integration."""
        raise NotImplementedError
        yield  # pragma: no cover


@activity.defn(name="search_product_url_activity")
async def fixture_product(name: str) -> dict[str, str | bool]:
    """Return explicit product metadata without an external store request."""
    return {"success": True, "url": "https://example.com/product", "name": name}


@activity.defn(name="ocr_page_activity")
async def fixture_ocr(pdf_path: str, page_num: int) -> PageOCRResult:
    """Provide source-preserving regions to exercise the complete pipeline."""
    if Path(pdf_path).stem == "failed-ocr" and page_num == 1:
        return PageOCRResult(page_num, [], False, "Simulated incomplete OCR")
    return PageOCRResult(
        page_num,
        [
            TextBlock("レバー", page_num, 0, 0, 1, 0.2, 0.99),
            TextBlock("ON", page_num, 0, 0.3, 1, 0.2, 0.99),
        ],
        True,
    )


@activity.defn(name="translate_blocks_activity")
async def fixture_translation(
    blocks: list[OCRBlock],
    source_lang: str,
    target_lang: str,
    product: str,
    use_web_research: bool,
) -> TranslationResult:
    """Ensure the new path delegates research to the durable agent before translation."""
    assert not use_web_research
    return TranslationResult(
        [
            TranslatedBlock(
                block["text"],
                "Liver" if block["text"] == "レバー" else block["text"],
                block["page"],
                block["x"],
                block["y"],
                block["width"],
                block["height"],
            )
            for block in blocks
        ],
        True,
    )


@pytest.mark.anyio
async def test_real_plugin_retries_and_replays_without_recalling_model(
    tmp_path: Path,
) -> None:
    """Verify durable retry and staged publication with a real read-only browser audit."""
    binary = shutil.which("temporal")
    assert binary, "Temporal CLI is required for the opt-in integration test"
    manual = tmp_path / "translations.json"
    manual.write_text(
        json.dumps(
            {
                "meta": {"manual_name": "Test"},
                "pages": [
                    {
                        "blocks": [
                            {
                                "text": "レバー",
                                "translation": "Liver",
                                "bbox": [0, 0, 1, 0.2],
                            },
                            {
                                "text": "ON",
                                "translation": "ON",
                                "bbox": [0, 0.3, 1, 0.2],
                            },
                        ]
                    }
                ],
            }
        )
    )
    shutil.copytree(Path(__file__).resolve().parents[1] / "web", tmp_path / "web")
    provider = FixtureProvider()
    async with await WorkflowEnvironment.start_local(
        port=7239, dev_server_existing_path=binary
    ) as environment:
        address = environment.client.service_client.config.target_host
        client = await Client.connect(address, plugins=[agents_plugin(provider)])
        with (
            patch("src.activities.agent_pipeline.ROOT", tmp_path),
            patch("src.activities.publication.ROOT", tmp_path),
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "fixture-only", "OPENAI_MODEL": "fixture"},
            ),
        ):
            async with Worker(
                client,
                task_queue="agents-integration",
                workflows=WORKFLOWS,
                activities=[
                    {
                        "search_product_url_activity": fixture_product,
                        "ocr_page_activity": fixture_ocr,
                        "translate_blocks_activity": fixture_translation,
                    }.get(function.__name__, function)
                    for function in ACTIVITIES
                ],
            ):
                handle = await client.start_workflow(
                    AgentCleanupWorkflow.run,
                    args=[str(manual), "Test", "", ""],
                    id="agents-cleanup-integration",
                    task_queue="agents-integration",
                )
                result = await handle.result()
                assert result["success"], result
                assert provider.calls == 2
                saved = json.loads(manual.read_text())
                assert [
                    block["translation"] for block in saved["pages"][0]["blocks"]
                ] == ["Lever", "ON"]
                histories = []
                for workflow_id in [
                    "agents-cleanup-integration",
                    "agents-cleanup-integration-batch-0",
                ]:
                    history = await client.get_workflow_handle(
                        workflow_id
                    ).fetch_history()
                    histories.append(history)
                    (tmp_path / (workflow_id + ".json")).write_text(history.to_json())
                parent_history = histories[0].to_json()
                patch_ids = {
                    json.loads(base64.b64decode(payload["data"]))["id"]
                    for event in json.loads(parent_history)["events"]
                    for detail in event.get("markerRecordedEventAttributes", {})
                    .get("details", {})
                    .values()
                    for payload in detail.get("payloads", [])
                }
                assert not patch_ids
                assert "prepare_instruction_check_activity" not in parent_history
                assert "resolve_instruction_check_activity" not in parent_history
                model_history = histories[-1].to_json()
                assert (
                    "invoke_model" in model_history or "get_response" in model_history
                )
                replayer = Replayer(
                    workflows=[
                        AgentCleanupWorkflow,
                        DurableAgentWorkflow,
                        GlossaryAgentWorkflow,
                        PDFTranslationWorkflow,
                        OCRWorkflow,
                        TranslationWorkflow,
                        SiteGenerationWorkflow,
                        CleanupWorkflow,
                    ],
                    plugins=[agents_plugin(provider)],
                )
                for history in histories:
                    await replayer.replay_workflow(history)
                assert provider.calls == 2, "Replay reissued a paid model call"
                import pymupdf

                pdf_path = tmp_path / "source.pdf"
                with pymupdf.open() as document:
                    document.new_page(width=100, height=100)
                    document.save(pdf_path)
                (tmp_path / "glossary.json").write_text('{"version":1,"entries":[]}')
                with patch("src.activities.site_generation._generate_main_index"):
                    complete = await client.execute_workflow(
                        PDFTranslationWorkflow.run,
                        PDFTranslationInput(
                            str(pdf_path),
                            "Kamen Rider Test",
                            str(tmp_path / "manuals/Test"),
                        ),
                        id="full-agent-pipeline",
                        task_queue="agents-integration",
                    )
                assert complete.success, complete.error
                assert complete.quality_status in {"complete", "complete_with_warnings"}
                assert Path(complete.audit_path).is_file()
                assert (
                    json.loads((tmp_path / "web/meta.json").read_text())["stats"][
                        "total_manuals"
                    ]
                    == 1
                )
                output = json.loads(Path(complete.json_path).read_text())
                assert [
                    block["translation"] for block in output["pages"][0]["blocks"]
                ] == ["Lever", "ON"]
                assert provider.calls == 4, (
                    "Expected only glossary and cleanup model calls"
                )
                for identifier, workflow_type in [
                    ("full-agent-pipeline", PDFTranslationWorkflow),
                    ("full-agent-pipeline-translation", TranslationWorkflow),
                    ("full-agent-pipeline-cleanup", CleanupWorkflow),
                    (
                        "full-agent-pipeline-translation-glossary-agent",
                        GlossaryAgentWorkflow,
                    ),
                ]:
                    history = await client.get_workflow_handle(
                        identifier
                    ).fetch_history()
                    await Replayer(
                        workflows=[workflow_type], plugins=[agents_plugin(provider)]
                    ).replay_workflow(history)
                assert provider.calls == 4

                # A missing local browser is advisory, never a human approval gate.
                with patch(
                    "src.activities.render_audit.async_playwright",
                    side_effect=RuntimeError("browser unavailable"),
                ):
                    unavailable = await client.execute_workflow(
                        PDFTranslationWorkflow.run,
                        PDFTranslationInput(
                            str(pdf_path), "Test", str(tmp_path / "manuals/NoBrowser")
                        ),
                        id="browser-unavailable-pipeline",
                        task_queue="agents-integration",
                    )
                assert unavailable.success
                assert unavailable.quality_status == "complete_with_warnings"
                assert "Browser audit unavailable" in " ".join(unavailable.warnings)
                assert (
                    json.loads(Path(unavailable.audit_path).read_text())[
                        "pages_checked"
                    ]
                    == 0
                )
                assert provider.calls == 5

                incomplete_pdf = tmp_path / "failed-ocr.pdf"
                with pymupdf.open() as document:
                    document.new_page(width=100, height=100)
                    document.new_page(width=100, height=100)
                    document.save(incomplete_pdf)
                failed = await client.execute_workflow(
                    PDFTranslationWorkflow.run,
                    PDFTranslationInput(
                        str(incomplete_pdf),
                        "Test",
                        str(tmp_path / "manuals/Incomplete"),
                    ),
                    id="incomplete-ocr-pipeline",
                    task_queue="agents-integration",
                )
                assert not failed.success and failed.quality_status == "failed"
                assert not (tmp_path / "manuals/Incomplete").exists()
                assert provider.calls == 5, (
                    "Incomplete OCR must stop before any translation/cleanup model call"
                )


@pytest.fixture
def anyio_backend() -> str:
    """Use the Temporal Worker's asyncio runtime."""
    return "asyncio"
