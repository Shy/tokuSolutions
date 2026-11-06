"""Structural guidance preserves OCR text while separating independent items."""

from copy import deepcopy
from pathlib import Path

from src.layout_models import LayoutCell, LayoutPage, LayoutRegion, LayoutTable
from src.quality.cleanup_context import ContextPage
from src.quality.page_grouping import PagePlan, Panel
from src.quality.structure_evidence import build_evidence, constrain_plan


def source_page() -> ContextPage:
    """Two OCR fragments followed by a separate instruction."""
    return ContextPage.model_validate(
        {
            "blocks": [
                {
                    "text": "ボタンを",
                    "translation": "Press",
                    "bbox": [0.1, 0.1, 0.2, 0.02],
                },
                {
                    "text": "押します。",
                    "translation": "the button.",
                    "bbox": [0.1, 0.13, 0.2, 0.02],
                },
                {
                    "text": "変身！",
                    "translation": "Transform!",
                    "bbox": [0.1, 0.2, 0.2, 0.02],
                },
            ]
        }
    )


def test_structure_groups_fragments_but_separates_independent_list_items() -> None:
    """Membership guides grouping without importing Docling's misrecognized text."""
    page = source_page()
    before = deepcopy(page)
    layout = LayoutPage(
        page=1,
        provider="docling",
        regions=[
            LayoutRegion(
                id="a",
                kind="list_item",
                text="WRONG OCR",
                bbox=(0.09, 0.09, 0.23, 0.07),
            ),
            LayoutRegion(id="b", kind="list_item", bbox=(0.09, 0.19, 0.23, 0.04)),
        ],
    )
    evidence = build_evidence(page, [layout])
    plan = PagePlan(
        panels=[Panel(id="joined", kind="prose", source_ids=[0, 1, 2])],
        furigana=[],
        notes=[],
    )
    checked = constrain_plan(plan, evidence)
    assert [panel.source_ids for panel in checked.panels] == [[0, 1], [2]]
    assert "WRONG OCR" not in evidence.model_dump_json()
    assert page == before


def test_table_cells_are_separate_even_inside_a_shared_table_region() -> None:
    """A table's enclosing box must not invite merging its contents."""
    page = source_page()
    layout = LayoutPage(
        page=1,
        provider="docling",
        tables=[
            LayoutTable(
                id="table",
                bbox=(0.09, 0.09, 0.23, 0.15),
                rows=2,
                columns=1,
                cells=[
                    LayoutCell(row=0, column=0, bbox=(0.09, 0.09, 0.23, 0.07)),
                    LayoutCell(row=1, column=0, bbox=(0.09, 0.19, 0.23, 0.04)),
                ],
            )
        ],
    )
    evidence = build_evidence(page, [layout])
    plan = PagePlan(
        panels=[Panel(id="table", kind="prose", source_ids=[0, 1, 2])],
        furigana=[],
        notes=[],
    )
    assert [panel.source_ids for panel in constrain_plan(plan, evidence).panels] == [
        [0, 1],
        [2],
    ]


def test_missing_or_ambiguous_structure_never_removes_ocr_sources() -> None:
    """Overlapping hypotheses remain advisory and retain full source membership."""
    page = source_page()
    layout = LayoutPage(
        page=1,
        provider="docling",
        regions=[
            LayoutRegion(id="a", kind="list_item", bbox=(0.09, 0.09, 0.23, 0.07)),
            LayoutRegion(id="b", kind="list_item", bbox=(0.09, 0.09, 0.23, 0.07)),
        ],
    )
    evidence = build_evidence(page, [layout])
    plan = PagePlan(
        panels=[Panel(id="p", kind="prose", source_ids=[0, 1, 2])],
        furigana=[],
        notes=[],
    )
    assert constrain_plan(plan, evidence).panels == plan.panels
    assert evidence.unmatched_source_ids == [2]
    assert evidence.ambiguous_source_ids == [0, 1]


def test_disagreeing_layout_variants_do_not_force_a_boundary() -> None:
    """Sparse/default disagreement is a recorded hypothesis, not a deletion rule."""
    page = source_page()
    first = LayoutPage(
        page=1,
        provider="default",
        regions=[
            LayoutRegion(id="a", kind="list_item", bbox=(0.09, 0.09, 0.23, 0.07)),
            LayoutRegion(id="b", kind="list_item", bbox=(0.09, 0.19, 0.23, 0.04)),
        ],
    )
    second = LayoutPage(
        page=1,
        provider="sparse",
        regions=[
            LayoutRegion(id="whole", kind="text", bbox=(0.09, 0.09, 0.23, 0.15)),
        ],
    )
    plan = PagePlan(
        panels=[Panel(id="p", kind="prose", source_ids=[0, 1, 2])],
        furigana=[],
        notes=[],
    )
    evidence = build_evidence(page, [first, second])
    assert constrain_plan(plan, evidence).panels == plan.panels
    assert evidence.conflicts


