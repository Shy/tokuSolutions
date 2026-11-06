"""Production semantic grouping keeps source coverage and editor provenance."""

from copy import deepcopy
from pathlib import Path

import pytest

from src.quality.cleanup_context import ContextPage
from src.quality.page_grouping import (
    PagePlan,
    Panel,
    PanelText,
    PanelTranslations,
    RubyLink,
)
from src.quality.semantic_layout import assemble_page, validate_grouped_page


def test_regroup_preserves_confirmed_native_artwork_on_unchanged_source() -> None:
    """Previously confirmed arrow ticks must not reappear as foreign-letter overlays."""
    from src.quality.semantic_layout import safe_plan, ungroup_page

    original = ContextPage.model_validate(
        {
            "image": "scan.png",
            "blocks": [
                {"text": "יוו", "translation": "יוו", "bbox": [0.2, 0.2, 0.05, 0.02]}
            ],
        }
    )
    accepted = assemble_page(
        original,
        PagePlan(panels=[], furigana=[], notes=[], native_artwork=[0]),
        PanelTranslations(panels=[], uncertainties=[]),
    )
    source = ContextPage.model_validate(ungroup_page(accepted))
    checked = safe_plan(source, PagePlan(panels=[], furigana=[], notes=[]))
    assert checked.native_artwork == [0]
    edited = source.model_copy(deep=True)
    edited.blocks[0].bbox = (0.4, 0.4, 0.05, 0.02)
    assert not safe_plan(
        edited, PagePlan(panels=[], furigana=[], notes=[])
    ).native_artwork


def test_untranslated_fallback_reuses_accepted_english_for_identical_source_membership() -> (
    None
):
    """A dropped one-source panel must not restore its older untranslated English field."""
    from src.quality.semantic_layout import preserve_complete_panels

    source = ContextPage.model_validate(
        {
            "image": "scan.png",
            "blocks": [
                {"text": "ケ", "translation": "ケ", "bbox": [0.1, 0.1, 0.2, 0.1]}
            ],
        }
    )
    previous = ContextPage.model_validate(
        assemble_page(
            source,
            PagePlan(
                panels=[Panel(id="label", kind="heading", source_ids=[0])],
                furigana=[],
                notes=[],
            ),
            PanelTranslations(
                panels=[
                    PanelText(
                        panel_id="label", japanese_items=["ケ"], english_items=["Ke"]
                    )
                ],
                uncertainties=[],
            ),
        )
    )
    fallback = assemble_page(
        source,
        PagePlan(panels=[], furigana=[], notes=[]),
        PanelTranslations(panels=[], uncertainties=[]),
    )
    repaired = preserve_complete_panels(previous, fallback)
    assert repaired["blocks"][0]["translation"] == "Ke"
    validate_grouped_page(source, ContextPage.model_validate(repaired))


def test_non_english_returned_translation_uses_existing_bounded_repair(
    tmp_path: Path,
) -> None:
    """New Japanese quotations cannot bypass the English-overlay guard."""
    import json

    from PIL import Image

    from src.activities.semantic_layout import _check_translation
    from src.agent_models import AgentReply

    page = ContextPage.model_validate(
        {
            "image": "scan.jpg",
            "blocks": [
                {
                    "text": "変身します。",
                    "translation": "Transform!",
                    "bbox": [0.1, 0.1, 0.4, 0.1],
                }
            ],
        }
    )
    panel = Panel(id="step", kind="prose", source_ids=[0])
    (tmp_path / "source-page.json").write_text(page.model_dump_json())
    (tmp_path / "plan.json").write_text(
        PagePlan(panels=[panel], furigana=[], notes=[]).model_dump_json()
    )
    (tmp_path / "lines.json").write_text("[]")
    Image.new("RGB", (400, 400), "white").save(tmp_path / "scan.jpg")
    request = tmp_path / "panel-step.request.json"
    request.write_text(
        json.dumps(
            {
                "input_items": [
                    {
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "panel": panel.model_dump(),
                                        "accepted_glossary": {},
                                    }
                                )
                            }
                        ]
                    }
                ]
            }
        )
    )
    texts = PanelTranslations(
        panels=[
            PanelText(
                panel_id="step",
                japanese_items=["変身します。"],
                english_items=["Transform! (変身)"],
            )
        ],
        uncertainties=[],
    )
    repair = _check_translation(
        str(request), AgentReply(output_json=texts.model_dump_json()), False
    )
    assert repair.endswith(".repair.request.json")
    assert not (tmp_path / "panel-step.validated.json").exists()


