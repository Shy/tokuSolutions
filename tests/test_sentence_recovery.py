"""Recover source-supported clauses without crossing diagrams or columns."""

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src.activities.semantic_layout import _check_translation, _plan_page
from src.agent_models import AgentReply
from src.quality.cleanup_context import ContextPage
from src.quality.page_grouping import (
    PagePlan,
    Panel,
    PanelText,
    PanelTranslations,
    validate_translations,
)
from src.quality.semantic_layout import assemble_page, validate_grouped_page


def prepare(tmp_path: Path, boundary: str = "") -> tuple[ContextPage, list[str]]:
    """Prepare adjacent battery instructions with a separately planned ending."""
    page = ContextPage.model_validate(
        {
            "image": "scan.jpg",
            "blocks": [
                {
                    "text": "電池ぶたの穴を先の細い",
                    "translation": "Use a thin pointed",
                    "bbox": [0.1, 0.2, 0.35, 0.01],
                },
                {
                    "text": "棒などで押しながら、",
                    "translation": "While pressing with a stick,",
                    "bbox": [0.12, 0.22, 0.2, 0.01],
                },
                {
                    "text": "電池ぶたを取り外します。",
                    "translation": "Remove the battery cover.",
                    "bbox": [0.1, 0.24, 0.3, 0.01],
                },
                {"text": "ON", "translation": "ON", "bbox": [0.7, 0.2, 0.05, 0.01]},
            ],
        }
    )
    if boundary == "column":
        page.blocks[1].bbox = (0.6, 0.22, 0.2, 0.01)
    elif boundary == "gap":
        page.blocks[1].bbox = (0.12, 0.4, 0.2, 0.01)
    elif boundary == "heading":
        page.blocks[1].text = "注意"
    image = Image.new("RGB", (1000, 1000), "white")
    if boundary == "art":
        ImageDraw.Draw(image).rectangle((350, 212, 440, 237), fill="black")
    image.save(tmp_path / "scan.jpg")
    (tmp_path / "source-page.json").write_text(page.model_dump_json())
    (tmp_path / "lines.json").write_text("[]")
    (tmp_path / "glossary.json").write_text("{}")
    plan = PagePlan(
        panels=[
            Panel(id="start", kind="prose", source_ids=[0]),
            Panel(
                id="end",
                kind="heading" if boundary == "heading" else "prose",
                source_ids=[1, 2],
            ),
        ],
        furigana=[],
        notes=[],
    )
    return page, _plan_page(
        str(tmp_path / "plan.request.json"),
        AgentReply(output_json=plan.model_dump_json()),
    )


def test_cross_region_clause_reconstruction_keeps_source_coverage(
    tmp_path: Path,
) -> None:
    """A split clause regains its printed action before crop translation."""
    page, paths = prepare(tmp_path)
    plan = PagePlan.model_validate_json((tmp_path / "plan.json").read_bytes())
    assert [panel.source_ids for panel in plan.panels] == [[0, 1, 2]]
    assert len(paths) == 1
    texts = PanelTranslations(
        panels=[
            PanelText(
                panel_id="start",
                japanese_items=[
                    "電池ぶたの穴を先の細い棒などで押しながら、電池ぶたを取り外します。"
                ],
                english_items=[
                    "Press the hole in the battery cover with a thin pointed tool while removing the cover."
                ],
            )
        ],
        uncertainties=[],
    )
    assert not _check_translation(
        paths[0], AgentReply(output_json=texts.model_dump_json()), False
    )
    grouped = assemble_page(page, plan, texts)
    validate_grouped_page(page, ContextPage.model_validate(grouped))
    assert grouped["source_blocks"] == [
        block.model_dump(mode="json", exclude_none=True) for block in page.blocks
    ]
    assert len(grouped["blocks"]) == 1
    assert grouped["source_blocks"][3]["text"] == "ON"
    assert plan.native_artwork == [3]


@pytest.mark.parametrize("boundary", ["column", "gap", "heading", "art"])
def test_reconstruction_respects_boundaries(tmp_path: Path, boundary: str) -> None:
    """Nearby text is insufficient when merging would steal a label or artwork."""
    prepare(tmp_path, boundary)
    plan = PagePlan.model_validate_json((tmp_path / "plan.json").read_bytes())
    assert plan.panels[0].source_ids == [0]


def test_dangling_output_gets_one_repair_then_private_fallback(tmp_path: Path) -> None:
    """A broken draft cannot be marked validated just because quantities match."""
    _, paths = prepare(tmp_path, "column")
    texts = PanelTranslations(
        panels=[
            PanelText(
                panel_id="start",
                japanese_items=["電池ぶたの穴を先の細い"],
                english_items=["(use) a thin pointed"],
            )
        ],
        uncertainties=[],
    )
    reply = AgentReply(output_json=texts.model_dump_json())
    repair = _check_translation(paths[0], reply, False)
    assert repair
    payload = json.loads(
        json.loads(Path(repair).read_bytes())["input_items"][0]["content"][0]["text"]
    )
    assert "incomplete" in payload["validation_error"]
    assert not _check_translation(repair, reply, True)
    assert not (tmp_path / "panel-start.validated.json").exists()
    assert (tmp_path / "panel-start.rejected-repair.json").exists()


def test_missing_action_is_not_completed_from_a_guess(tmp_path: Path) -> None:
    """A fluent invented action has no printed Japanese ending to support it."""
    _, paths = prepare(tmp_path, "column")
    texts = PanelTranslations(
        panels=[
            PanelText(
                panel_id="start",
                japanese_items=["電池ぶたの穴を先の細い棒で回してください。"],
                english_items=[
                    "Turn the hole in the battery cover with a thin pointed stick."
                ],
            )
        ],
        uncertainties=[],
    )
    repair = _check_translation(
        paths[0], AgentReply(output_json=texts.model_dump_json()), False
    )
    assert repair
    assert (
        "unsupported clause ending"
        in Path(tmp_path / "panel-start.rejected.json").read_text()
    )


def test_clause_detector_keeps_valid_predicate_adjectives() -> None:
    """A complete descriptive sentence must not be mistaken for a missing noun."""
    page = ContextPage.model_validate(
        {
            "image": "scan.jpg",
            "blocks": [
                {
                    "text": "先が尖っています。",
                    "translation": "The tip is pointed.",
                    "bbox": [0.1, 0.2, 0.3, 0.01],
                },
            ],
        }
    )
    plan = PagePlan(
        panels=[Panel(id="tip", kind="prose", source_ids=[0])], furigana=[], notes=[]
    )
    texts = PanelTranslations(
        panels=[
            PanelText(
                panel_id="tip",
                japanese_items=["先が尖っています。"],
                english_items=["The tip is pointed."],
            )
        ],
        uncertainties=[],
    )
    validate_translations(page, plan, texts)
    texts.panels[0].english_items = ["While pressing the cover,"]
    with pytest.raises(ValueError, match="incomplete"):
        validate_translations(page, plan, texts)