def test_structure_preparation_rejects_an_unrelated_source_pdf(tmp_path: Path) -> None:
    """Cached layouts from another scan cannot guide the current document."""
    import json

    import pytest

    from src.activities.semantic_layout import _prepare_structure

    source = tmp_path / "source.pdf"
    source.write_bytes(b"actual pdf input")
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"source_sha256": "wrong", "pages": []}))
    with pytest.raises(ValueError, match="source PDF"):
        _prepare_structure(
            str(tmp_path / "manual.json"),
            str(source),
            str(index),
            str(tmp_path / "run"),
        )


def test_printed_continuation_can_cross_list_item_hypotheses() -> None:
    """A while-pressing fragment may complete in the next numbered item."""
    page = source_page()
    page.blocks[0].text = "ボタンを押しながら、"
    layout = LayoutPage(
        page=1,
        provider="docling",
        regions=[
            LayoutRegion(id="a", kind="list_item", bbox=(0.09, 0.09, 0.23, 0.035)),
            LayoutRegion(id="b", kind="list_item", bbox=(0.09, 0.125, 0.23, 0.035)),
        ],
    )
    plan = PagePlan(
        panels=[Panel(id="p", kind="prose", source_ids=[0, 1])], furigana=[], notes=[]
    )
    assert constrain_plan(plan, build_evidence(page, [layout])).panels == plan.panels


def test_previous_panel_preservation_cannot_restore_a_cross_cell_merge() -> None:
    """A post-processing fallback must respect the same structural boundaries."""
    from src.quality.page_grouping import PanelText, PanelTranslations
    from src.quality.semantic_layout import assemble_page, preserve_complete_panels

    page = source_page()
    old_plan = PagePlan(
        panels=[Panel(id="old", kind="prose", source_ids=[0, 1, 2])],
        furigana=[],
        notes=[],
    )
    old = assemble_page(
        page,
        old_plan,
        PanelTranslations(
            panels=[
                PanelText(
                    panel_id="old",
                    japanese_items=["ボタンを押します。変身！"],
                    english_items=["Press the button. Transform!"],
                )
            ],
            uncertainties=[],
        ),
    )
    separate = assemble_page(
        page,
        PagePlan(panels=[], furigana=[], notes=[]),
        PanelTranslations(panels=[], uncertainties=[]),
    )
    layout = LayoutPage(
        page=1,
        provider="docling",
        regions=[
            LayoutRegion(id="a", kind="list_item", bbox=(0.09, 0.09, 0.23, 0.07)),
            LayoutRegion(id="b", kind="list_item", bbox=(0.09, 0.19, 0.23, 0.04)),
        ],
    )
    result = preserve_complete_panels(
        ContextPage.model_validate(old), separate, build_evidence(page, [layout])
    )
    assert len(result["blocks"]) == 3


def test_planning_request_contains_structure_and_original_ocr_geometry(
    tmp_path: Path,
) -> None:
    """The actual request accepts tuple geometry and excludes extractor wording."""
    import json

    from PIL import Image

    from src.activities.semantic_layout import _prepare_page

    page = source_page()
    page.image = "scan.jpg"
    source = tmp_path / "translations.json"
    source.write_text(json.dumps({"meta": {}, "pages": [page.model_dump(mode="json")]}))
    Image.new("RGB", (500, 700), "white").save(tmp_path / "scan.jpg")
    output = tmp_path / "prepared"
    output.mkdir()
    (output / "lines.json").write_text("[]")
    glossary = tmp_path / "terms.json"
    glossary.write_text('{"entries": []}')
    layout = LayoutPage(
        page=1,
        provider="docling",
        regions=[
            LayoutRegion(
                id="a", kind="text", text="WRONG OCR", bbox=(0.09, 0.09, 0.23, 0.07)
            )
        ],
    )
    evidence = tmp_path / "structure.json"
    evidence.write_text(build_evidence(page, [layout]).model_dump_json())
    request_path = _prepare_page(
        str(source), 0, str(output), "", str(glossary), str(evidence)
    )
    request = json.loads(Path(request_path).read_bytes())
    payload = json.loads(request["input_items"][0]["content"][0]["text"])
    assert payload["source_regions"][0]["bbox"] == [0.1, 0.1, 0.2, 0.02]
    assert payload["source_regions"][0]["text"] == "ボタンを"
    assert payload["structural_evidence"]["variants"][0]["regions"][0][
        "source_ids"
    ] == [0, 1]
    image_record = json.loads((output / "source-image.json").read_bytes())
    assert Path(image_record["path"]).parent == output
    assert Path(image_record["path"]).is_file()
    assert "WRONG OCR" not in request_path and "WRONG OCR" not in json.dumps(payload)