def example() -> tuple[dict[str, object], PagePlan, PanelTranslations]:
    """A safety paragraph, its reading guide and an unrelated switch label."""
    page: dict[str, object] = {
        "image": "pages/page-0.webp",
        "blocks": [
            {
                "text": "電池を入れてください。",
                "translation": "Insert batteries.",
                "bbox": [0.1, 0.2, 0.4, 0.04],
            },
            {
                "text": "でんち",
                "translation": "Electricity",
                "bbox": [0.11, 0.18, 0.06, 0.015],
            },
            {"text": "ON", "translation": "ON", "bbox": [0.7, 0.2, 0.1, 0.04]},
        ],
    }
    plan = PagePlan(
        panels=[Panel(id="battery", kind="prose", source_ids=[0])],
        furigana=[RubyLink(source_id=1, parent_id=0)],
        notes=[],
    )
    texts = PanelTranslations(
        panels=[
            PanelText(
                panel_id="battery",
                japanese_items=["電池を入れてください。"],
                english_items=["Insert batteries."],
            )
        ],
        uncertainties=[],
    )
    return page, plan, texts


def test_grouped_output_retains_all_sources_and_validates_coverage() -> None:
    """Source Japanese survives while guides disappear only from display English."""
    source, plan, texts = example()
    grouped = assemble_page(ContextPage.model_validate(source), plan, texts)
    validate_grouped_page(
        ContextPage.model_validate(source), ContextPage.model_validate(grouped)
    )
    assert len(grouped["blocks"]) == 2
    assert grouped["blocks"][0]["layout"] == "panel"
    assert grouped["source_blocks"] == source["blocks"]
    assert "Electricity" not in [block["translation"] for block in grouped["blocks"]]
    broken = deepcopy(grouped)
    ledger = broken["layout_provenance"]
    mapping = ledger["mapping"]
    assert isinstance(mapping, list) and isinstance(mapping[0], dict)
    mapping[0]["furigana_ids"] = []
    with pytest.raises(ValueError, match="coverage"):
        validate_grouped_page(
            ContextPage.model_validate(source), ContextPage.model_validate(broken)
        )


def test_source_edits_or_invented_display_text_fail_publication_validation() -> None:
    """A grouping ledger must not become a bypass for deleting or inventing Japanese."""
    source, plan, texts = example()
    grouped = assemble_page(ContextPage.model_validate(source), plan, texts)
    grouped["blocks"][0]["text"] = "invented"
    with pytest.raises(ValueError):
        validate_grouped_page(
            ContextPage.model_validate(source), ContextPage.model_validate(grouped)
        )


def test_full_width_paragraph_is_allowed_without_merging_separate_columns() -> None:
    """A portrait manual may have a single wide text column."""
    from src.quality.page_grouping import validate_plan

    source, _, _ = example()
    page = ContextPage.model_validate(source)
    page.blocks[0].bbox = (0.05, 0.2, 0.9, 0.04)
    page.blocks[2].bbox = (0.7, 0.6, 0.1, 0.04)
    validate_plan(
        page,
        PagePlan(
            panels=[Panel(id="wide", kind="prose", source_ids=[0])],
            furigana=[],
            notes=[],
        ),
    )


def test_prior_english_cleanup_does_not_invalidate_preserved_japanese() -> None:
    """The ledger protects source Japanese rather than obsolete machine English."""
    source, plan, texts = example()
    page = ContextPage.model_validate(source)
    grouped = ContextPage.model_validate(assemble_page(page, plan, texts))
    page.blocks[0].translation = "Earlier draft before cleanup"
    validate_grouped_page(page, grouped)


