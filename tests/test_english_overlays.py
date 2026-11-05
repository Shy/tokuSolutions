"""English display checks cover scripts without deleting source information."""

import asyncio
import json
from pathlib import Path

import pytest

from src.quality.cleanup_context import ContextBlock, ContextPage
from src.quality.english_overlays import (
    include_untranslated_labels,
    normalize_battery_codes,
    normalize_parallel_quotes,
    unexpected_letters,
)
from src.quality.page_grouping import PagePlan, RubyLink


def test_scripts_are_flagged_but_english_accents_units_and_symbols_are_allowed() -> (
    None
):
    """Script detection is broader than Hebrew and does not reject English units."""
    assert not unexpected_letters("2 × AAA; 3 µA; 10 Ω; +/−; ①; café")
    for text in ("単4", "電池", "יווווי", "Блок", "배터리", "بطارية"):
        assert unexpected_letters(text)


def test_battery_normalization_is_source_backed_and_retains_counts() -> None:
    """Japanese battery-size names become familiar English codes, not quantities."""
    assert (
        normalize_battery_codes("単4乾電池2本", "Uses 2 単4 batteries")
        == "Uses 2 AAA batteries"
    )
    assert normalize_battery_codes("単4", "AAA (単4)") == "AAA"
    assert normalize_battery_codes("単3", "単3 x3") == "AA x3"
    assert normalize_battery_codes("電池", "単4 x2") == "単4 x2"


def test_parallel_japanese_quotes_keep_the_existing_english_equivalent() -> None:
    """Only explicit source-backed parallel translations can lose duplicate Japanese."""
    source = "「秘技!」が鳴り、その後「秘技!」を繰り返します。"
    text = 'The line "秘技!" ("Secret Technique!") plays. Then "秘技!" repeats.'
    assert (
        normalize_parallel_quotes(source, text)
        == 'The line "Secret Technique!" plays. Then "Secret Technique!" repeats.'
    )
    assert normalize_parallel_quotes("別の言葉", text) == text
    assert (
        normalize_parallel_quotes(source, 'The line "秘技!" plays.')
        == 'The line "秘技!" plays.'
    )


def test_retained_labels_are_translated_but_guides_and_artwork_are_not_reclaimed() -> (
    None
):
    """The planner's unlisted-label fallback must not bypass English checks."""
    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="コール", translation="コール", bbox=(0.1, 0.2, 0.2, 0.03)
            ),
            ContextBlock(
                text="でんち", translation="でんち", bbox=(0.1, 0.1, 0.1, 0.01)
            ),
            ContextBlock(
                text="電池", translation="Batteries", bbox=(0.1, 0.12, 0.2, 0.03)
            ),
            ContextBlock(
                text="יווווי", translation="יווווי", bbox=(0.7, 0.2, 0.01, 0.02)
            ),
        ],
    )
    plan = PagePlan(
        panels=[],
        furigana=[RubyLink(source_id=1, parent_id=2)],
        native_artwork=[3],
        notes=[],
    )
    updated = include_untranslated_labels(page, plan)
    assert [panel.source_ids for panel in updated.panels] == [[0]]
    assert not plan.panels


def test_non_japanese_foreign_script_is_rejected_by_translation_validation() -> None:
    """A model output cannot evade the English guard by using another script."""
    from src.quality.page_grouping import (
        Panel,
        PanelText,
        PanelTranslations,
        validate_translations,
    )

    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="電池", translation="Batteries", bbox=(0.1, 0.1, 0.2, 0.03)
            )
        ],
    )
    plan = PagePlan(
        panels=[Panel(id="label", kind="heading", source_ids=[0])],
        furigana=[],
        notes=[],
    )
    with pytest.raises(ValueError, match="other-script"):
        validate_translations(
            page,
            plan,
            PanelTranslations(
                panels=[
                    PanelText(
                        panel_id="label",
                        japanese_items=["電池"],
                        english_items=["بطارية"],
                    )
                ],
                uncertainties=[],
            ),
        )


def test_untranslated_fallback_is_reported_without_deleting_source(
    tmp_path: Path,
) -> None:
    """Failed translation retains information but cannot silently pass as English."""
    from src.activities.semantic_layout import apply_semantic_manual_activity
    from src.quality.page_grouping import PanelTranslations
    from src.quality.semantic_layout import assemble_page

    page = ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="コール", translation="コール", bbox=(0.1, 0.1, 0.2, 0.03)
            )
        ],
    )
    manual = tmp_path / "translations.json"
    manual.write_text(
        json.dumps(
            {"meta": {}, "pages": [page.model_dump(mode="json", exclude_none=True)]}
        )
    )
    result = assemble_page(
        page,
        PagePlan(panels=[], furigana=[], notes=[]),
        PanelTranslations(panels=[], uncertainties=[]),
    )
    output = tmp_path / "result.json"
    output.write_text(json.dumps(result))
    warnings = asyncio.run(apply_semantic_manual_activity(str(manual), [str(output)]))
    assert any("retained non-English overlay" in warning for warning in warnings)
    assert (
        json.loads(manual.read_text())["pages"][0]["source_blocks"][0]["text"]
        == "コール"
    )