def test_semantic_cli_requires_one_manual_for_one_layout_index(tmp_path: Path) -> None:
    """One document's geometry must not be accidentally applied across the corpus."""
    from click.testing import CliRunner

    from src.quality.semantic_repair import main

    index = tmp_path / "index.json"
    index.write_text("{}")
    result = CliRunner().invoke(
        main, ["--run-id", "test", "--layout-index", str(index)]
    )
    assert "exactly one --manual" in result.output


def test_publication_rejects_restoring_a_structurally_invalid_panel() -> None:
    """The final ledger check also enforces supported independent-unit boundaries."""
    import pytest

    from src.quality.page_grouping import PanelText, PanelTranslations
    from src.quality.semantic_layout import assemble_page, validate_grouped_page

    page = source_page()
    plan = PagePlan(
        panels=[Panel(id="old", kind="prose", source_ids=[0, 1, 2])],
        furigana=[],
        notes=[],
    )
    grouped = assemble_page(
        page,
        plan,
        PanelTranslations(
            panels=[
                PanelText(
                    panel_id="old",
                    japanese_items=["ボタンを押します。変身！"],
                    english_items=["Press the button. Transform!"],
                )
            ],
            uncertainties=[],
        ),
    )
    layout = LayoutPage(
        page=1,
        provider="docling",
        regions=[
            LayoutRegion(id="a", kind="list_item", bbox=(0.09, 0.09, 0.23, 0.07)),
            LayoutRegion(id="b", kind="list_item", bbox=(0.09, 0.19, 0.23, 0.04)),
        ],
    )
    grouped["layout_provenance"]["structure"] = build_evidence(
        page, [layout]
    ).model_dump(mode="json")
    with pytest.raises(ValueError, match="structural"):
        validate_grouped_page(page, ContextPage.model_validate(grouped))


def test_a_diagram_label_stays_separate_from_prose_outside_the_picture() -> None:
    """Picture membership supplies boundaries even when local OCR missed the label."""
    page = source_page()
    layout = LayoutPage(
        page=1,
        provider="docling",
        regions=[
            LayoutRegion(id="diagram", kind="picture", bbox=(0.09, 0.09, 0.23, 0.07)),
            LayoutRegion(id="body", kind="text", bbox=(0.09, 0.19, 0.23, 0.04)),
        ],
    )
    plan = PagePlan(
        panels=[Panel(id="p", kind="prose", source_ids=[0, 1, 2])],
        furigana=[],
        notes=[],
    )
    assert [
        panel.source_ids
        for panel in constrain_plan(plan, build_evidence(page, [layout])).panels
    ] == [[0, 1], [2]]


def test_google_cell_text_extents_separate_items_without_becoming_layout_limits() -> (
    None
):
    """Semantic cell membership must not force English into Japanese text-sized boxes."""
    page = source_page()
    layout = LayoutPage(
        page=1,
        provider="google-layout-v1.0",
        tables=[
            LayoutTable(
                id="t",
                bbox=(0.09, 0.09, 0.3, 0.2),
                rows=2,
                columns=1,
                cells=[
                    LayoutCell(row=0, column=0, bbox=(0.09, 0.09, 0.23, 0.07)),
                    LayoutCell(row=1, column=0, bbox=(0.09, 0.19, 0.23, 0.04)),
                ],
            )
        ],
    )
    evidence = build_evidence(page, [layout])
    assert {region.kind for region in evidence.variants[0].regions} == {"cell_text"}
    plan = PagePlan(
        panels=[Panel(id="p", kind="prose", source_ids=[0, 1, 2])],
        furigana=[],
        notes=[],
    )
    assert [panel.source_ids for panel in constrain_plan(plan, evidence).panels] == [
        [0, 1],
        [2],
    ]


