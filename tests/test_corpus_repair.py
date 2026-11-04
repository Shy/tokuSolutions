"""Repair only evidenced problems and preserve edited source data."""

from copy import deepcopy
from pathlib import Path

import pytest

from src.quality.cleanup_context import ManualData
from src.quality.corpus_repair import canonical_box, find_issues, guard_repairs
from src.quality.glossary import Glossary


def test_normalize_reversed_endpoints_without_moving_the_rectangle() -> None:
    """Negative dimensions encode the same two endpoints, not permission to expand."""
    assert canonical_box([0.5, 0.4, -0.1, -0.2]) == [0.4, 0.2, 0.1, 0.2]
    with pytest.raises(ValueError):
        canonical_box([0.5, 0.4, 0, 0.2])
    with pytest.raises(ValueError):
        canonical_box([0.9, 0.4, 0.2, 0.2])


def test_guard_rejects_new_numeric_loss_but_keeps_existing_omission_improvements() -> (
    None
):
    """Do not veto an unrelated word repair because an old numeric omission remains."""
    before: ManualData = {
        "meta": {"manual_name": "Owner's title"},
        "pages": [
            {
                "blocks": [
                    {
                        "text": "3秒押す",
                        "translation": "Press for 3 seconds",
                        "bbox": [0, 0, 1, 0.1],
                    },
                    {
                        "text": "レバーを2回",
                        "translation": "Pull the liver",
                        "bbox": [0, 0.2, 1, 0.1],
                    },
                ]
            }
        ],
    }
    after = deepcopy(before)
    after["meta"]["manual_name"] = "AI title"
    after["pages"][0]["blocks"][0]["translation"] = "Press for 5 seconds"
    after["pages"][0]["blocks"][1]["translation"] = "Pull the lever"
    rejected = guard_repairs(before, after, Glossary(entries=[]), "Test")
    assert len(rejected) == 1
    assert after["meta"]["manual_name"] == "Owner's title"
    assert after["pages"][0]["blocks"][0]["translation"] == "Press for 3 seconds"
    assert after["pages"][0]["blocks"][1]["translation"] == "Pull the lever"
    assert [
        issue.kind for issue in find_issues(before, Glossary(entries=[]), "Test")
    ] == ["numbers"]


def test_custom_titles_do_not_disable_product_scoped_constraints() -> None:
    """Resolve product IDs before sending the historical manual to generic cleanup."""
    from datetime import date

    from src.quality.corpus_repair import repair_constraints
    from src.quality.glossary import Entry, Source

    glossary = Glossary(
        entries=[
            Entry(
                id="zein",
                japanese="ゼイン",
                english="Zein",
                product="ZeinFolder",
                status="accepted",
                sources=[
                    Source(
                        url="https://example.org/zein",
                        title="Zein",
                        evidence="ゼイン Zein",
                        accessed=date(2026, 11, 4),
                    )
                ],
            )
        ]
    )
    data: ManualData = {
        "meta": {"manual_name": "Owner title"},
        "pages": [{"blocks": []}],
    }
    resolved = repair_constraints(data, glossary, "ZeinFolder")
    assert len(resolved.entries) == 1
    assert resolved.entries[0].english == "Zein"
    assert resolved.entries[0].product is None


def test_numeric_audit_understands_number_words_and_battery_sizes() -> None:
    """AA size codes are not quantities; equivalent English number words are valid."""
    data: ManualData = {
        "meta": {},
        "pages": [
            {
                "blocks": [
                    {
                        "text": "単3乾電池3本を4秒",
                        "translation": "Three AA batteries for four seconds",
                    },
                    {"text": "1,2の順", "translation": "In the order 1 and 2"},
                    {"text": "21回", "translation": "Twenty-one times"},
                    {"text": "1,000回", "translation": "1000 times"},
                ]
            }
        ],
    }
    assert not find_issues(data, Glossary(entries=[]), "Test")


def test_guard_catches_quantities_changed_using_only_english_words() -> None:
    """A digit-only comparison must not overlook four becoming five."""
    before: ManualData = {
        "meta": {},
        "pages": [
            {
                "blocks": [
                    {
                        "text": "4秒",
                        "translation": "Four seconds",
                        "bbox": [0, 0, 1, 0.1],
                    },
                ]
            }
        ],
    }
    after = deepcopy(before)
    after["pages"][0]["blocks"][0]["translation"] = "Five seconds"
    assert guard_repairs(before, after, Glossary(entries=[]), "Test")
    assert after["pages"][0]["blocks"][0]["translation"] == "Four seconds"


@pytest.mark.anyio
async def test_recovery_reuses_saved_batches_without_recalling_the_model(
    tmp_path: Path,
) -> None:
    """A failed run can finish application using its immutable response cache."""
    import asyncio
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock, patch

    from temporalio.client import Client

    from src.agent_models import AgentCleanupPlan
    from src.quality.corpus_repair import repair_batches

    stage = tmp_path / "staging/translations.json"
    stage.parent.mkdir()
    stage.write_text("interrupted draft")
    directory = tmp_path / "benchmark-runs/agent-pipeline/cleanup/saved"
    directory.mkdir(parents=True)
    plan = AgentCleanupPlan(directory=str(directory), batch_count=1)
    (directory / "plan.json").write_text(plan.model_dump_json())
    (directory / "source-path.txt").write_text(str(stage.resolve()))
    (directory / "source.json").write_text("frozen draft")
    (directory / "reply-0.json").write_text("{}")
    execute = AsyncMock(return_value={"success": True, "corrections": 1})
    client = cast(Client, SimpleNamespace(execute_activity=execute))
    with patch("src.quality.corpus_repair.ROOT", tmp_path):
        result = await repair_batches(
            client,
            "isolated",
            stage,
            "Test",
            Path("glossary.json"),
            None,
            asyncio.Semaphore(3),
        )
    assert result["success"]
    assert execute.await_count == 1
    assert stage.read_text() == "frozen draft"


@pytest.fixture
def anyio_backend() -> str:
    """Use the Temporal SDK's asyncio runtime."""
    return "asyncio"
