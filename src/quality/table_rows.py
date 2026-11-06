"""Project translated entries into image-confirmed table cells, retaining source groups."""

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import TypedDict, cast

from PIL import Image, ImageStat
from pydantic import TypeAdapter

from src.quality.cleanup_context import ContextPage, ManualPageData
from src.quality.page_grouping import OCRLine, SourceMapping


class DisplayRow(TypedDict):
    """An English entry tied to OCR lines and a printed cell."""

    text: str
    translation: str
    bbox: list[float]
    line_ids: list[int]


class RowProjection(TypedDict):
    """A projection is valid only while the original translation remains unchanged."""

    translation: str
    rows: list[DisplayRow]


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text))


def _cell(
    image: Image.Image, bounds: tuple[float, float, float, float]
) -> list[float] | None:
    """Find the four enclosing printed rules, on light or dark table backgrounds."""
    width, height = image.size
    left, top, span, tall = bounds
    x0 = max(0, round(left * width) - 5)
    x1 = min(width, round((left + span) * width) + 5)
    y0 = max(0, round((top - 0.08) * height))
    y1 = min(height, round((top + tall + 0.08) * height))
    if x1 <= x0 or y1 <= y0:
        return None
    sample = image.crop(
        (
            x0,
            max(0, round(top * height) - 3),
            x1,
            min(height, max(round((top + tall) * height) + 3, round(top * height) + 1)),
        )
    )
    dark = ImageStat.Stat(sample).median[0] < 128
    binary = image.point(lambda value: int(value > 200) if dark else int(value < 160))
    strip = binary.crop((x0, y0, x1, y1))
    pixels = strip.tobytes()
    rules = [
        y0 + row
        for row in range(strip.height)
        if sum(pixels[row * strip.width : (row + 1) * strip.width]) / strip.width > 0.94
    ]
    above = [row for row in rules if row < top * height - 3]
    below = [row for row in rules if row > (top + tall) * height + 3]
    if not above or not below:
        return None
    upper, lower = max(above) + 2, min(below) - 1
    if lower - upper > max(tall * height * 12, height * 0.04) or lower <= upper:
        return None
    vertical = binary.crop((0, upper, width, lower))
    pixels = vertical.tobytes()
    columns = [
        column
        for column in range(width)
        if sum(pixels[column::width]) / vertical.height > 0.94
    ]
    before = [column for column in columns if column < left * width - 3]
    after = [column for column in columns if column > (left + span) * width + 3]
    if not before or not after:
        return None
    start, end = max(before) + 2, min(after) - 1
    if end <= start or lower - upper < tall * height + 2:
        return None
    return [
        start / width,
        upper / height,
        (end - start) / width,
        (lower - upper) / height,
    ]


