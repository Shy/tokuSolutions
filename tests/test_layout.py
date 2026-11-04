"""Layout edits preserve words, source coverage, neighbors and rendered readability."""

import pytest
from PIL import Image, ImageDraw

from src.quality.cleanup_context import ContextBlock, ContextPage
from src.quality.layout import (
    LayoutPatch,
    LayoutProposal,
    RegionMetric,
    ViewMetrics,
    apply_proposal,
    measured_improvement,
)


def sample_page() -> ContextPage:
    """Return a short instruction with spare white space and a nearby label."""
    return ContextPage(
        image="page.webp",
        blocks=[
            ContextBlock(
                text="押してください",
                translation="Press the button.",
                bbox=(0.1, 0.1, 0.2, 0.1),
            ),
            ContextBlock(text="ON", translation="ON", bbox=(0.5, 0.1, 0.1, 0.1)),
        ],
    )


def proposal(box: list[float], text: str = "Press the button.") -> LayoutProposal:
    """Build one proposed expansion."""
    return LayoutProposal(
        patches=[
            LayoutPatch(
                block_index=0,
                bbox=box,
                translation=text,
                explanation="Improve wrapping",
            )
        ],
        observations=[],
    )


def test_expansion_retains_original_and_does_not_mutate_input() -> None:
    """Only display geometry/whitespace may change on the returned copy."""
    original = sample_page()
    updated, decisions = apply_proposal(
        original,
        proposal([0.1, 0.1, 0.27, 0.1], "Press the\nbutton."),
        Image.new("RGB", (1000, 1000), "white"),
    )
    assert decisions[0].accepted
    assert original.blocks[0].bbox == (0.1, 0.1, 0.2, 0.1)
    assert updated.blocks[0].text == original.blocks[0].text
    assert updated.blocks[0].translation == "Press the\nbutton."


@pytest.mark.parametrize(
    ("box", "text"),
    [
        ([0.15, 0.1, 0.2, 0.1], "Press the button."),
        ([0.1, 0.1, 0.6, 0.1], "Press the button."),
        ([0.1, 0.1, 0.27, 0.1], "Do not press the button."),
        ([0.1, 0.1, 0.2, 0.8], "Press the button."),
        ([-0.01, 0.1, 0.27, 0.1], "Press the button."),
    ],
)
def test_unsafe_changes_retain_the_baseline(box: list[float], text: str) -> None:
    """Reject exposed source, collisions, altered meaning, excessive growth and bounds."""
    original = sample_page()
    updated, decisions = apply_proposal(
        original, proposal(box, text), Image.new("RGB", (1000, 1000), "white")
    )
    assert not decisions[0].accepted
    assert updated == original


def test_expansion_cannot_cover_new_diagram_ink() -> None:
    """Whitespace expansion must not erase a neighboring line drawing."""
    image = Image.new("RGB", (1000, 1000), "white")
    ImageDraw.Draw(image).rectangle((310, 100, 390, 200), fill="black")
    updated, decisions = apply_proposal(
        sample_page(), proposal([0.1, 0.1, 0.27, 0.1]), image
    )
    assert not decisions[0].accepted
    assert "ink" in decisions[0].reason
    assert updated == sample_page()


def test_duplicate_or_unknown_indices_are_rejected() -> None:
    """Ambiguous identity must not silently select one edit."""
    patch = proposal([0.1, 0.1, 0.27, 0.1]).patches[0]
    request = LayoutProposal(
        patches=[patch, patch, patch.model_copy(update={"block_index": 9})],
        observations=[],
    )
    updated, decisions = apply_proposal(
        sample_page(), request, Image.new("RGB", (1000, 1000), "white")
    )
    assert updated == sample_page()
    assert not any(decision.accepted for decision in decisions)


def metrics(font: float, overflow: bool = False) -> list[ViewMetrics]:
    """Use the same stable region identity in every target view."""
    return [
        ViewMetrics(
            view=view,
            blocks=[RegionMetric(block_index=0, font_px=font, overflow=overflow)],
        )
        for view in ("desktop", "mobile", "print")
    ]


def test_measurement_requires_gain_without_regressions_in_any_view() -> None:
    """Improvement in one viewport cannot hide a new failure in another."""
    assert measured_improvement(metrics(8), metrics(10))
    assert measured_improvement(metrics(8, True), metrics(8))
    assert not measured_improvement(metrics(8), metrics(8))
    after = metrics(10)
    after[1].blocks[0].overflow = True
    assert not measured_improvement(metrics(8), after)
    after = metrics(10)
    after[2].blocks[0].font_px = 7
    assert not measured_improvement(metrics(8), after)
    assert not measured_improvement(metrics(8), metrics(10)[:1])


def test_neighbor_collision_is_rejected_within_expansion_budget() -> None:
    """A modest expansion still cannot cover another overlay."""
    page = sample_page()
    page.blocks[1].bbox = (0.32, 0.1, 0.1, 0.1)
    updated, decisions = apply_proposal(
        page, proposal([0.1, 0.1, 0.27, 0.1]), Image.new("RGB", (1000, 1000), "white")
    )
    assert not decisions[0].accepted
    assert "overlap" in decisions[0].reason
    assert updated == page


def test_measurement_cannot_omit_a_target_or_duplicate_region_identity() -> None:
    """Partial/tampered evidence cannot pass as a complete rendered comparison."""
    assert not measured_improvement(metrics(8)[:1], metrics(10)[:1])
    duplicate = metrics(10)
    duplicate[0].blocks.append(duplicate[0].blocks[0])
    assert not measured_improvement(metrics(8), duplicate)
