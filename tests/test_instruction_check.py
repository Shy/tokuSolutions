"""Assess complete instructions and apply only confirmed source-preserving repairs."""

from pathlib import Path

from src.cleanup import OpenAICleanupResponse, OpenAICorrection
from src.quality.cleanup_context import build_batches
from src.quality.glossary import Glossary
from src.quality.instruction_check import (
    InstructionReview,
    build_instruction_units,
    resolve_review,
)


def test_instruction_units_follow_columns_and_keep_fragment_ids() -> None:
    """An intervening diagram label is not part of a continuous warning."""
    batch = build_batches(
        {
            "meta": {},
            "pages": [
                {
                    "blocks": [
                        {
                            "text": "装填/引き抜きを",
                            "translation": "insertion/removal",
                            "bbox": [0.1, 0.1, 0.2, 0.02],
                        },
                        {
                            "text": "ドライバー",
                            "translation": "Driver",
                            "bbox": [0.6, 0.12, 0.2, 0.02],
                        },
                        {
                            "text": "繰り返すと、",
                            "translation": "when repeated",
                            "bbox": [0.1, 0.125, 0.2, 0.02],
                        },
                        {
                            "text": "擦れ跡がつきます。",
                            "translation": "scuff marks appear",
                            "bbox": [0.1, 0.15, 0.2, 0.02],
                        },
                    ]
                }
            ],
        }
    )[0]
    units = build_instruction_units(batch)
    assert units[0].indices == ["0-0", "0-2", "0-3"]
    assert units[0].japanese == "装填/引き抜きを繰り返すと、擦れ跡がつきます。"
    assert units[1].indices == ["0-1"]


def test_review_drops_uncertain_edits_and_requires_confirmation_for_repairs() -> None:
    """Malformed coverage or unconfirmed repairs never alter the retained draft."""
    batch = build_batches(
        {
            "meta": {},
            "pages": [
                {
                    "blocks": [
                        {
                            "text": "レバーを引く。",
                            "translation": "Pull the liver.",
                            "bbox": [0.1, 0.1, 0.2, 0.02],
                        },
                        {
                            "text": "ON",
                            "translation": "ON",
                            "bbox": [0.6, 0.1, 0.2, 0.02],
                        },
                    ]
                }
            ],
        }
    )[0]
    units = build_instruction_units(batch)
    proposed = OpenAICleanupResponse(
        remove=[], corrections=[], product_name="", tags=[]
    )
    review = InstructionReview.model_validate(
        {
            "groups": [
                {
                    "group_id": units[0].id,
                    "decision": "repair",
                    "source_quote": "レバーを引く。",
                    "explanation": "Lever, not organ.",
                    "corrections": [{"index": "0-0", "translation": "Pull the lever."}],
                },
                {
                    "group_id": units[1].id,
                    "decision": "accept",
                    "source_quote": "ON",
                    "explanation": "Literal switch label.",
                    "corrections": [],
                },
            ]
        }
    )
    revised, repair_ids = resolve_review(batch, proposed, review, Glossary(entries=[]))
    assert revised.corrections == [
        OpenAICorrection(index="0-0", translation="Pull the lever.")
    ]
    assert repair_ids == [units[0].id]
    rejected = review.model_copy(update={"groups": review.groups[:1]})
    import pytest

    with pytest.raises(ValueError, match="coverage"):
        resolve_review(batch, proposed, rejected, Glossary(entries=[]))
    review.groups[0].decision = "uncertain"
    review.groups[0].corrections = []
    revised, repair_ids = resolve_review(batch, proposed, review, Glossary(entries=[]))
    assert not revised.corrections and not repair_ids


def test_conflicting_ocr_blocks_repairs_even_if_agent_accepts_them() -> None:
    """Independent OCR disagreement cannot be silently resolved by a model verdict."""
    batch = build_batches(
        {
            "meta": {},
            "pages": [
                {
                    "blocks": [
                        {
                            "text": "下側がに",
                            "translation": "Lower side",
                            "bbox": [0, 0, 0.2, 0.1],
                        }
                    ]
                }
            ],
        }
    )[0]
    unit = build_instruction_units(batch)[0]
    proposed = OpenAICleanupResponse(
        remove=[],
        corrections=[OpenAICorrection(index="0-0", translation="Lower side +")],
        product_name="",
        tags=[],
    )
    review = InstructionReview.model_validate(
        {
            "groups": [
                {
                    "group_id": unit.id,
                    "decision": "accept",
                    "source_quote": "下側がに",
                    "explanation": "Claimed correct",
                    "corrections": [],
                }
            ]
        }
    )
    result, repairs = resolve_review(
        batch, proposed, review, Glossary(entries=[]), blocked_indices={"0-0"}
    )
    assert not result.corrections and not repairs


