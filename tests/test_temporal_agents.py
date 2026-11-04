"""Check real Agents SDK orchestration and source-preserving Activity boundaries."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from src.activities.agent_pipeline import (
    apply_agent_cleanup_activity,
    load_agent_request_activity,
    prepare_agent_cleanup_activity,
    save_agent_reply_activity,
)
from src.agent_models import AgentCallInput, AgentReply
from src.cleanup import OpenAICleanupResponse, OpenAICorrection
from src.quality.cleanup_context import ManualData
from src.quality.glossary import Entry, Glossary, Source
from src.temporal_client import connect_temporal
from src.workflows.agent_workflows import DurableAgentWorkflow


@pytest.mark.anyio
async def test_client_installs_the_official_temporal_agents_plugin() -> None:
    """Both worker and CLI use the same configured connection factory."""
    from temporalio.openai_agents import OpenAIAgentsPlugin

    with patch("src.temporal_client.Client.connect", new_callable=AsyncMock) as connect:
        await connect_temporal("localhost:7239")
    assert isinstance(connect.call_args.kwargs["plugins"][0], OpenAIAgentsPlugin)


@pytest.mark.anyio
async def test_durable_agent_uses_runner_and_typed_output() -> None:
    """The model call belongs to the agent Workflow, rather than a wrapping Activity."""
    result = SimpleNamespace(
        final_output=OpenAICleanupResponse(
            remove=[], corrections=[], product_name="", tags=[]
        ),
        raw_responses=[],
    )
    with patch(
        "src.workflows.agent_workflows.Runner.run",
        new_callable=AsyncMock,
        return_value=result,
    ) as run:
        reply = await DurableAgentWorkflow().run(
            AgentCallInput(
                kind="cleanup",
                instructions="Edit faithfully",
                prompt="Source",
                model="gpt-5-mini",
            )
        )
    assert json.loads(reply.output_json)["corrections"] == []
    agent = run.call_args.args[0]
    assert agent.output_type is OpenAICleanupResponse
    assert run.call_args.kwargs["run_config"].tracing_disabled


@pytest.mark.anyio
async def test_agent_cleanup_preserves_source_and_is_idempotent(tmp_path: Path) -> None:
    """Recorded agent replies cannot delete labels or change accepted spellings."""
    directory = tmp_path / "manual"
    directory.mkdir()
    Image.new("RGB", (100, 100), "white").save(directory / "page.webp")
    path = directory / "translations.json"
    original: ManualData = {
        "meta": {"manual_name": "Zein"},
        "pages": [
            {
                "image": "page.webp",
                "blocks": [
                    {
                        "text": "ゼインドライバー",
                        "translation": "Zein Driver",
                        "bbox": [0, 0, 1, 0.2],
                    },
                    {
                        "text": "レバー",
                        "translation": "Liver",
                        "bbox": [0, 0.2, 1, 0.2],
                    },
                    {"text": "ON", "translation": "ON", "bbox": [0, 0.4, 0.1, 0.1]},
                ],
            }
        ],
    }
    path.write_text(json.dumps(original))
    glossary = tmp_path / "glossary.json"
    glossary.write_text(
        Glossary(
            entries=[
                Entry(
                    id="zein",
                    japanese="ゼインドライバー",
                    english="Zein Driver",
                    status="accepted",
                    sources=[
                        Source(
                            url="https://www.tv-asahi.co.jp/zein/",
                            title="Zein",
                            evidence="ゼインドライバー / Zein Driver",
                            accessed=date.today(),
                        )
                    ],
                )
            ]
        ).model_dump_json()
    )
    with (
        patch("src.activities.agent_pipeline.ROOT", tmp_path),
        patch(
            "src.activities.agent_pipeline.activity.info",
            return_value=SimpleNamespace(workflow_run_id="test"),
        ),
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
    ):
        plan = await prepare_agent_cleanup_activity(
            str(path), "Zein", "", str(glossary)
        )
    assert plan.batch_count == 1
    request = await load_agent_request_activity(plan, 0)
    assert request.kind == "cleanup" and "Zein Driver" in request.prompt
    response = OpenAICleanupResponse(
        remove=["0-2"],
        corrections=[
            OpenAICorrection(index="0-0", translation="Zein Screwdriver"),
            OpenAICorrection(index="0-1", translation="Lever"),
        ],
        product_name="",
        tags=[],
    )
    await save_agent_reply_activity(
        plan, 0, AgentReply(output_json=response.model_dump_json())
    )
    result = await apply_agent_cleanup_activity(plan)
    assert result["success"] and result["corrections"] == 1
    saved = json.loads(path.read_text())
    assert len(saved["pages"][0]["blocks"]) == 3
    assert [block["text"] for block in saved["pages"][0]["blocks"]] == [
        block["text"] for block in original["pages"][0]["blocks"]
    ]
    assert saved["pages"][0]["blocks"][0]["translation"] == "Zein Driver"
    assert saved["pages"][0]["blocks"][1]["translation"] == "Lever"
    assert await apply_agent_cleanup_activity(plan) == result
    path.write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        await apply_agent_cleanup_activity(plan)


@pytest.fixture
def anyio_backend() -> str:
    """Exercise async Activities with the Worker's asyncio backend."""
    return "asyncio"


