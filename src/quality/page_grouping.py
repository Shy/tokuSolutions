"""Source-linked semantic panels; confirmed ruby is retained only in provenance."""

import re
import unicodedata
from collections import Counter
from typing import Literal, NotRequired, TypedDict

from pydantic import Field

from src.quality.cleanup_context import ContextPage
from src.quality.models import Artifact


class Panel(Artifact):
    """A visual text unit; source IDs are in reading order, never model coordinates."""

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,100}$")
    kind: Literal["heading", "prose"]
    source_ids: list[int] = Field(min_length=1)


class RubyLink(Artifact):
    """A confirmed reading guide attached to a retained source region."""

    source_id: int
    parent_id: int


class PagePlan(Artifact):
    """Unmentioned source regions pass through unchanged; none silently disappear."""

    panels: list[Panel]
    furigana: list[RubyLink]
    notes: list[str]


class PanelText(Artifact):
    """One-to-one Japanese/English paragraphs or bullets reconstructed from the scan."""

    panel_id: str
    japanese_items: list[str] = Field(min_length=1)
    english_items: list[str] = Field(min_length=1)


class PanelTranslations(Artifact):
    """Complete panel text, without standalone furigana translations."""

    panels: list[PanelText]
    uncertainties: list[str]


def _box(page: ContextPage, identifier: int) -> tuple[float, float, float, float]:
    if not 0 <= identifier < len(page.blocks) or page.blocks[identifier].bbox is None:
        raise ValueError("Unknown source region or missing geometry")
    bounds = page.blocks[identifier].bbox
    assert bounds is not None
    return bounds


def panel_box(
    page: ContextPage, panel: Panel, plan: PagePlan
) -> tuple[float, float, float, float]:
    """Derive the mask from source geometry, including discarded ruby's pixels."""
    identities = panel.source_ids + [
        ruby.source_id for ruby in plan.furigana if ruby.parent_id in panel.source_ids
    ]
    boxes = [_box(page, identity) for identity in identities]
    left = max(0, min(bounds[0] for bounds in boxes) - 0.0015)
    top = max(0, min(bounds[1] for bounds in boxes) - 0.0015)
    right = min(1, max(bounds[0] + bounds[2] for bounds in boxes) + 0.0015)
    bottom = min(1, max(bounds[1] + bounds[3] for bounds in boxes) + 0.0015)
    return left, top, right - left, bottom - top


def eligible_ruby_parents(page: ContextPage, identifier: int) -> list[int]:
    """Find possible bases for small hiragana, katakana or mixed reading guides.

    This supplies candidates, not an automatic deletion decision. The planner
    must still confirm that the kana pronounces the base rather than being a label.
    """
    guide = page.blocks[identifier]
    reading = unicodedata.normalize("NFKC", guide.text.strip())
    if (
        not guide.bbox
        or not re.fullmatch(r"[ぁ-ゖァ-ヺ\s・ー]+", reading)
        or not re.search(r"[ぁ-ゖァ-ヺ]", reading)
    ):
        return []
    small = guide.bbox
    candidates = []
    for parent_id, parent in enumerate(page.blocks):
        if parent_id == identifier or not parent.bbox:
            continue
        base_text = unicodedata.normalize("NFKC", parent.text)
        if not re.search(
            r"[一-龯]|(?<![A-Za-z0-9])[A-Z]{2,6}(?![A-Za-z0-9])", base_text
        ):
            continue
        large = parent.bbox
        overlap = min(small[0] + small[2], large[0] + large[2]) - max(
            small[0], large[0]
        )
        if (
            small[3] <= large[3] * 0.75
            and overlap > 0
            and small[1] <= large[1] + large[3]
            and small[1] + small[3] >= large[1] - large[3] * 1.5
        ):
            candidates.append((abs(small[1] + small[3] - large[1]), parent_id))
    return [parent_id for _, parent_id in sorted(candidates)]


