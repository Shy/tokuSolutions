"""Page-local editing context and frozen terminology for automated cleanup."""

import base64
import hashlib
import io
import re
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from openai.types.responses import ResponseInputContentParam, ResponseInputParam
from PIL import Image
from pydantic import BaseModel, JsonValue

from src.quality.glossary import Glossary, glossary_flags, resolve_glossary
from src.quality.models import Artifact, Block
from src.quality.pipeline import ManualCandidate, infer_franchise


class ManualBlockData(TypedDict):
    """The existing persisted block shape; geometry is optional in legacy tests."""

    text: str
    translation: str
    bbox: NotRequired[list[float]]
    layout: NotRequired[str]


class ManualPageData(TypedDict):
    """The persisted page shape."""

    blocks: list[ManualBlockData]
    image: NotRequired[str]
    source_blocks: NotRequired[list[ManualBlockData]]
    layout_provenance: NotRequired[dict[str, JsonValue]]


class ManualData(TypedDict):
    """Mutable published manual data, with metadata kept in the existing format."""

    meta: dict[str, JsonValue]
    pages: list[ManualPageData]


class ContextBlock(BaseModel):
    """Read published blocks without requiring changes to the viewer contract."""

    text: str
    translation: str
    bbox: tuple[float, float, float, float] | None = None
    layout: Literal["panel"] | None = None


class ContextPage(BaseModel):
    """A page image and its source blocks."""

    image: str = ""
    blocks: list[ContextBlock]
    source_blocks: list[ContextBlock] | None = None
    layout_provenance: dict[str, JsonValue] | None = None


class ContextManual(BaseModel):
    """A validated projection of legacy and current manual JSON."""

    meta: dict[str, JsonValue]
    pages: list[ContextPage]


class EditBlock(Artifact):
    """Stable original index, geometry and original Japanese beside the draft."""

    index: str
    original: str
    translation: str
    bbox: tuple[float, float, float, float] | None
    possible_ruby: bool = False


class EditBatch(Artifact):
    """Editable blocks plus read-only neighbors and page headings."""

    page: int
    image: str
    blocks: list[EditBlock]
    neighbors: list[EditBlock]
    headings: list[EditBlock]


def _possible_ruby(block: ContextBlock, others: list[ContextBlock]) -> bool:
    if not block.bbox or not re.fullmatch(r"[ぁ-ゖ\s・]+", block.text):
        return False
    left, top, width, height = block.bbox
    if height <= 0 or width <= 0:
        return False
    for other in others:
        if not other.bbox or not re.search(r"[一-龯]", other.text):
            continue
        other_left, other_top, other_width, other_height = other.bbox
        overlap = min(left + width, other_left + other_width) - max(left, other_left)
        gap = other_top - (top + height)
        if (
            height < other_height * 0.7
            and 0 <= gap < other_height
            and overlap > width * 0.7
        ):
            return True
    return False


def build_batches(value: object) -> list[EditBatch]:
    """Keep batches within a page, with 100-block and approximate text-size bounds."""
    manual = ContextManual.model_validate(value)
    batches: list[EditBatch] = []
    for page_index, page in enumerate(manual.pages):
        blocks = [
            EditBlock(
                index=f"{page_index}-{block_index}",
                original=block.text,
                translation=block.translation,
                bbox=block.bbox,
                possible_ruby=_possible_ruby(block, page.blocks),
            )
            for block_index, block in enumerate(page.blocks)
        ]
        start = 0
        while start < len(blocks):
            end = start
            characters = 0
            while end < len(blocks) and end - start < 100:
                size = len(blocks[end].original) + len(blocks[end].translation)
                if end > start and characters + size > 24_000:
                    break
                characters += size
                end += 1
            batches.append(
                EditBatch(
                    page=page_index,
                    image=page.image,
                    blocks=blocks[start:end],
                    neighbors=blocks[max(0, start - 2) : start] + blocks[end : end + 2],
                    headings=blocks[:3],
                )
            )
            start = end
    return batches


