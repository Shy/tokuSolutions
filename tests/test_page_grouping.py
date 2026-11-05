"""Keep source coverage while removing confirmed ruby from displayed translation."""

import pytest

from src.quality.cleanup_context import ContextBlock, ContextPage
from src.quality.page_grouping import (
    PagePlan,
    Panel,
    RubyLink,
    panel_box,
    validate_plan,
)


def source() -> ContextPage:
    """A normal sentence, its ruby, and a separate diagram label."""
    return ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="電池を入れてください。",
                translation="Insert batteries.",
                bbox=(0.1, 0.2, 0.4, 0.04),
            ),
            ContextBlock(
                text="でんち", translation="Electricity", bbox=(0.11, 0.18, 0.06, 0.015)
            ),
            ContextBlock(text="ON", translation="ON", bbox=(0.7, 0.2, 0.1, 0.04)),
        ],
    )


def test_group_bounds_cover_main_text_and_attached_furigana() -> None:
    """Discarding ruby English must not leave the Japanese guide visible underneath."""
    page = source()
    plan = PagePlan(
        panels=[Panel(id="battery", kind="prose", source_ids=[0])],
        furigana=[RubyLink(source_id=1, parent_id=0)],
        notes=[],
    )
    validate_plan(page, plan)
    bounds = panel_box(page, plan.panels[0], plan)
    assert bounds[1] <= 0.18
    assert bounds[1] + bounds[3] >= 0.24
    assert len(page.blocks) == 3


@pytest.mark.parametrize(
    "plan",
    [
        PagePlan(
            panels=[Panel(id="bad", kind="prose", source_ids=[0, 0])],
            furigana=[],
            notes=[],
        ),
        PagePlan(
            panels=[Panel(id="bad", kind="prose", source_ids=[0])],
            furigana=[RubyLink(source_id=2, parent_id=0)],
            notes=[],
        ),
        PagePlan(
            panels=[Panel(id="bad", kind="prose", source_ids=[0, 2])],
            furigana=[],
            notes=[],
        ),
    ],
)
def test_reject_duplicate_ids_literal_label_removal_and_cross_column_merges(
    plan: PagePlan,
) -> None:
    """A model cannot silently drop a code or combine unrelated page columns."""
    with pytest.raises(ValueError):
        validate_plan(source(), plan)


def test_group_translation_cannot_summarize_away_warnings_or_quantities() -> None:
    """Pair every source bullet with English and retain pacemaker/age facts."""
    from src.quality.page_grouping import (
        PanelText,
        PanelTranslations,
        validate_translations,
    )

    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="●ペースメーカーに近づけない。●3才未満には与えない。",
                translation="Old draft",
                bbox=(0.1, 0.2, 0.4, 0.2),
            )
        ],
    )
    plan = PagePlan(
        panels=[Panel(id="warning", kind="prose", source_ids=[0])],
        furigana=[],
        notes=[],
    )
    bad = PanelTranslations(
        panels=[
            PanelText(
                panel_id="warning",
                japanese_items=["注意"],
                english_items=["Use carefully."],
            )
        ],
        uncertainties=[],
    )
    with pytest.raises(ValueError):
        validate_translations(page, plan, bad)
    good = PanelTranslations(
        panels=[
            PanelText(
                panel_id="warning",
                japanese_items=[
                    "ペースメーカーに近づけない。",
                    "3才未満には与えない。",
                ],
                english_items=[
                    "Keep away from pacemakers.",
                    "Do not give to children under three.",
                ],
            )
        ],
        uncertainties=[],
    )
    validate_translations(page, plan, good)
    good.panels[0].english_items[1] = "Do not give to children under five."
    with pytest.raises(ValueError):
        validate_translations(page, plan, good)


def test_normalization_resolves_annotation_membership_and_keeps_invalid_guides() -> (
    None
):
    """Models can name all panel members; code separates confirmed ruby safely."""
    from src.quality.page_grouping import normalize_plan

    raw = PagePlan(
        panels=[Panel(id="battery", kind="prose", source_ids=[0, 1, 2])],
        furigana=[
            RubyLink(source_id=1, parent_id=0),
            RubyLink(source_id=2, parent_id=0),
        ],
        notes=[],
    )
    normalized = normalize_plan(source(), raw)
    validate_plan(source(), normalized)
    assert normalized.panels[0].source_ids == [0]
    assert [link.source_id for link in normalized.furigana] == [1]
    assert raw.panels[0].source_ids == [0, 1, 2]