def validate_plan(page: ContextPage, plan: PagePlan) -> None:
    """Reject ungrounded ruby, duplicate assignments and unrelated content masking."""
    if len({panel.id for panel in plan.panels}) != len(plan.panels):
        raise ValueError("Duplicate panel identity")
    main = [identity for panel in plan.panels for identity in panel.source_ids]
    ruby_ids = [ruby.source_id for ruby in plan.furigana]
    if any(count != 1 for count in Counter(main + ruby_ids).values()):
        raise ValueError("Duplicate source assignment")
    for identity in main + ruby_ids:
        _box(page, identity)
    for ruby in plan.furigana:
        if ruby.parent_id in ruby_ids or ruby.parent_id == ruby.source_id:
            raise ValueError("Ruby parent must be retained main text")
        if ruby.parent_id not in eligible_ruby_parents(page, ruby.source_id):
            raise ValueError(
                f"Unconfirmed furigana: {ruby.source_id} -> {ruby.parent_id}"
            )
    for panel in plan.panels:
        bounds = panel_box(page, panel, plan)
        if (
            bounds[2] > 0.62
            and max(_box(page, identity)[2] for identity in panel.source_ids)
            < bounds[2] * 0.75
        ):
            raise ValueError("Panel crosses page columns")
        owned = set(panel.source_ids) | {
            ruby.source_id
            for ruby in plan.furigana
            if ruby.parent_id in panel.source_ids
        }
        for identity in range(len(page.blocks)):
            if identity in owned:
                continue
            other = _box(page, identity)
            center = (other[0] + other[2] / 2, other[1] + other[3] / 2)
            if (
                bounds[0] + 0.001 < center[0] < bounds[0] + bounds[2] - 0.001
                and bounds[1] + 0.001 < center[1] < bounds[1] + bounds[3] - 0.001
            ):
                raise ValueError(
                    f"Panel {panel.id} would mask unrelated source {identity}"
                )


def validate_translations(
    page: ContextPage,
    plan: PagePlan,
    result: PanelTranslations,
    reference_texts: dict[str, str] | None = None,
) -> None:
    """Require complete, source-paired bullets and preserve explicit numeric/safety facts."""
    from src.quality.quantities import numeric_tokens

    if len(result.panels) != len(plan.panels) or {
        text.panel_id for text in result.panels
    } != {panel.id for panel in plan.panels}:
        raise ValueError("Missing or duplicate panel translation")
    by_id = {text.panel_id: text for text in result.panels}
    required_terms = {
        "ペースメーカー": r"pacemaker",
        "プラスマイナス": r"polarity|plus.?minus|positive.*negative|\+.*[−–-]",
        "LED": r"\bLED\b",
        "窒息": r"chok|suffocat",
        "充電式": r"rechargeable",
        "ショート": r"short.?circuit",
        "分解": r"disassembl|dismantl|take.*apart",
        "火の中": r"fire",
        "保護者": r"guardian|parent|adult",
        "お読み": r"read",
        "ご説明": r"explain",
    }
    for panel in plan.panels:
        text = by_id[panel.id]
        if len(text.japanese_items) != len(text.english_items) or not all(
            item.strip() for item in text.japanese_items + text.english_items
        ):
            raise ValueError("Incomplete source/English pairing")
        source = "\n".join(page.blocks[identity].text for identity in panel.source_ids)
        for japanese, english_item in zip(
            text.japanese_items, text.english_items, strict=True
        ):
            source_chars = len(re.findall(r"[ぁ-ゖァ-ヺ一-龯]", japanese))
            if (
                panel.kind == "prose"
                and source_chars > 25
                and len(re.findall(r"[A-Za-z]", english_item)) < source_chars * 0.7
            ):
                raise ValueError(
                    f"{panel.id}: likely summary of a full source paragraph"
                )
        reference = (reference_texts or {}).get(panel.id, source)
        headings = {
            "warning": "警告",
            "caution": "注意",
            "precautionsforuse": "使用上の注意",
        }
        for item in text.english_items:
            heading = headings.get(re.sub(r"[^a-z]", "", item.casefold()))
            if heading and heading not in source + reference:
                raise ValueError(f"{panel.id}: unrelated neighboring heading imported")
        english = "\n".join(text.english_items)
        if re.search(
            r"\([^)]*(?:incomplete in (?:the )?source|OCR (?:error|unclear)|illegible in (?:the )?(?:scan|source))[^)]*\)",
            english,
            re.IGNORECASE,
        ):
            raise ValueError(
                f"{panel.id}: editorial uncertainty belongs in uncertainties, not translated instructions"
            )
        if re.search(r"[ぁ-ゖァ-ヺ一-龯]", english):
            raise ValueError(f"{panel.id}: untranslated Japanese/OCR glyphs in English")
        bullets = max(source.count("●"), reference.count("●"))
        if panel.kind == "prose" and len(text.english_items) < bullets:
            raise ValueError(f"{panel.id}: source bullet coverage reduced")
        # Reconstructed Japanese may correct OCR errors, but both views must
        # retain the explicit source quantities rather than trusting the model.
        for output in ("\n".join(text.japanese_items), english):
            if (numeric_tokens(source) | numeric_tokens(reference)) - numeric_tokens(
                output
            ):
                raise ValueError(f"{panel.id}: explicit source quantity missing")
        for japanese, pattern in required_terms.items():
            if (japanese in source or japanese in reference) and not re.search(
                pattern, english, re.IGNORECASE | re.DOTALL
            ):
                raise ValueError(
                    f"{panel.id}: explicit safety fact missing ({japanese})"
                )