def test_layout_guard_rejects_masking_illustrations_between_text_lines(
    tmp_path: Path,
) -> None:
    """A source-ID-complete panel must still preserve an intervening illustration."""
    from PIL import Image, ImageDraw

    from src.quality.semantic_layout import panel_preserves_illustrations

    image = Image.new("RGB", (600, 800), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((140, 240, 260, 420), fill="black")
    image_path = tmp_path / "scan.png"
    image.save(image_path)
    page = ContextPage.model_validate(
        {
            "image": "scan.png",
            "blocks": [
                {
                    "text": "文章を読みます。",
                    "translation": "Read.",
                    "bbox": [0.1, 0.2, 0.4, 0.04],
                },
                {
                    "text": "次に進みます。",
                    "translation": "Continue.",
                    "bbox": [0.1, 0.6, 0.4, 0.04],
                },
            ],
        }
    )
    plan = PagePlan(
        panels=[Panel(id="bad", kind="prose", source_ids=[0, 1])], furigana=[], notes=[]
    )
    assert not panel_preserves_illustrations(page, plan, plan.panels[0], image_path, [])
    drawing.rectangle((140, 240, 260, 420), fill="white")
    image.save(image_path)
    assert panel_preserves_illustrations(page, plan, plan.panels[0], image_path, [])


def test_solid_colored_panel_background_is_not_an_illustration(tmp_path: Path) -> None:
    """White caregiver text may be printed on a solid pink background."""
    from PIL import Image

    from src.quality.semantic_layout import panel_preserves_illustrations

    image_path = tmp_path / "pink.png"
    Image.new("RGB", (600, 800), (230, 0, 90)).save(image_path)
    source, plan, _ = example()
    assert panel_preserves_illustrations(
        ContextPage.model_validate(source), plan, plan.panels[0], image_path, []
    )


def test_panel_with_intervening_art_is_split_into_safe_text_units(
    tmp_path: Path,
) -> None:
    """Keep both instructions while avoiding a large mask over their illustration."""
    from PIL import Image, ImageDraw

    from src.quality.semantic_layout import split_illustrated_panel

    image_path = tmp_path / "scan.png"
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).rectangle((140, 240, 260, 420), fill="black")
    image.save(image_path)
    page = ContextPage.model_validate(
        {
            "image": "scan.png",
            "blocks": [
                {
                    "text": "文章を読みます。",
                    "translation": "Read.",
                    "bbox": [0.1, 0.2, 0.4, 0.04],
                },
                {
                    "text": "次に進みます。",
                    "translation": "Continue.",
                    "bbox": [0.1, 0.6, 0.4, 0.04],
                },
            ],
        }
    )
    panel = Panel(id="body", kind="prose", source_ids=[0, 1])
    plan = PagePlan(panels=[panel], furigana=[], notes=[])
    parts = split_illustrated_panel(page, plan, panel, image_path, [])
    assert [part.source_ids for part in parts] == [[0], [1]]


def test_ocr_continuation_stops_before_the_next_heading() -> None:
    """Recover a clipped second line without translating the following heading."""
    from src.quality.page_grouping import OCRLine
    from src.quality.semantic_layout import panel_geometry, panel_references

    page = ContextPage.model_validate(
        {
            "image": "scan.png",
            "blocks": [
                {
                    "text": "3才未満には",
                    "translation": "Under three",
                    "bbox": [0.1, 0.2, 0.4, 0.02],
                },
                {
                    "text": "警告",
                    "translation": "Warning",
                    "bbox": [0.1, 0.26, 0.4, 0.04],
                },
            ],
        }
    )
    panel = Panel(id="age", kind="prose", source_ids=[0])
    plan = PagePlan(panels=[panel], furigana=[], notes=[])
    lines = [
        OCRLine(id=0, text="3才未満には", bbox=(0.1, 0.2, 0.4, 0.02)),
        OCRLine(id=1, text="与えないでください。", bbox=(0.1, 0.23, 0.4, 0.02)),
        OCRLine(id=2, text="警告", bbox=(0.1, 0.26, 0.4, 0.04)),
    ]
    bounds = panel_geometry(page, plan, panel, lines)
    assert 0.25 < bounds[1] + bounds[3] < 0.27
    assert "与えない" in panel_references(page, plan, lines)["age"]
    assert "警告" not in panel_references(page, plan, lines)["age"]


def test_regroup_keeps_current_edits_to_ungrouped_labels() -> None:
    """Reprocessing a grouped manual retains final label edits as its next draft."""
    from src.quality.semantic_layout import ungroup_page

    source, plan, texts = example()
    grouped = assemble_page(ContextPage.model_validate(source), plan, texts)
    grouped["blocks"][1]["translation"] = "Edited switch label"
    restored = ungroup_page(grouped)
    assert len(restored["blocks"]) == 3
    assert restored["blocks"][2]["translation"] == "Edited switch label"
    assert restored["blocks"][0]["text"] == "電池を入れてください。"
    assert restored["layout_provenance"]["previous_page"] == grouped


