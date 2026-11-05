"""Detect untranslated display text while preserving source scripts and valid units."""

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.quality.cleanup_context import ContextPage
    from src.quality.page_grouping import PagePlan


def unexpected_letters(text: str) -> set[str]:
    """Flag non-Latin letters for source checking; common electrical units are valid."""
    return {
        character
        for character in text
        if unicodedata.category(character).startswith("L")
        and "LATIN" not in unicodedata.name(character, "")
        and character not in {"µ", "μ", "Ω"}
    }


def normalize_battery_codes(source: str, translation: str) -> str:
    """Convert explicit Japanese AA/AAA designations without changing battery counts."""
    for digit, code in (("3", "AA"), ("4", "AAA")):
        if re.search(r"単\s*" + digit, unicodedata.normalize("NFKC", source)):
            translation = re.sub(
                r"\b(?:AA|AAA)\s*\(\s*単\s*" + digit + r"\s*\)", code, translation
            )
            translation = re.sub(r"単\s*" + digit, code, translation)
    return translation


def normalize_parallel_quotes(source: str, translation: str) -> str:
    """Keep an existing English equivalent instead of its duplicate source quotation."""
    compact_source = re.sub(r"\s+", "", source)
    equivalents: dict[str, set[str]] = {}

    def replace(match: re.Match[str]) -> str:
        original, english = match.group(1), match.group(2)
        if (
            not unexpected_letters(original)
            or unexpected_letters(english)
            or not re.search(r"[A-Za-z]", english)
            or re.sub(r"\s+", "", original) not in compact_source
        ):
            return match.group(0)
        equivalents.setdefault(original, set()).add(english)
        return f'"{english}"'

    translation = re.sub(
        r'["「“]([^"「」“”]+)["」”]\s*\(\s*["“]([^"“”]+)["”]\s*\)',
        replace,
        translation,
    )
    for original, choices in equivalents.items():
        if len(choices) == 1:
            english = next(iter(choices))
            for opening, closing in (('"', '"'), ("「", "」"), ("“", "”")):
                translation = translation.replace(
                    opening + original + closing, '"' + english + '"'
                )
    return translation


def include_untranslated_labels(page: "ContextPage", plan: "PagePlan") -> "PagePlan":
    """Route unclaimed non-English labels through the existing bounded crop translator."""
    from src.quality.page_grouping import Panel

    result = plan.model_copy(deep=True)
    owned = {identity for panel in result.panels for identity in panel.source_ids}
    owned.update(link.source_id for link in result.furigana)
    owned.update(result.native_artwork)
    for identity, block in enumerate(page.blocks):
        if identity not in owned and unexpected_letters(block.translation):
            result.panels.append(
                Panel(
                    id=f"english-label-{identity}",
                    kind="heading",
                    source_ids=[identity],
                )
            )
    return result