def normalize_plan(page: ContextPage, proposed: PagePlan) -> PagePlan:
    """Resolve redundant annotation membership and ground panels in physical columns.

    Rejected annotation links retain their original text. Regions excluded from
    a panel remain available as passthrough overlays; they are never deleted.
    """
    from statistics import median

    plan = proposed.model_copy(deep=True)
    links: list[RubyLink] = []
    for link in plan.furigana:
        try:
            validate_plan(page, PagePlan(panels=[], furigana=[link], notes=[]))
        except ValueError as error:
            plan.notes.append(f"Retained source {link.source_id}: {error}")
        else:
            if link.source_id not in {item.source_id for item in links}:
                links.append(link)
    plan.furigana = links
    ruby_ids = {link.source_id for link in links}
    panels: list[Panel] = []
    for panel in plan.panels:
        members = [
            identity
            for identity in dict.fromkeys(panel.source_ids)
            if identity not in ruby_ids
        ]
        if not members:
            continue
        if panel.kind == "prose":
            anchor = max(
                (_box(page, identity) for identity in members),
                key=lambda bounds: bounds[2],
            )
            kept = [
                identity
                for identity in members
                if anchor[0] - 0.015
                <= _box(page, identity)[0] + _box(page, identity)[2] / 2
                <= anchor[0] + anchor[2] + 0.015
            ]
            if len(kept) != len(members):
                plan.notes.append(
                    f"{panel.id}: excluded other-column members; retained separately"
                )
            members = kept
            typical = median(
                _box(page, identity)[3]
                / max(1, len(page.blocks[identity].text.splitlines()))
                for identity in members
            )
            chunk: list[int] = []
            for identity in members:
                block = page.blocks[identity]
                bounds = _box(page, identity)
                heading = (
                    len(block.text.strip()) < 35
                    and bool(
                        re.search(r"使用上の注意|保護者の方へ|指はさみ注意", block.text)
                    )
                ) or ("\n" not in block.text and bounds[3] > typical * 1.7)
                if heading:
                    if chunk:
                        panels.append(
                            Panel(
                                id=f"{panel.id}-part-{len(panels)}",
                                kind="prose",
                                source_ids=chunk,
                            )
                        )
                        chunk = []
                    panels.append(
                        Panel(
                            id=f"{panel.id}-heading-{identity}",
                            kind="heading",
                            source_ids=[identity],
                        )
                    )
                else:
                    chunk.append(identity)
            if chunk:
                panels.append(Panel(id=panel.id, kind="prose", source_ids=chunk))
        else:
            panels.append(panel.model_copy(update={"source_ids": members}))
    plan.panels = panels
    # Close small OCR omissions within each grounded panel, without stealing
    # another panel's regions or following text across a large vertical gap.
    for panel in plan.panels:
        if panel.kind != "prose":
            continue
        for _round in range(3):
            owned = {identity for item in plan.panels for identity in item.source_ids}
            bounds = panel_box(page, panel, plan)
            heights = [
                _box(page, identity)[3]
                / max(1, len(page.blocks[identity].text.splitlines()))
                for identity in panel.source_ids
            ]
            typical = median(heights)
            extra = []
            for identity, block in enumerate(page.blocks):
                if identity in owned or identity in ruby_ids:
                    continue
                box = _box(page, identity)
                center = box[0] + box[2] / 2
                inside = (
                    bounds[0] <= center <= bounds[0] + bounds[2]
                    and bounds[1] <= box[1] + box[3] / 2 <= bounds[1] + bounds[3]
                )
                continuation = (
                    bounds[0] <= center <= bounds[0] + bounds[2]
                    and 0 <= box[1] - (bounds[1] + bounds[3]) < typical * 1.2
                    and box[3] / max(1, len(block.text.splitlines())) <= typical * 1.6
                )
                if inside or continuation:
                    extra.append(identity)
            if not extra:
                break
            panel.source_ids = sorted(
                panel.source_ids + extra,
                key=lambda identity: (_box(page, identity)[1], _box(page, identity)[0]),
            )
    validate_plan(page, plan)
    return plan