def test_final_structural_split_falls_back_when_original_ocr_boxes_overlap(
    tmp_path: Path,
) -> None:
    """A separated label inside a broad OCR body must not fail the entire manual."""
    import json

    from PIL import Image

    from src.activities.semantic_layout import _finish_page
    from src.quality.page_grouping import PanelText, PanelTranslations
    from src.quality.semantic_layout import validate_grouped_page

    page = ContextPage.model_validate(
        {
            "image": "scan.jpg",
            "blocks": [
                {
                    "text": "ボタン",
                    "translation": "Button",
                    "bbox": [0.5, 0.1, 0.05, 0.02],
                },
                {
                    "text": "説明です。",
                    "translation": "Instructions.",
                    "bbox": [0.1, 0.1, 0.8, 0.1],
                },
            ],
        }
    )
    evidence = build_evidence(
        page,
        [
            LayoutPage(
                page=1,
                provider="docling",
                regions=[
                    LayoutRegion(
                        id="picture", kind="picture", bbox=(0.49, 0.09, 0.08, 0.04)
                    )
                ],
            )
        ],
    )
    plan = constrain_plan(
        PagePlan(
            panels=[Panel(id="p", kind="prose", source_ids=[0, 1])],
            furigana=[],
            notes=[],
        ),
        evidence,
    )
    (tmp_path / "source-page.json").write_text(page.model_dump_json())
    (tmp_path / "plan.json").write_text(plan.model_dump_json())
    (tmp_path / "structure.json").write_text(evidence.model_dump_json())
    (tmp_path / "lines.json").write_text("[]")
    Image.new("RGB", (1000, 1000), "white").save(tmp_path / "scan.jpg")
    for panel in plan.panels:
        block = page.blocks[panel.source_ids[0]]
        translated = PanelTranslations(
            panels=[
                PanelText(
                    panel_id=panel.id,
                    japanese_items=[block.text],
                    english_items=[block.translation],
                )
            ],
            uncertainties=[],
        )
        (tmp_path / f"panel-{panel.id}.validated.json").write_text(
            translated.model_dump_json()
        )
    result = json.loads(Path(_finish_page(str(tmp_path))).read_bytes())
    validate_grouped_page(page, ContextPage.model_validate(result))
    assert len(result["source_blocks"]) == 2
    assert sorted(block["translation"] for block in result["blocks"]) == [
        "Button",
        "Instructions.",
    ]


def test_geometry_fallback_cannot_reabsorb_an_independent_dropped_unit() -> None:
    """Once structure splits rows, omission recovery cannot join them again."""
    from src.quality.semantic_layout import safe_plan
    from src.quality.structure_evidence import (
        StructuralRegion,
        StructuralVariant,
        StructureEvidence,
    )

    page = ContextPage.model_validate(
        {
            "blocks": [
                {
                    "text": "最初の説明です。",
                    "translation": "First instruction.",
                    "bbox": [0.1, 0.1, 0.4, 0.02],
                },
                {
                    "text": "次の説明です。",
                    "translation": "Next instruction.",
                    "bbox": [0.1, 0.13, 0.4, 0.02],
                },
                {
                    "text": "ボタン",
                    "translation": "Button",
                    "bbox": [0.2, 0.13, 0.03, 0.02],
                },
            ]
        }
    )
    evidence = StructureEvidence(
        variants=[
            StructuralVariant(
                provider="google-layout",
                assignments={0: "first", 1: "second", 2: "label"},
                regions=[
                    StructuralRegion(
                        id=identity,
                        kind="cell_text",
                        bbox=page.blocks[index].bbox,
                        source_ids=[index],
                    )
                    for index, identity in enumerate(("first", "second", "label"))
                ],
            )
        ],
        unmatched_source_ids=[],
        ambiguous_source_ids=[],
    )
    plan = PagePlan(
        panels=[
            Panel(id="first", kind="prose", source_ids=[0]),
            Panel(id="second", kind="prose", source_ids=[1]),
            Panel(id="label", kind="heading", source_ids=[2]),
        ],
        furigana=[],
        notes=[],
    )
    checked = safe_plan(page, constrain_plan(plan, evidence), recover_omissions=False)
    assert [panel.source_ids for panel in checked.panels] == [[0], [2]]
    assert constrain_plan(checked, evidence).panels == checked.panels
    assert page.blocks[1].translation == "Next instruction."