def test_translation_does_not_import_a_neighboring_heading() -> None:
    """The under-three warning must not absorb the next Caution heading."""
    from src.quality.page_grouping import validate_translations

    source, plan, texts = example()
    texts.panels[0].japanese_items.append("注意")
    texts.panels[0].english_items.append("Caution")
    with pytest.raises(ValueError, match="heading"):
        validate_translations(ContextPage.model_validate(source), plan, texts)


def test_crop_requests_use_the_split_panel_ids(tmp_path: Path) -> None:
    """The production preparer rebuilds OCR references after splitting a panel."""
    import json

    from PIL import Image, ImageDraw

    from src.activities.semantic_layout import _plan_page
    from src.agent_models import AgentReply

    page = ContextPage.model_validate(
        {
            "image": "scan.jpg",
            "blocks": [
                {
                    "text": "文章を読みます。",
                    "translation": "Read.",
                    "bbox": [0.1, 0.2, 0.4, 0.04],
                },
                {
                    "text": "次に進みます。",
                    "translation": "Continue.",
                    "bbox": [0.1, 0.6, 0.4, 0.04],
                },
            ],
        }
    )
    (tmp_path / "source-page.json").write_text(page.model_dump_json())
    (tmp_path / "lines.json").write_text("[]")
    (tmp_path / "glossary.json").write_text("{}")
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).rectangle((140, 240, 260, 420), fill="black")
    image.save(tmp_path / "scan.jpg")
    plan = PagePlan(
        panels=[Panel(id="body", kind="prose", source_ids=[0, 1])],
        furigana=[],
        notes=[],
    )
    paths = _plan_page(
        str(tmp_path / "plan.request.json"),
        AgentReply(output_json=plan.model_dump_json()),
    )
    assert len(paths) == 2
    for path in paths:
        request = json.loads(Path(path).read_bytes())
        payload = json.loads(request["input_items"][0]["content"][0]["text"])
        assert payload["panel"]["id"] in {"body-a", "body-b"}
        assert "fresh_panel_ocr" in payload


def test_editorial_uncertainty_is_not_inserted_into_translated_instructions() -> None:
    """OCR caveats belong in uncertainty records rather than the manual's prose."""
    from src.quality.page_grouping import validate_translations

    source, plan, texts = example()
    texts.panels[0].english_items[0] += " (sentence incomplete in source)"
    with pytest.raises(ValueError, match="uncertainty"):
        validate_translations(ContextPage.model_validate(source), plan, texts)


def test_panel_requests_keep_concise_english_rules_and_glossary_on_repair(
    tmp_path: Path,
) -> None:
    """Check the real request contract, not model quality from a fake reply."""
    import json

    from PIL import Image

    from src.activities.semantic_layout import _check_translation, _plan_page
    from src.agent_models import AgentReply
    from src.quality.semantic_layout import TRANSLATE_INSTRUCTIONS

    source, plan, _ = example()
    page = ContextPage.model_validate(source)
    (tmp_path / "source-page.json").write_text(page.model_dump_json())
    (tmp_path / "lines.json").write_text("[]")
    (tmp_path / "glossary.json").write_text(
        json.dumps({"電池": "batteries"}, ensure_ascii=False)
    )
    Image.new("RGB", (600, 800), "white").save(tmp_path / "scan.jpg")
    paths = _plan_page(
        str(tmp_path / "plan.request.json"),
        AgentReply(output_json=plan.model_dump_json()),
    )
    assert len(paths) == 1
    repair_path = _check_translation(paths[0], AgentReply(output_json="{}"), False)
    assert repair_path
    for path in [paths[0], repair_path]:
        request = json.loads(Path(path).read_bytes())
        instructions = request["instructions"]
        assert instructions.startswith(TRANSLATE_INSTRUCTIONS)
        for rule in (
            "direct, concise, natural English",
            "Use direct verbs",
            '"please understand this"',
            "only when it adds no practical meaning",
            "useful qualifications",
            "Never summarize",
            "negation, condition, quantity",
            "Keep codes/literal labels",
            "Do not mechanically delete repeated words",
        ):
            assert rule in instructions
        payload = json.loads(request["input_items"][0]["content"][0]["text"])
        assert payload["accepted_glossary"] == {"電池": "batteries"}
        assert payload["source_text"] == "電池を入れてください。"
    assert "validation_error" in payload


