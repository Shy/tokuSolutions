"""Conservative display-only layout edits and cross-viewport acceptance guards."""

from collections import Counter
from typing import Literal

from PIL import Image, ImageChops, ImageDraw
from pydantic import Field

from src.quality.cleanup_context import ContextPage
from src.quality.models import Artifact


class LayoutPatch(Artifact):
    """An indexed display expansion with unchanged words."""

    block_index: int = Field(ge=0)
    bbox: list[float] = Field(min_length=4, max_length=4)
    translation: str
    explanation: str


class LayoutProposal(Artifact):
    """A bounded model proposal; empty patches are valid."""

    patches: list[LayoutPatch] = Field(max_length=12)
    observations: list[str]


class PatchDecision(Artifact):
    """A recorded deterministic acceptance or rejection."""

    block_index: int
    accepted: bool
    reason: str


class RegionMetric(Artifact):
    """A browser measurement at device scale one."""

    block_index: int
    projection_index: int | None = None
    font_px: float = Field(gt=0)
    overflow: bool
    fit_overflow: bool | None = None
    dom_overflow: bool | None = None


class ViewMetrics(Artifact):
    """One complete page measured in a target view."""

    view: Literal["desktop", "mobile", "pdf"]
    blocks: list[RegionMetric]


def _overlap(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    return max(
        0.0, min(first[0] + first[2], second[0] + second[2]) - max(first[0], second[0])
    ) * max(
        0.0, min(first[1] + first[3], second[1] + second[3]) - max(first[1], second[1])
    )


def _new_ink(
    image: Image.Image, original: tuple[float, ...], proposed: tuple[float, ...]
) -> float:
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)

    def rectangle(box: tuple[float, ...]) -> tuple[int, int, int, int]:
        return (
            round(box[0] * image.width),
            round(box[1] * image.height),
            round((box[0] + box[2]) * image.width) - 1,
            round((box[1] + box[3]) * image.height) - 1,
        )

    draw.rectangle(rectangle(proposed), fill=255)
    draw.rectangle(rectangle(original), fill=0)
    histogram = mask.histogram()
    area = histogram[255]
    if not area:
        return 0.0
    darkness = image.convert("L").point(lambda value: 255 if value < 180 else 0)
    covered = ImageChops.multiply(darkness, mask).histogram()[255]
    return covered / area


def apply_proposal(
    page: ContextPage, proposal: LayoutProposal, image: Image.Image
) -> tuple[ContextPage, list[PatchDecision]]:
    """Expand into near-white space without exposing source or changing words.

    Source page and input proposal remain unchanged. Ink protection is a
    conservative pixel heuristic, not a certified diagram segmentation map.
    """
    candidate = page.model_copy(deep=True)
    counts = Counter(patch.block_index for patch in proposal.patches)
    decisions: list[PatchDecision] = []
    for patch in proposal.patches:
        reason = ""
        if patch.block_index >= len(page.blocks) or counts[patch.block_index] != 1:
            reason = "Unknown or duplicate block identity"
        else:
            block = page.blocks[patch.block_index]
            original = block.bbox
            proposed = tuple(patch.bbox)
            if original is None:
                reason = "Missing source geometry"
            elif patch.translation.split() != block.translation.split():
                reason = "Changed words, punctuation or quantities"
            elif (
                min(proposed[:2]) < 0
                or min(proposed[2:]) <= 0
                or proposed[0] + proposed[2] > 1.000001
                or proposed[1] + proposed[3] > 1.000001
            ):
                reason = "Invalid page bounds"
            elif (
                proposed[0] > original[0] + 1e-7
                or proposed[1] > original[1] + 1e-7
                or proposed[0] + proposed[2] < original[0] + original[2] - 1e-7
                or proposed[1] + proposed[3] < original[1] + original[3] - 1e-7
            ):
                reason = "Would expose original source text"
            elif (
                proposed[2] * proposed[3] > original[2] * original[3] * 3
                or abs(proposed[0] - original[0]) > 0.04
                or abs(proposed[1] - original[1]) > 0.04
                or proposed[2] - original[2] > 0.08
                or proposed[3] - original[3] > 0.06
            ):
                reason = "Expansion exceeds bounded layout budget"
            elif any(
                neighbor.bbox
                and _overlap(proposed, neighbor.bbox)
                > _overlap(original, neighbor.bbox) + 1e-7
                for index, neighbor in enumerate(candidate.blocks)
                if index != patch.block_index
            ):
                reason = "Would increase overlap with another source/overlay"
            elif _new_ink(image, original, proposed) > 0.005:
                reason = "Would cover new scan ink (possible diagram or uncovered text)"
            else:
                candidate.blocks[patch.block_index] = block.model_copy(
                    update={"bbox": proposed, "translation": patch.translation}
                )
        decisions.append(
            PatchDecision(
                block_index=patch.block_index,
                accepted=not reason,
                reason=reason or "Words/source retained; bounded near-white expansion",
            )
        )
    return candidate, decisions


def measured_improvement(before: list[ViewMetrics], after: list[ViewMetrics]) -> bool:
    """Require complete target coverage, a measurable gain and no view regression."""

    def index(metrics: list[ViewMetrics]) -> dict[tuple[str, int], RegionMetric]:
        return {
            (view.view, block.block_index): block
            for view in metrics
            for block in view.blocks
        }

    for metrics in (before, after):
        if len(metrics) != 3 or {view.view for view in metrics} != {
            "desktop",
            "mobile",
            "pdf",
        }:
            return False
        if any(
            len({block.block_index for block in view.blocks}) != len(view.blocks)
            for view in metrics
        ):
            return False
    original = index(before)
    updated = index(after)
    if (
        {view.view for view in before} != {view.view for view in after}
        or original.keys() != updated.keys()
        or not original
    ):
        return False
    improved = False
    for identity, baseline in original.items():
        candidate = updated[identity]
        if candidate.font_px < baseline.font_px - 0.15 or (
            candidate.overflow and not baseline.overflow
        ):
            return False
        improved |= candidate.font_px >= baseline.font_px + 0.5 or (
            baseline.overflow and not candidate.overflow
        )
    return improved