class OCRLine(Artifact):
    """Fine-grained fresh OCR geometry used to recover clipped legacy continuations."""

    id: int
    text: str
    bbox: tuple[float, float, float, float]


def grounded_panel(
    page: ContextPage, plan: PagePlan, panel: Panel, lines: list[OCRLine]
) -> tuple[tuple[float, float, float, float], list[OCRLine]]:
    """Extend masks to actual OCR line edges, following only short continuations."""
    from statistics import median

    base = panel_box(page, panel, plan)
    selected = [
        line
        for line in lines
        if base[0] <= line.bbox[0] + line.bbox[2] / 2 <= base[0] + base[2]
        and base[1] <= line.bbox[1] + line.bbox[3] / 2 <= base[1] + base[3]
        and line.bbox[2] <= base[2] * 1.3
    ]
    if not selected:
        return base, []
    typical = median(
        _box(page, identity)[3] / max(1, len(page.blocks[identity].text.splitlines()))
        for identity in panel.source_ids
    )
    if panel.kind == "prose":
        for _round in range(3):
            bottom = max(line.bbox[1] + line.bbox[3] for line in selected)
            extra = [
                line
                for line in lines
                if line.id not in {item.id for item in selected}
                and base[0] <= line.bbox[0] + line.bbox[2] / 2 <= base[0] + base[2]
                and line.bbox[2] <= base[2] * 1.3
                and 0 <= line.bbox[1] - bottom < typical * 1.5
                and line.bbox[3] <= typical * 1.6
                and not line.text.lstrip().startswith(("●", "•"))
            ]
            if not extra:
                break
            selected.extend(extra)
    left = max(0, min(base[0], *(line.bbox[0] - 0.0015 for line in selected)))
    top = max(0, min(base[1], *(line.bbox[1] - 0.0015 for line in selected)))
    right = min(
        1,
        max(
            base[0] + base[2],
            *(line.bbox[0] + line.bbox[2] + 0.0015 for line in selected),
        ),
    )
    bottom = min(
        1,
        max(
            base[1] + base[3],
            *(line.bbox[1] + line.bbox[3] + 0.0015 for line in selected),
        ),
    )
    return (left, top, right - left, bottom - top), sorted(
        selected, key=lambda line: (line.bbox[1], line.bbox[0])
    )


def cover_reading_guides(
    page: ContextPage,
    plan: PagePlan,
    panel: Panel,
    bounds: tuple[float, float, float, float],
    lines: list[OCRLine],
) -> tuple[float, float, float, float]:
    """Cover small fresh reading guides above a panel without covering another unit."""
    from statistics import median

    typical = median(
        _box(page, identity)[3] / max(1, len(page.blocks[identity].text.splitlines()))
        for identity in panel.source_ids
    )
    owned = set(panel.source_ids) | {
        ruby.source_id for ruby in plan.furigana if ruby.parent_id in panel.source_ids
    }
    top = bounds[1]
    for line in lines:
        box = line.bbox
        if (
            not re.fullmatch(r"[ぁ-ゖ\s・ー]+", line.text)
            or box[3] > typical * 0.75
            or not 0 <= bounds[1] - (box[1] + box[3]) < typical * 0.9
            or not bounds[0] <= box[0] + box[2] / 2 <= bounds[0] + bounds[2]
        ):
            continue
        blocked = False
        for identity, block in enumerate(page.blocks):
            if identity in owned or block.bbox is None:
                continue
            center = (
                block.bbox[0] + block.bbox[2] / 2,
                block.bbox[1] + block.bbox[3] / 2,
            )
            if (
                box[0] <= center[0] <= box[0] + box[2]
                and box[1] <= center[1] <= box[1] + box[3]
            ):
                blocked = True
                break
        if not blocked:
            top = min(top, max(0, box[1] - 0.001))
    return bounds[0], top, bounds[2], bounds[3] + bounds[1] - top


class SourceMapping(TypedDict):
    """Every original region has one disposition in the prototype ledger."""

    display_index: int
    source_ids: list[int]
    furigana_ids: list[int]
    panel_id: NotRequired[str]
    japanese_items: NotRequired[list[str]]
    disposition: NotRequired[str]