def test_concise_caregiver_english_keeps_actions_and_exact_source_ledger() -> None:
    """Removing courtesy wording must leave reading/explanation instructions."""
    source, plan, texts = example()
    blocks = source["blocks"]
    assert isinstance(blocks, list)
    blocks[0]["text"] = "保護者の方へ。よくお読みいただき、お子様へご説明ください。"
    texts.panels[0].japanese_items = [blocks[0]["text"]]
    texts.panels[0].english_items = [
        "Parents: read carefully and explain these instructions to your child."
    ]
    page = ContextPage.model_validate(source)
    grouped = assemble_page(page, plan, texts)
    assert grouped["blocks"][0]["translation"] == texts.panels[0].english_items[0]
    assert grouped["source_blocks"] == [
        block.model_dump(mode="json", exclude_none=True) for block in page.blocks
    ]
    texts.panels[0].english_items = ["Parents: read carefully."]
    with pytest.raises(ValueError, match="safety fact missing"):
        assemble_page(page, plan, texts)


def test_confirmed_katakana_guides_are_not_translation_reference_words() -> None:
    """DX's Deluxe reading is excluded while real katakana mode text is retained."""
    from src.quality.page_grouping import OCRLine
    from src.quality.semantic_layout import panel_references

    page = ContextPage.model_validate(
        {
            "image": "scan.png",
            "blocks": [
                {
                    "text": "デラックス ひでん",
                    "translation": "Deluxe Hiden",
                    "bbox": [0.1, 0.18, 0.13, 0.012],
                },
                {
                    "text": "DX飛電ゼロワンドライバー\nリセット",
                    "translation": "DX Hiden Zero-One Driver\nReset",
                    "bbox": [0.1, 0.2, 0.4, 0.08],
                },
            ],
        }
    )
    plan = PagePlan(
        panels=[Panel(id="body", kind="prose", source_ids=[1])],
        furigana=[RubyLink(source_id=0, parent_id=1)],
        notes=[],
    )
    lines = [
        OCRLine(id=0, text="デラックス ひでん", bbox=(0.1, 0.18, 0.13, 0.012)),
        OCRLine(id=1, text="DX飛電ゼロワンドライバー", bbox=(0.1, 0.2, 0.4, 0.03)),
        OCRLine(id=2, text="リセット", bbox=(0.1, 0.24, 0.2, 0.025)),
    ]
    reference = panel_references(page, plan, lines)["body"]
    assert "デラックス" not in reference
    assert "リセット" in reference


def test_unclaimed_callout_fragments_recover_without_merging_contents(
    tmp_path: Path,
) -> None:
    """Seed recovery from a dangling source clause, keeping separate contents rows."""
    from PIL import Image

    from src.quality.semantic_layout import recover_clause_panels

    image = tmp_path / "scan.png"
    Image.new("RGB", (1000, 1400), "white").save(image)
    page = ContextPage.model_validate(
        {
            "blocks": [
                {
                    "text": "ボタン電池は上側が＋に、下側が−に",
                    "translation": "The top is plus, bottom is",
                    "bbox": [0.3, 0.4, 0.12, 0.03],
                },
                {
                    "text": "なるように",
                    "translation": "As it happens",
                    "bbox": [0.3, 0.434, 0.10, 0.01],
                },
                {
                    "text": "セットします。",
                    "translation": "Set them.",
                    "bbox": [0.3, 0.448, 0.12, 0.01],
                },
                {
                    "text": "使い方 … 4",
                    "translation": "How to use … 4",
                    "bbox": [0.6, 0.4, 0.25, 0.02],
                },
                {
                    "text": "電池交換 … 5",
                    "translation": "Battery replacement … 5",
                    "bbox": [0.6, 0.425, 0.25, 0.02],
                },
            ]
        }
    )
    recovered = recover_clause_panels(
        page, PagePlan(panels=[], furigana=[], notes=[]), image, []
    )
    assert [panel.source_ids for panel in recovered.panels] == [[0, 1, 2]]