@pytest.mark.anyio
async def test_new_cleanup_schedule_uses_agent_child_workflow() -> None:
    """New runs route model work through the official durable Agent runner."""
    from src.workflows.cleanup_workflow import CleanupInput, CleanupWorkflow

    with (
        patch("src.workflows.cleanup_workflow.workflow.patched", return_value=True),
        patch("src.workflows.cleanup_workflow.workflow.logger"),
        patch(
            "src.workflows.cleanup_workflow.workflow.info",
            return_value=SimpleNamespace(workflow_id="cleanup"),
        ),
        patch(
            "src.workflows.cleanup_workflow.workflow.execute_activity",
            new_callable=AsyncMock,
            return_value={"success": True, "fixes": 0, "removals": 0},
        ) as execute,
        patch(
            "src.workflows.cleanup_workflow.workflow.execute_child_workflow",
            new_callable=AsyncMock,
            return_value={"success": True, "corrections": 1},
        ) as child,
    ):
        result = await CleanupWorkflow().run(
            CleanupInput("manual.json", "Product", "", glossary_path="glossary.json")
        )
    assert result.openai_corrections == 1
    assert execute.call_count == 2
    child.assert_awaited_once()


@pytest.mark.anyio
async def test_new_translation_researches_with_agent_and_disables_embedded_web_calls() -> (
    None
):
    """Research is durable and raw OpenAI research is not repeated in the translation Activity."""
    from src.activities.translation import TranslationResult
    from src.workflows.translation_workflow import TranslationInput, TranslationWorkflow

    with (
        patch("src.workflows.translation_workflow.workflow.patched", return_value=True),
        patch("src.workflows.translation_workflow.workflow.logger"),
        patch(
            "src.workflows.translation_workflow.workflow.info",
            return_value=SimpleNamespace(workflow_id="translation"),
        ),
        patch(
            "src.workflows.translation_workflow.workflow.execute_activity",
            new_callable=AsyncMock,
            return_value=TranslationResult([], True),
        ) as execute,
        patch(
            "src.workflows.translation_workflow.workflow.execute_child_workflow",
            new_callable=AsyncMock,
            return_value=False,
        ) as child,
    ):
        result = await TranslationWorkflow().run(
            TranslationInput(blocks=[], product="Test")
        )
    assert result.success
    child.assert_awaited_once()
    assert execute.call_args.kwargs["args"] == [[], "ja", "en", "Test", False]