def test_english_panel_cannot_leave_japanese_ocr_symbols_untranslated() -> None:
    """The plus/minus reading disambiguates OCR's 十一 glyph confusion."""
    from src.quality.page_grouping import (
        PanelText,
        PanelTranslations,
        validate_translations,
    )

    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="十一(プラスマイナス)を正しく",
                translation="Old",
                bbox=(0, 0, 0.5, 0.1),
            )
        ],
    )
    plan = PagePlan(
        panels=[Panel(id="polarity", kind="prose", source_ids=[0])],
        furigana=[],
        notes=[],
    )
    text = PanelTranslations(
        panels=[
            PanelText(
                panel_id="polarity",
                japanese_items=["+−（プラスマイナス）を正しくセットしてください"],
                english_items=["Set 十一 correctly."],
            )
        ],
        uncertainties=[],
    )
    with pytest.raises(ValueError):
        validate_translations(page, plan, text)
    text.panels[0].english_items = [
        "Install batteries with the correct polarity (+/−)."
    ]
    validate_translations(page, plan, text)


def test_caregiver_instruction_cannot_be_reduced_to_a_heading() -> None:
    """Reading/explanation instructions are content, not optional boilerplate."""
    from src.quality.page_grouping import (
        PanelText,
        PanelTranslations,
        validate_translations,
    )

    source_text = "この枠内では、本商品をご使用になるときに、注意していただきたいことを記載しております。よくお読みいただき、お子様へご説明ください。"
    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text=source_text, translation="Old", bbox=(0.7, 0.7, 0.2, 0.05)
            )
        ],
    )
    plan = PagePlan(
        panels=[Panel(id="parents", kind="prose", source_ids=[0])],
        furigana=[],
        notes=[],
    )
    text = PanelTranslations(
        panels=[
            PanelText(
                panel_id="parents",
                japanese_items=[source_text],
                english_items=["Precautions (important safety information)"],
            )
        ],
        uncertainties=[],
    )
    with pytest.raises(ValueError):
        validate_translations(page, plan, text)
    text.panels[0].english_items = [
        "This box lists precautions for using this product. Read them carefully and explain them to your child."
    ]
    validate_translations(page, plan, text)


def test_mixed_kana_reading_guides_can_attach_to_acronyms_and_kanji() -> None:
    """DX and Hiden pronunciation aids are not additional English product words."""
    from src.quality.semantic_layout import source_candidates

    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="デラックス ひでん",
                translation="Deluxe Hiden",
                bbox=(0.1, 0.18, 0.13, 0.012),
            ),
            ContextBlock(
                text="DX飛電ゼロワンドライバー",
                translation="DX Hiden Zero-One Driver",
                bbox=(0.1, 0.2, 0.4, 0.035),
            ),
            ContextBlock(
                text="デラックス", translation="Deluxe", bbox=(0.6, 0.18, 0.08, 0.012)
            ),
            ContextBlock(text="DX", translation="DX", bbox=(0.6, 0.2, 0.1, 0.035)),
        ],
    )
    plan = PagePlan(
        panels=[],
        furigana=[
            RubyLink(source_id=0, parent_id=1),
            RubyLink(source_id=2, parent_id=3),
        ],
        notes=[],
    )
    validate_plan(page, plan)
    candidates = source_candidates(page)
    assert candidates[0]["eligible_ruby_parents"] == [1]
    assert candidates[2]["eligible_ruby_parents"] == [3]


def test_katakana_instruction_at_normal_text_size_is_not_a_reading_guide() -> None:
    """Allowing katakana must not erase ordinary mode names or instructions."""
    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="リセット", translation="Reset", bbox=(0.1, 0.18, 0.2, 0.035)
            ),
            ContextBlock(text="電源", translation="Power", bbox=(0.1, 0.2, 0.3, 0.035)),
        ],
    )
    with pytest.raises(ValueError, match="Unconfirmed furigana"):
        validate_plan(
            page,
            PagePlan(
                panels=[], furigana=[RubyLink(source_id=0, parent_id=1)], notes=[]
            ),
        )


def test_foreign_script_ocr_needs_scan_confirmation_before_suppression() -> None:
    """Arrow ticks misread as Hebrew remain visible until scan classification."""
    from src.quality.page_grouping import native_artwork_candidate, normalize_plan

    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="יווווי", translation="יווווי", bbox=(0.4, 0.7, 0.01, 0.02)
            )
        ],
    )
    assert native_artwork_candidate("יווווי") == "scan"
    assert (
        normalize_plan(page, PagePlan(panels=[], furigana=[], notes=[])).native_artwork
        == []
    )