def test_regroup_keeps_previously_complete_instruction_when_new_plan_splits_it() -> (
    None
):
    """An accepted sentence must not regress into separately translated fragments."""
    from src.quality.semantic_layout import preserve_complete_panels

    source = ContextPage.model_validate(
        {
            "image": "page.webp",
            "blocks": [
                {
                    "text": "押しながら、",
                    "translation": "While pressing,",
                    "bbox": [0.1, 0.2, 0.2, 0.02],
                },
                {
                    "text": "合わせます。",
                    "translation": "align the joint.",
                    "bbox": [0.1, 0.225, 0.2, 0.02],
                },
                {
                    "text": "目次 4",
                    "translation": "Contents 4",
                    "bbox": [0.6, 0.2, 0.2, 0.02],
                },
            ],
        }
    )
    previous = assemble_page(
        source,
        PagePlan(
            panels=[Panel(id="complete", kind="prose", source_ids=[0, 1])],
            furigana=[],
            notes=[],
        ),
        PanelTranslations(
            panels=[
                PanelText(
                    panel_id="complete",
                    japanese_items=["押しながら、合わせます。"],
                    english_items=["While pressing, align the joint."],
                )
            ],
            uncertainties=[],
        ),
    )
    split = assemble_page(
        source,
        PagePlan(panels=[], furigana=[], notes=[]),
        PanelTranslations(panels=[], uncertainties=[]),
    )
    recovered = preserve_complete_panels(ContextPage.model_validate(previous), split)
    assert len(recovered["blocks"]) == 2
    assert recovered["blocks"][0]["translation"] == "While pressing, align the joint."
    assert recovered["blocks"][1]["translation"] == "Contents 4"
    validate_grouped_page(source, ContextPage.model_validate(recovered))


def test_regroup_does_not_restore_a_merged_contents_list() -> None:
    """Contents entries remain separate even if an earlier plan merged the column."""
    from src.quality.semantic_layout import preserve_complete_panels

    source = ContextPage.model_validate(
        {
            "image": "page.webp",
            "blocks": [
                {
                    "text": "目次",
                    "translation": "Table of Contents",
                    "bbox": [0.1, 0.1, 0.2, 0.02],
                },
                {
                    "text": "使い方",
                    "translation": "How to use",
                    "bbox": [0.1, 0.2, 0.2, 0.02],
                },
                {
                    "text": "電池交換",
                    "translation": "Battery replacement",
                    "bbox": [0.1, 0.23, 0.2, 0.02],
                },
            ],
        }
    )
    previous = assemble_page(
        source,
        PagePlan(
            panels=[Panel(id="contents", kind="prose", source_ids=[1, 2])],
            furigana=[],
            notes=[],
        ),
        PanelTranslations(
            panels=[
                PanelText(
                    panel_id="contents",
                    japanese_items=["使い方", "電池交換"],
                    english_items=["How to use", "Battery replacement"],
                )
            ],
            uncertainties=[],
        ),
    )
    separate = assemble_page(
        source,
        PagePlan(panels=[], furigana=[], notes=[]),
        PanelTranslations(panels=[], uncertainties=[]),
    )
    result = preserve_complete_panels(ContextPage.model_validate(previous), separate)
    assert len(result["blocks"]) == 3
    assert [block["translation"] for block in result["blocks"]] == [
        "Table of Contents",
        "How to use",
        "Battery replacement",
    ]


def test_complete_headline_does_not_recover_nearby_diagram_labels() -> None:
    """A finished headline cannot grow its mask into the illustration below."""
    from src.quality.page_grouping import OCRLine, grounded_panel

    page = ContextPage.model_validate(
        {
            "blocks": [
                {
                    "text": "さあ、おまえの罪を数えろ!",
                    "translation": "Now, count your sins!",
                    "bbox": [0.35, 0.4, 0.55, 0.04],
                }
            ]
        }
    )
    panel = Panel(id="headline", kind="prose", source_ids=[0])
    bounds, lines = grounded_panel(
        page,
        PagePlan(panels=[panel], furigana=[], notes=[]),
        panel,
        [
            OCRLine(
                id=0, text="さあ、おまえの罪を数えろ!", bbox=(0.35, 0.4, 0.55, 0.04)
            ),
            OCRLine(id=1, text="台詞ボタン", bbox=(0.65, 0.46, 0.1, 0.01)),
        ],
    )
    assert bounds[1] + bounds[3] < 0.45
    assert [line.id for line in lines] == [0]
