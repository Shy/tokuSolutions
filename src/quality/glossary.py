"""Load the shared toku terminology cache and resolve applicable accepted terms."""

import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, field_validator, model_validator

from src.quality.models import Artifact, Block, Flag, FrozenCase

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Source(Artifact):
    """Evidence supporting a spelling, with retrieval provenance."""

    url: str
    title: NonEmpty
    evidence: NonEmpty
    accessed: date
    kind: Literal["page", "search_result", "structured_data"] = "page"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Require a source URL rather than an unsupported model assertion."""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source must have an HTTP(S) URL")
        return value


class Entry(Artifact):
    """One preferred English spelling and its optional franchise/product scope."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    japanese: NonEmpty
    english: NonEmpty
    franchise: NonEmpty | None = None
    product: NonEmpty | None = None
    aliases: list[NonEmpty] = Field(default_factory=list)
    status: Literal["accepted", "needs_review", "ignored"] = "needs_review"
    sources: list[Source] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        """Keep evidence-free suggestions pending until a source is recorded."""
        if self.status == "accepted" and not self.sources:
            raise ValueError("accepted entries require source evidence")
        return self


class Glossary(Artifact):
    """A versioned, Git-reviewable cache shared across all toku manuals."""

    version: Literal[1] = 1
    entries: list[Entry]

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        """Ensure review references survive edits and reordered entries."""
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("duplicate glossary entry id")
        return self


@dataclass
class Resolution:
    """Accepted mappings and unresolved entry IDs for a specific context."""

    terms: dict[str, Entry]
    review: dict[str, list[str]]


def load_glossary(path: Path) -> Glossary:
    """Read and validate the repository cache without rewriting it.

    :param path: Shared JSON glossary file.
    :returns: Validated entries with source provenance and review state.
    """
    return Glossary.model_validate_json(path.read_bytes())


def resolve_glossary(
    glossary: Glossary, *, franchise: str | None = None, product: str | None = None
) -> Resolution:
    """Select applicable accepted terms and report pending/conflicting entries.

    Product scope takes priority over franchise scope, then unscoped terms.
    Equally specific conflicting spellings are withheld for review. Unknown
    context never activates a scoped term. This function does not replace text.
    """
    grouped: dict[str, list[Entry]] = {}
    for entry in glossary.entries:
        if entry.franchise is not None and entry.franchise != franchise:
            continue
        if entry.product is not None and entry.product != product:
            continue
        grouped.setdefault(entry.japanese, []).append(entry)
    terms: dict[str, Entry] = {}
    review: dict[str, list[str]] = {}
    for japanese, entries in grouped.items():
        pending = [entry.id for entry in entries if entry.status == "needs_review"]
        if pending:
            review[japanese] = sorted(pending)
        accepted = [entry for entry in entries if entry.status == "accepted"]
        if not accepted:
            continue
        priorities = {
            entry.id: 2 * int(entry.product is not None)
            + int(entry.franchise is not None)
            for entry in accepted
        }
        priority = max(priorities.values())
        specific = [entry for entry in accepted if priorities[entry.id] == priority]
        if len({entry.english for entry in specific}) > 1:
            review[japanese] = sorted(
                set(review.get(japanese, []) + [entry.id for entry in specific])
            )
        else:
            terms[japanese] = min(specific, key=lambda entry: entry.id)
    return Resolution(terms=terms, review=review)


def _term_pattern(terms: Iterable[str]) -> re.Pattern[str] | None:
    alternatives: list[str] = []
    for term in sorted(set(terms), key=lambda value: (-len(value), value)):
        if not term:
            continue
        left = right = ""
        # A short name such as キバ must not alter the longer name キバット.
        for character, side in ((term[0], "left"), (term[-1], "right")):
            alphabet = ""
            if re.fullmatch(r"[ァ-ヺー-ヿ]", character):
                alphabet = "ァ-ヺー-ヿ"
            elif re.fullmatch(r"[A-Za-z0-9_]", character):
                alphabet = "A-Za-z0-9_"
            if alphabet:
                if side == "left":
                    left = f"(?<![{alphabet}])"
                else:
                    right = f"(?![{alphabet}])"
        alternatives.append(left + re.escape(term) + right)
    return re.compile("|".join(alternatives)) if alternatives else None


def matching_terms(text: str, terms: Iterable[str]) -> list[str]:
    """Find nonoverlapping whole names, preferring longer registered phrases."""
    pattern = _term_pattern(terms)
    return [match.group() for match in pattern.finditer(text)] if pattern else []


def render_glossary_input(
    text: str, terms: Mapping[str, str], *, html_mode: bool
) -> str:
    """Render accepted terms as English, optionally protecting Google HTML spans.

    :param text: Original Japanese retained separately in the result artifact.
    :param terms: Context-resolved accepted source-to-target mappings.
    :param html_mode: Escape literal source markup and use do-not-translate spans.
    """
    pattern = _term_pattern(terms)

    def literal(value: str) -> str:
        return (
            html.escape(value, quote=False).replace("\n", "<br>")
            if html_mode
            else value
        )

    pieces: list[str] = []
    offset = 0
    for match in pattern.finditer(text) if pattern else []:
        pieces.append(literal(text[offset : match.start()]))
        english = literal(terms[match.group()])
        pieces.append(
            f'<span translate="no">{english}</span>' if html_mode else english
        )
        offset = match.end()
    pieces.append(literal(text[offset:]))
    return "".join(pieces)


def resolve_case_glossary(glossary: Glossary, case: FrozenCase) -> Resolution:
    """Use the frozen selection's franchise feature and product directory scope."""
    franchises = set(case.features) & {
        "kamen-rider",
        "sentai",
        "ultraman",
        "power-rangers",
    }
    franchise = next(iter(franchises)) if len(franchises) == 1 else None
    return resolve_glossary(
        glossary, franchise=franchise, product=Path(case.manual).name
    )


def glossary_flags(
    blocks: list[Block], resolution: Resolution, glossary: Glossary
) -> list[Flag]:
    """Flag applicable uncertainty and missing canonical names without rewriting output."""
    entries = {entry.id: entry for entry in glossary.entries}
    flags: list[Flag] = []
    for block in blocks:
        for japanese in sorted(set(matching_terms(block.text, resolution.review))):
            identifiers = resolution.review[japanese]
            evidence = "; ".join(
                f"{identifier}: {entries[identifier].english}"
                + (
                    f" ({entries[identifier].notes})"
                    if entries[identifier].notes
                    else ""
                )
                for identifier in identifiers
            )
            flags.append(
                Flag(
                    id=f"{block.id}:glossary-review-{identifiers[0]}",
                    block_id=block.id,
                    kind="glossary-review",
                    reason=f"Uncertain/conflicting terminology for {japanese}: {evidence}. Check the frozen glossary sources.",
                )
            )
        matches = matching_terms(block.text, resolution.terms)
        for japanese in sorted(set(matches)):
            entry = resolution.terms[japanese]
            english_pattern = (
                r"(?<!\w)"
                + r"\s+".join(re.escape(word) for word in entry.english.split())
                + (r"s?" if entry.english.split()[-1].casefold() == "key" else "")
                + r"(?!\w)"
            )
            occurrences = len(
                re.findall(english_pattern, block.translation, re.IGNORECASE)
            )
            if occurrences < matches.count(japanese):
                flags.append(
                    Flag(
                        id=f"{block.id}:glossary-mismatch-{entry.id}",
                        block_id=block.id,
                        kind="glossary-mismatch",
                        reason=f"Expected {japanese} → {entry.english}; the translated name is missing or changed. Source: {entry.sources[0].url}",
                    )
                )
    return flags