@pytest.mark.anyio
async def test_glossary_verification_reuses_durable_reply_without_another_model_call(
    tmp_path: Path,
) -> None:
    """A durable research result still needs source verification before cache acceptance."""
    from src.activities.agent_pipeline import (
        merge_glossary_agent_activity,
        prepare_glossary_agent_activity,
    )
    from src.agent_models import GlossaryAgentInput
    from src.quality.pipeline import ManualBlock
    from src.quality.research import LookupResult
    from src.quality.web_research import ResearchFinding, ResearchFindings

    (tmp_path / "glossary.json").write_text(Glossary(entries=[]).model_dump_json())
    source = GlossaryAgentInput(
        product="仮面ライダーゼイン",
        blocks=[ManualBlock(text="キバ", page=0, x=0, y=0, width=1, height=1)],
    )
    finding = ResearchFinding(
        japanese="キバ",
        english="Kiva",
        franchise="kamen-rider",
        product=source.product,
        url="https://www.tv-asahi.co.jp/kiva/",
        title="Kiva",
        evidence="キバ / Kiva",
        reason="Bilingual name",
    )
    with (
        patch("src.activities.agent_pipeline.ROOT", tmp_path),
        patch(
            "src.activities.agent_pipeline.activity.info",
            return_value=SimpleNamespace(workflow_run_id="glossary-test"),
        ),
        patch.dict("os.environ", {"OPENAI_API_KEY": "fixture"}),
        patch(
            "src.quality.research.WikidataLookup.lookup", return_value=LookupResult()
        ),
        patch(
            "src.quality.web_research.GlossaryResearcher._page_text",
            return_value=finding.evidence,
        ),
        patch(
            "src.quality.web_research.OpenAI",
            side_effect=AssertionError("Raw OpenAI must not be called"),
        ),
    ):
        plan = await prepare_glossary_agent_activity(source)
        assert plan.request is not None
        reply = AgentReply(
            output_json=ResearchFindings(findings=[finding]).model_dump_json(),
            consulted_urls=[finding.url],
        )
        assert await merge_glossary_agent_activity(plan, reply) == 1
    glossary = Glossary.model_validate_json((tmp_path / "glossary.json").read_bytes())
    assert (
        glossary.entries[0].english == "Kiva"
        and glossary.entries[0].status == "accepted"
    )


@pytest.mark.anyio
async def test_prepared_images_fit_temporal_payload_limit(tmp_path: Path) -> None:
    """High-entropy scans must be bounded before they enter Workflow history."""
    image = tmp_path / "page.png"
    Image.effect_noise((2400, 2400), 100).convert("RGB").save(image)
    manual = tmp_path / "translations.json"
    manual.write_text(
        json.dumps(
            {
                "meta": {},
                "pages": [
                    {
                        "image": "page.png",
                        "blocks": [
                            {
                                "text": "レバー",
                                "translation": "Lever",
                                "bbox": [0, 0, 1, 1],
                            }
                        ],
                    }
                ],
            }
        )
    )
    with (
        patch("src.activities.agent_pipeline.ROOT", tmp_path),
        patch(
            "src.activities.agent_pipeline.activity.info",
            return_value=SimpleNamespace(workflow_run_id="large-image-test"),
        ),
        patch.dict("os.environ", {"OPENAI_API_KEY": "fixture"}),
    ):
        plan = await prepare_agent_cleanup_activity(str(manual), "Test", "", "")
        request = await load_agent_request_activity(plan, 0)
    assert request.input_items
    assert len(request.model_dump_json().encode()) <= 1_400_000


@pytest.mark.anyio
async def test_cleanup_parent_passes_references_instead_of_page_images() -> None:
    """Large manuals do not accumulate every image request in the parent history."""
    from src.agent_models import AgentCleanupPlan
    from src.workflows.agent_workflows import AgentCleanupWorkflow

    plan = AgentCleanupPlan(directory="prepared", batch_count=1)
    reply = AgentReply(
        output_json=OpenAICleanupResponse(
            remove=[], corrections=[], product_name="", tags=[]
        ).model_dump_json()
    )
    with (
        patch(
            "src.workflows.agent_workflows.workflow.info",
            return_value=SimpleNamespace(workflow_id="cleanup"),
        ),
        patch(
            "src.workflows.agent_workflows.workflow.execute_activity",
            new_callable=AsyncMock,
            side_effect=[plan, None, {"success": True, "corrections": 0}],
        ) as execute,
        patch(
            "src.workflows.agent_workflows.workflow.execute_child_workflow",
            new_callable=AsyncMock,
            return_value=reply,
        ) as child,
    ):
        await AgentCleanupWorkflow().run("manual.json", "Product", "", "glossary.json")
    assert child.await_count == 1
    assert execute.await_count == 3
    request = child.call_args.args[1]
    assert request.cleanup_plan == plan and not request.input_items
    assert len(request.model_dump_json()) < 1000
    assert all(
        call.args[0] is not load_agent_request_activity
        for call in execute.call_args_list
    )


