"""Readable scan artwork loses only its redundant English mask."""

from copy import deepcopy

import pytest
from pydantic import TypeAdapter

from src.quality.cleanup_context import ContextPage
from src.quality.page_grouping import PagePlan, Panel, PanelTranslations, SourceMapping
from src.quality.semantic_layout import (
    assemble_page,
    safe_plan,
    ungroup_page,
    validate_grouped_page,
)


def artwork_page(texts: list[str]) -> ContextPage:
    """Keep fixtures separated so neighboring masks cannot hide a symbol."""
    return ContextPage.model_validate(
        {
            "image": "scan.webp",
            "blocks": [
                {
                    "text": text,
                    "translation": "old English",
                    "bbox": [0.1, 0.05 + index * 0.05, 0.2, 0.02],
                }
                for index, text in enumerate(texts)
            ],
        }
    )


def test_native_symbols_preserve_scan_and_every_source_through_regroup() -> None:
    page = artwork_page(
        [
            "+",
            "−",
            "⊕",
            "→",
            "LED",
            "ON/OFF",
            "電池の＋と－を確認してください。",
            "LR44 x 3",
            "?",
            "ー",
            "電池2本",
            "ONにします。",
        ]
    )
    plan = safe_plan(page, PagePlan(panels=[], furigana=[], notes=[]))
    grouped = assemble_page(page, plan, PanelTranslations(panels=[], uncertainties=[]))
    assert [block["text"] for block in grouped["blocks"]] == [
        block.text for block in page.blocks[6:]
    ]
    assert grouped["source_blocks"] == [
        block.model_dump(mode="json", exclude_none=True) for block in page.blocks
    ]
    ledger = TypeAdapter(list[SourceMapping]).validate_python(
        grouped["layout_provenance"]["mapping"]
    )
    assert len(ledger) == len(page.blocks)
    assert [row["display_index"] for row in ledger[:6]] == [-1] * 6
    validate_grouped_page(page, ContextPage.model_validate(grouped))
    restored = ContextPage.model_validate(ungroup_page(grouped))
    regrouped = assemble_page(
        restored,
        safe_plan(restored, plan),
        PanelTranslations(panels=[], uncertainties=[]),
    )
    assert regrouped == grouped


def test_scan_confirmed_english_and_step_labels_need_bounded_eligibility() -> None:
    page = artwork_page(
        ["SUPER BEST", "①-1", "Battery polarity", "LR44 x 3", "単4電池", "?", "A"]
    )
    plan = safe_plan(
        page,
        PagePlan(panels=[], furigana=[], notes=[], native_artwork=[0, 1, 3, 4, 5, 6]),
    )
    assert plan.native_artwork == [0, 1]
    grouped = assemble_page(page, plan, PanelTranslations(panels=[], uncertainties=[]))
    assert [block["text"] for block in grouped["blocks"]] == [
        block.text for block in page.blocks[2:]
    ]
    forged = deepcopy(grouped)
    mapping = TypeAdapter(list[SourceMapping]).validate_python(
        forged["layout_provenance"]["mapping"]
    )
    mapping[-1]["display_index"] = -1
    forged["layout_provenance"]["mapping"] = TypeAdapter(
        list[SourceMapping]
    ).dump_python(mapping, mode="json")
    with pytest.raises(ValueError):
        validate_grouped_page(page, ContextPage.model_validate(forged))


def test_panels_cannot_swallow_native_symbols() -> None:
    page = artwork_page(["電池を入れます。", "+", "ふたを閉めます。"])
    plan = safe_plan(
        page,
        PagePlan(
            panels=[Panel(id="unsafe", kind="prose", source_ids=[0, 1, 2])],
            furigana=[],
            notes=[],
        ),
    )
    assert all(1 not in panel.source_ids for panel in plan.panels)
    assert [panel.source_ids for panel in plan.panels] == [[0], [2]]


def test_adjacent_printed_clause_pairs_join_without_new_words() -> None:
    """Keep source-supported actions when the model splits one numbered clause."""
    from src.quality.page_grouping import PanelText, join_printed_continuations

    fragments = PanelTranslations(
        panels=[
            PanelText(
                panel_id="cover",
                japanese_items=["穴を棒で押し、", "ふたを取り外します。"],
                english_items=[
                    "Press the hole with a stick,",
                    "then remove the cover.",
                ],
            )
        ],
        uncertainties=[],
    )
    joined = join_printed_continuations(fragments)
    assert joined.panels[0].english_items == [
        "Press the hole with a stick, then remove the cover."
    ]
    assert joined.panels[0].japanese_items == ["穴を棒で押し、\nふたを取り外します。"]
    assert len(fragments.panels[0].english_items) == 2


def test_battery_designation_rejects_contradictory_single_a() -> None:
    from src.quality.page_grouping import PanelText, validate_translations

    page = artwork_page(["単4乾電池3本を入れます。"])
    plan = PagePlan(
        panels=[Panel(id="battery", kind="prose", source_ids=[0])],
        furigana=[],
        notes=[],
    )
    text = PanelTranslations(
        panels=[
            PanelText(
                panel_id="battery",
                japanese_items=[page.blocks[0].text],
                english_items=["Insert three AAA (single-A) batteries."],
            )
        ],
        uncertainties=[],
    )
    with pytest.raises(ValueError, match="battery type"):
        validate_translations(page, plan, text)