def load_constraints(glossary_path: str, value: object) -> Glossary:
    """Read the run's frozen snapshot and enforce its product/franchise context."""
    if not glossary_path:
        return Glossary(entries=[])
    path = Path(glossary_path)
    raw = path.read_bytes()
    glossary = Glossary.model_validate_json(raw)
    manual = ContextManual.model_validate(value)
    product = str(manual.meta.get("manual_name", ""))
    franchise = infer_franchise(
        product
        + "\n"
        + "\n".join(block.text for page in manual.pages for block in page.blocks)
    )
    candidate_path = path.with_name("candidate.json")
    if candidate_path.exists():
        candidate = ManualCandidate.model_validate_json(candidate_path.read_bytes())
        if hashlib.sha256(raw).hexdigest() != candidate.glossary_sha256:
            raise ValueError("Frozen cleanup glossary changed")
        product, franchise = candidate.product, candidate.franchise
    resolution = resolve_glossary(glossary, product=product, franchise=franchise)
    return Glossary(entries=list(resolution.terms.values()))


def literal_label(text: str) -> str | None:
    """Recognize short uppercase codes, switch labels, step numbers and symbols."""
    stripped = text.strip()
    if stripped and re.fullmatch(r"[A-Z0-9+−①-⑳→←↑↓\s.()/×-]{1,24}", stripped):
        return stripped
    return None


def correction_allowed(block: EditBlock, translation: str, glossary: Glossary) -> bool:
    """Reject blank edits, changed literal labels and lost accepted terminology."""
    if not translation.strip():
        return False
    label = literal_label(block.original)
    if label is not None:
        return translation.strip() == label
    # Constraints have already been resolved in their original run context.
    unscoped = glossary.model_copy(
        update={
            "entries": [
                entry.model_copy(update={"product": None, "franchise": None})
                for entry in glossary.entries
            ]
        }
    )
    source = Block(
        id=block.index,
        text=block.original,
        translation=translation,
        bbox=block.bbox or (0, 0, 1, 1),
    )
    return not glossary_flags([source], resolve_glossary(unscoped), unscoped)


def image_input(
    prompt: str, batch: EditBatch, manual_dir: Path | None, *, max_dimension: int = 2400
) -> str | ResponseInputParam:
    """Attach a bounded page image when available; safely retain text-only cleanup."""
    if manual_dir is None or not batch.image:
        return prompt
    image_path = (manual_dir / batch.image).resolve()
    if not image_path.is_relative_to(manual_dir.resolve()):
        raise ValueError("Page image must remain inside the manual directory")
    if not image_path.exists():
        return prompt

    def encoded_image(image: Image.Image) -> str:
        image.thumbnail((max_dimension, max_dimension))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()

    with Image.open(image_path) as image:
        contents: list[ResponseInputContentParam] = [
            {"type": "input_text", "text": prompt},
            {
                "type": "input_image",
                "image_url": encoded_image(image.copy()),
                "detail": "high",
            },
        ]
        boxes = [block.bbox for block in batch.blocks if block.bbox]
        if boxes:
            left = max(0.0, min(box[0] for box in boxes) - 0.02)
            top = max(0.0, min(box[1] for box in boxes) - 0.02)
            right = min(1.0, max(box[0] + box[2] for box in boxes) + 0.02)
            bottom = min(1.0, max(box[1] + box[3] for box in boxes) + 0.02)
            if 0 < (right - left) * (bottom - top) < 0.8:
                crop = image.crop(
                    (
                        int(left * image.width),
                        int(top * image.height),
                        int(right * image.width),
                        int(bottom * image.height),
                    )
                )
                contents.extend(
                    [
                        {
                            "type": "input_text",
                            "text": f"Detailed editable-section crop; normalized page edges: {left}, {top}, {right}, {bottom}. Original bboxes remain page-relative.",
                        },
                        {
                            "type": "input_image",
                            "image_url": encoded_image(crop),
                            "detail": "high",
                        },
                    ]
                )
    return [{"role": "user", "content": contents}]