@pytest.mark.anyio
async def test_glossary_agent_accepts_search_calls_without_action() -> None:
    """An actionless search item cannot discard a completed structured research reply."""
    from src.quality.web_research import ResearchFindings

    result = SimpleNamespace(
        final_output=ResearchFindings(findings=[]),
        raw_responses=[
            SimpleNamespace(
                output=[
                    SimpleNamespace(type="web_search_call", action=None),
                    SimpleNamespace(
                        type="web_search_call",
                        action=SimpleNamespace(
                            model_dump=lambda: {
                                "type": "search",
                                "sources": [
                                    {"url": "https://toy.bandai.co.jp/example"}
                                ],
                            }
                        ),
                    ),
                ],
                usage=SimpleNamespace(input_tokens=12, output_tokens=7),
            )
        ],
    )
    with patch(
        "src.workflows.agent_workflows.Runner.run",
        new_callable=AsyncMock,
        return_value=result,
    ):
        reply = await DurableAgentWorkflow().run(
            AgentCallInput(
                kind="glossary",
                instructions="Research",
                prompt="Names",
                model="gpt-5-mini",
            )
        )
    assert reply.consulted_urls == ["https://toy.bandai.co.jp/example"]
    assert reply.input_tokens == 12
    assert reply.output_tokens == 7
    assert json.loads(reply.output_json) == {"findings": []}


@pytest.mark.anyio
async def test_targeted_cleanup_only_exposes_selected_editable_blocks(
    tmp_path: Path,
) -> None:
    """Keep full source and page context while constraining the model's edit scope."""
    path = tmp_path / "translations.json"
    path.write_text(
        json.dumps(
            {
                "meta": {},
                "pages": [
                    {
                        "blocks": [
                            {
                                "text": "レバー",
                                "translation": "Liver",
                                "bbox": [0, 0, 1, 0.1],
                            },
                            {
                                "text": "ON",
                                "translation": "ON",
                                "bbox": [0, 0.2, 1, 0.1],
                            },
                        ]
                    }
                ],
            }
        )
    )
    with (
        patch("src.activities.agent_pipeline.ROOT", tmp_path),
        patch(
            "src.activities.agent_pipeline.activity.info",
            return_value=SimpleNamespace(workflow_run_id="targeted"),
        ),
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
    ):
        plan = await prepare_agent_cleanup_activity(str(path), "Test", "", "", ["0-0"])
    request = await load_agent_request_activity(plan, 0)
    batch = json.loads(request.prompt)["batch"]
    assert [block["index"] for block in batch["blocks"]] == ["0-0"]
    assert "0-1" in [block["index"] for block in batch["neighbors"]]


def test_preparation_can_use_a_standalone_activity_identity(tmp_path: Path) -> None:
    """Recovery jobs should reuse frozen plans without requiring a wrapper Workflow."""
    from src.activities.agent_pipeline import _directory

    with (
        patch("src.activities.agent_pipeline.ROOT", tmp_path),
        patch(
            "src.activities.agent_pipeline.activity.info",
            return_value=SimpleNamespace(
                workflow_run_id=None, activity_id="repair-preparation"
            ),
        ),
    ):
        assert (
            _directory("cleanup").parent
            == tmp_path / "benchmark-runs/agent-pipeline/cleanup"
        )