def project_table_rows(
    page: ManualPageData, image_path: Path
) -> dict[str, RowProjection]:
    """Separate paired entries only when each can be grounded in a ruled scan cell."""
    ledger = page.get("layout_provenance", {})
    lines = TypeAdapter(list[OCRLine]).validate_python(ledger.get("ocr_lines", []))
    mappings = TypeAdapter(list[SourceMapping]).validate_python(
        ledger.get("mapping", [])
    )
    by_index = {row["display_index"]: row for row in mappings}
    result: dict[str, RowProjection] = {}
    structure = cast(dict[str, object], ledger.get("structure", {}))
    pictures = [
        region["bbox"]
        for variant in cast(list[dict[str, object]], structure.get("variants", []))
        for region in cast(list[dict[str, object]], variant.get("regions", []))
        if region["kind"] == "picture"
        and cast(list[float], region["bbox"])[2] * cast(list[float], region["bbox"])[3]
        < 0.65
    ]
    with Image.open(image_path) as source:
        image = source.convert("L")
    cell_cache: dict[int, list[float] | None] = {}
    for index, block in enumerate(page["blocks"]):
        english = [
            re.sub(r"^\s*[•●]\s*", "", item).strip()
            for item in block["translation"].splitlines()
            if item.strip()
        ]
        mapping = by_index.get(index)
        japanese = (
            mapping.get("japanese_items", block["text"].splitlines())
            if mapping
            else block["text"].splitlines()
        )
        if not english or "bbox" not in block:
            continue
        bounds = block["bbox"]
        candidates = [
            line
            for line in lines
            if bounds[0] - 0.003
            <= line.bbox[0] + line.bbox[2] / 2
            <= bounds[0] + bounds[2] + 0.003
            and bounds[1] - 0.003
            <= line.bbox[1] + line.bbox[3] / 2
            <= bounds[1] + bounds[3] + 0.003
        ]
        cells: dict[tuple[float, ...], list[OCRLine]] = {}
        for line in candidates:
            if line.id not in cell_cache:
                cell_cache[line.id] = _cell(image, line.bbox)
            cell = cell_cache[line.id]
            if cell:
                cells.setdefault(tuple(cell), []).append(line)
        if len(cells) == 1:
            key, cell_lines = next(iter(cells.items()))
            # One complete printed entry may use its whole empty cell, but never
            # cover a second independently translated label in the same box.
            neighbors = [
                other
                for other_index, other in enumerate(page["blocks"])
                if other_index != index
                and "bbox" in other
                and key[0] < other["bbox"][0] + other["bbox"][2]
                and other["bbox"][0] < key[0] + key[2]
                and key[1] < other["bbox"][1] + other["bbox"][3]
                and other["bbox"][1] < key[1] + key[3]
            ]
            similarity = SequenceMatcher(
                None,
                _compact(block["text"]),
                _compact("".join(line.text for line in cell_lines)),
            ).ratio()
            crosses_picture = any(
                key[0] < picture[0] + picture[2]
                and picture[0] < key[0] + key[2]
                and key[1] < picture[1] + picture[3]
                and picture[1] < key[1] + key[3]
                for picture in cast(list[list[float]], pictures)
            )
            if (
                not neighbors
                and not crosses_picture
                and similarity >= 0.8
                and key[2] >= bounds[2] * 1.3
            ):
                result[str(index)] = {
                    "translation": block["translation"],
                    "rows": [
                        {
                            "text": block["text"],
                            "translation": " ".join(english),
                            "bbox": list(key),
                            "line_ids": [line.id for line in cell_lines],
                        }
                    ],
                }
                continue
        if len(english) < 2 or len(english) != len(japanese):
            continue
        if len(cells) < 2:
            continue
        assigned: dict[tuple[float, ...], list[tuple[str, str]]] = {}
        for original, translation in zip(japanese, english, strict=True):
            key, score = max(
                (
                    (
                        key,
                        SequenceMatcher(
                            None,
                            _compact(original),
                            _compact(
                                "".join(
                                    line.text
                                    for line in sorted(
                                        items, key=lambda line: line.bbox[1]
                                    )
                                )
                            ),
                        ).ratio(),
                    )
                    for key, items in cells.items()
                ),
                key=lambda item: item[1],
            )
            if score < 0.58:
                break
            assigned.setdefault(key, []).append((original, translation))
        else:
            if len(assigned) < 2:
                continue
            keys = list(assigned)
            if (
                max(key[0] for key in keys) - min(key[0] for key in keys) > 0.005
                or max(key[0] + key[2] for key in keys)
                - min(key[0] + key[2] for key in keys)
                > 0.005
            ):
                continue
            if any(
                first[1] < second[1] + second[3] and first[1] + first[3] > second[1]
                for offset, first in enumerate(keys)
                for second in keys[offset + 1 :]
            ):
                continue
            result[str(index)] = {
                "translation": block["translation"],
                "rows": [
                    {
                        "text": "\n".join(pair[0] for pair in items),
                        "translation": "\n".join(pair[1] for pair in items),
                        "bbox": list(key),
                        "line_ids": [line.id for line in cells[key]],
                    }
                    for key, items in sorted(
                        assigned.items(), key=lambda item: (item[0][1], item[0][0])
                    )
                ],
            }
    return result


def projected_block_count(page: "ContextPage") -> int:
    """Count visible entries without changing the original source/display ledger."""
    return len(projected_block_indices(page))


def projected_block_indices(page: "ContextPage") -> list[int]:
    """Retain each rendered cell's editor parent, including repeated parent IDs."""
    projections = TypeAdapter(dict[str, RowProjection]).validate_python(
        (page.layout_provenance or {}).get("row_layout", {})
    )
    return [
        index
        for index, block in enumerate(page.blocks)
        for _ in range(
            len(projections[str(index)]["rows"])
            if str(index) in projections
            and projections[str(index)]["translation"] == block.translation
            and projections[str(index)]["rows"]
            else 1
        )
    ]
