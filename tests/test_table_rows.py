"""Use printed table cells without treating prose line breaks as independent entries."""

from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import TypeAdapter

from src.quality.cleanup_context import ManualPageData
from src.quality.table_rows import project_table_rows


def test_grid_cells_separate_entries_even_inside_one_ocr_region(tmp_path: Path) -> None:
    """Original source groups remain intact while display rows follow the grid."""
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    for y in (200, 300, 400):
        draw.line((100, y, 700, y), fill="black", width=3)
    for x in (100, 700):
        draw.line((x, 200, x, 400), fill="black", width=3)
    path = tmp_path / "table.png"
    image.save(path)
    page = {
        "blocks": [
            {
                "text": "変身!\n行くぜ!",
                "translation": "• Transform!\n• Here we go!",
                "bbox": [0.12, 0.23, 0.3, 0.13],
            }
        ],
        "layout_provenance": {
            "ocr_lines": [
                {"id": 0, "text": "変身!", "bbox": [0.12, 0.23, 0.1, 0.02]},
                {"id": 1, "text": "行くぜ!", "bbox": [0.12, 0.33, 0.1, 0.02]},
            ],
            "mapping": [
                {
                    "display_index": 0,
                    "source_ids": [0],
                    "furigana_ids": [],
                    "japanese_items": ["変身!", "行くぜ!"],
                }
            ],
        },
    }
    validated = TypeAdapter(ManualPageData).validate_python(page)
    rows = project_table_rows(validated, path)
    assert len(rows["0"]["rows"]) == 2
    assert [row["translation"] for row in rows["0"]["rows"]] == [
        "Transform!",
        "Here we go!",
    ]
    assert (
        rows["0"]["rows"][0]["bbox"][1] + rows["0"]["rows"][0]["bbox"][3]
        < rows["0"]["rows"][1]["bbox"][1]
    )
    Image.new("RGB", (1000, 1000), "white").save(path)
    assert project_table_rows(validated, path) == {}


def test_one_complete_entry_can_use_its_printed_cell_without_eating_neighbors(
    tmp_path: Path,
) -> None:
    """English can fill a verified row rather than the narrow Japanese glyph box."""
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 200, 700, 260), outline="black", width=3)
    path = tmp_path / "cell.png"
    image.save(path)
    page = TypeAdapter(ManualPageData).validate_python(
        {
            "blocks": [
                {
                    "text": "行くぜ。フィリップ。ああ。",
                    "translation": "• Let's go.\n• Philip.\n• Yeah.",
                    "bbox": [0.12, 0.22, 0.15, 0.02],
                }
            ],
            "layout_provenance": {
                "ocr_lines": [
                    {
                        "id": 0,
                        "text": "行くぜ。フィリップ。ああ。",
                        "bbox": [0.12, 0.22, 0.15, 0.02],
                    }
                ],
                "mapping": [],
            },
        }
    )
    result = project_table_rows(page, path)
    assert len(result["0"]["rows"]) == 1
    assert result["0"]["rows"][0]["translation"] == "Let's go. Philip. Yeah."
    assert result["0"]["rows"][0]["bbox"][2] > 0.5
    page["blocks"].append(
        {
            "text": "別のラベル",
            "translation": "Other label",
            "bbox": [0.4, 0.22, 0.1, 0.02],
        }
    )
    assert "0" not in project_table_rows(page, path)


def test_single_cell_candidate_cannot_expand_over_a_detected_diagram(
    tmp_path: Path,
) -> None:
    """An enclosing diagram outline is not permission to turn its label into a row."""
    image = Image.new("RGB", (1000, 1000), "white")
    ImageDraw.Draw(image).rectangle((100, 200, 700, 260), outline="black", width=3)
    path = tmp_path / "diagram.png"
    image.save(path)
    page = TypeAdapter(ManualPageData).validate_python(
        {
            "blocks": [
                {
                    "text": "リセット",
                    "translation": "Reset",
                    "bbox": [0.12, 0.22, 0.15, 0.02],
                }
            ],
            "layout_provenance": {
                "ocr_lines": [
                    {"id": 0, "text": "リセット", "bbox": [0.12, 0.22, 0.15, 0.02]}
                ],
                "mapping": [],
                "structure": {
                    "variants": [
                        {
                            "regions": [
                                {"kind": "picture", "bbox": [0.1, 0.2, 0.6, 0.06]}
                            ]
                        }
                    ]
                },
            },
        }
    )
    assert project_table_rows(page, path) == {}