def test_comparison_quotes_can_span_one_instruction_but_not_another_column() -> None:
    """Whole-sentence evidence need not be misclassified as unsupported OCR."""
    from src.quality.comparison import validate_assessment
    from src.quality.comparison_models import Assessment, Finding
    from src.quality.models import Block

    blocks = [
        Block(id="a", text="装填/引き抜きを", bbox=(0.1, 0.1, 0.2, 0.02)),
        Block(id="b", text="繰り返すと、", bbox=(0.1, 0.125, 0.2, 0.02)),
        Block(id="c", text="電池", bbox=(0.6, 0.125, 0.2, 0.02)),
    ]
    finding = Finding(
        block_id="a",
        related_ids=["a", "b"],
        category="action",
        severity="major",
        a_wrong=True,
        b_wrong=False,
        source_quote="装填/引き抜きを繰り返すと、",
        expected_meaning="When insertion/removal is repeated",
        explanation="Instruction meaning",
        uncertainty="",
    )
    checked = validate_assessment(
        Assessment(covered_ids=["a", "b", "c"], findings=[finding]), blocks
    )
    assert not checked.findings[0].uncertainty
    finding.related_ids = ["a", "c"]
    unsupported = validate_assessment(
        Assessment(covered_ids=["a", "b", "c"], findings=[finding]), blocks
    )
    assert "instruction boundaries" in unsupported.findings[0].uncertainty
    assert not unsupported.findings[0].related_ids


def test_confirmation_cannot_start_another_repair_round() -> None:
    """An unconfirmed or newly revised repair is discarded rather than looped."""
    batch = build_batches(
        {
            "meta": {},
            "pages": [
                {
                    "blocks": [
                        {
                            "text": "レバー",
                            "translation": "Liver",
                            "bbox": [0, 0, 0.2, 0.1],
                        }
                    ]
                }
            ],
        }
    )[0]
    unit = build_instruction_units(batch)[0]
    proposed = OpenAICleanupResponse(
        remove=[],
        corrections=[OpenAICorrection(index="0-0", translation="Lever")],
        product_name="",
        tags=[],
    )
    review = InstructionReview.model_validate(
        {
            "groups": [
                {
                    "group_id": unit.id,
                    "decision": "repair",
                    "source_quote": "レバー",
                    "explanation": "Still wants a change",
                    "corrections": [{"index": "0-0", "translation": "Handle"}],
                }
            ]
        }
    )
    result, repairs = resolve_review(
        batch, proposed, review, Glossary(entries=[]), confirmation=True
    )
    assert not result.corrections and not repairs


def test_repair_confirmation_serializes_dated_glossary_sources(tmp_path: Path) -> None:
    """A real evidence date must survive the repair-to-confirmation Activity boundary."""
    import json
    from datetime import date
    from types import SimpleNamespace
    from unittest.mock import patch

    from src.activities.agent_pipeline import (
        _instruction_request,
        _prepare_cleanup,
        _resolve_instruction,
    )
    from src.agent_models import AgentCallInput, AgentReply
    from src.quality.glossary import Entry, Source

    path = tmp_path / "translations.json"
    path.write_text(
        json.dumps(
            {
                "meta": {"manual_name": "Test"},
                "pages": [
                    {
                        "blocks": [
                            {
                                "text": "レバー",
                                "translation": "Liver",
                                "bbox": [0, 0, 0.2, 0.1],
                            }
                        ]
                    }
                ],
            }
        )
    )
    glossary = tmp_path / "glossary.json"
    glossary.write_text(
        Glossary(
            entries=[
                Entry(
                    id="lever",
                    japanese="レバー",
                    english="Lever",
                    status="accepted",
                    sources=[
                        Source(
                            url="https://toy.bandai.co.jp/test",
                            title="Test",
                            evidence="レバー Lever",
                            accessed=date(2026, 11, 3),
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
            return_value=SimpleNamespace(workflow_run_id="dated-repair"),
        ),
        patch.dict("os.environ", {"OPENAI_API_KEY": "fixture"}),
    ):
        plan = _prepare_cleanup(str(path), "", "", str(glossary))
    proposed = AgentReply(
        output_json=OpenAICleanupResponse(
            remove=[], corrections=[], product_name="", tags=[]
        ).model_dump_json()
    )
    _instruction_request(plan, 0, proposed)
    verdict = AgentReply(
        output_json=InstructionReview.model_validate(
            {
                "groups": [
                    {
                        "group_id": "0-0",
                        "decision": "repair",
                        "source_quote": "レバー",
                        "explanation": "Mechanical lever",
                        "corrections": [{"index": "0-0", "translation": "Lever"}],
                    }
                ]
            }
        ).model_dump_json()
    )
    result = _resolve_instruction(plan, 0, verdict, False)
    assert result.needs_confirmation
    request = AgentCallInput.model_validate_json(
        (Path(plan.directory) / "confirm-request-0.json").read_bytes()
    )
    assert (
        json.loads(request.prompt)["glossary"]["entries"][0]["sources"][0]["accessed"]
        == "2026-11-03"
    )
