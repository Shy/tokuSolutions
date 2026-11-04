"""Deterministic name discovery and conservative, cached bilingual lookups."""

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

import requests
from pydantic import BaseModel, Field

from src.quality.glossary import Entry, Glossary, Source, load_glossary
from src.quality.models import Artifact, Block

PREFIXES = {
    "kamen-rider": ("仮面ライダー", "Kamen Rider "),
    "sentai": ("スーパー戦隊", "Super Sentai "),
    "ultraman": ("ウルトラマン", "Ultraman "),
}
# Operating vocabulary is translated normally, rather than researched as names.
STOP_WORDS = frozenset(
    [
        "ボタン",
        "キーパーツ",
        "キーモード",
        "プロジェクター",
        "リセットボタン",
        "バカ",
        "アンケート",
        "カードスロット",
        "ケーブル",
        "デラックス",
        "ナビゲート",
        "パワーアップフォーム",
        "モード",
        "セット",
        "ベルト",
        "スイッチ",
        "カード",
        "コード",
        "ライダー",
        "ドライバー",
        "レバー",
        "パーツ",
        "カバー",
        "カバーパーツ",
        "フェイスプレート",
        "ロック",
        "オン",
        "オフ",
        "サウンド",
        "アンロック",
        "クリア",
        "リセット",
        "テスト",
        "プロ",
        "アンタ",
        "アイツ",
        "ヤツ",
        "ミッチ",
        "メロディー",
        "タイミング",
        "スピーカー",
        "プラス",
        "マイナス",
        "アルカリ",
        "ドライバーセット",
    ]
)


class ResearchPage(Artifact):
    """Source context retained for every discovered name."""

    case_id: str
    product: str
    franchise: str | None = None
    blocks: list[Block]


class Occurrence(Artifact):
    """Link a research candidate back to an original OCR region."""

    case_id: str
    block_id: str


class Candidate(Artifact):
    """A possible name, never itself proof of an English spelling."""

    japanese: str
    product: str
    franchise: str | None
    occurrences: list[Occurrence]


class LookupResult(Artifact):
    """Only unambiguous canonical bilingual matches have an English value."""

    english: str | None = None
    sources: list[Source] = Field(default_factory=list)
    reason: str = "No exact bilingual franchise match; spelling remains provisional."
    failed: bool = False


class ResearchReport(Artifact):
    """Record bounded research, including unsuccessful and conflicting lookups."""

    policy: str = "wikidata-exact-labels-v1"
    added_ids: list[str] = Field(default_factory=list)
    pending_ids: list[str] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    outcomes: list[LookupResult] = Field(default_factory=list)


class Lookup(Protocol):
    """A replaceable public lookup boundary, usable offline in tests."""

    def lookup(self, japanese: str, franchise: str | None) -> LookupResult:
        """Return evidence or an explicit unresolved outcome."""
        ...


@runtime_checkable
class ContextualLookup(Protocol):
    """Optional whole-manual AI research behind the existing lookup boundary."""

    def research(
        self,
        pages: list[ResearchPage],
        candidates: list[Candidate],
        glossary: Glossary,
        *,
        limit: int,
    ) -> ResearchReport:
        """Return source-observed names with evidence and bounded outcomes."""
        ...


def discover_terms(
    pages: list[ResearchPage], glossary: Glossary, *, limit: int = 20
) -> list[Candidate]:
    """Rank repeated or isolated katakana names, excluding applicable known terms.

    :param limit: Maximum number of new names researched per invocation.
    :returns: Stable candidates with source-region references; not exhaustive NER.
    """
    if not 0 <= limit <= 200:
        raise ValueError("research limit must be between 0 and 200")
    candidates: dict[tuple[str, str, str | None], Candidate] = {}
    counts: Counter[tuple[str, str, str | None]] = Counter()
    isolated: set[tuple[str, str, str | None]] = set()
    for page in pages:
        known = {
            entry.japanese
            for entry in glossary.entries
            if entry.franchise in (None, page.franchise)
            and entry.product in (None, page.product)
        }
        for block in page.blocks:
            for match in re.finditer(r"[ァ-ヺー]{2,40}", block.text):
                term = match.group()
                if term in STOP_WORDS or term in known:
                    continue
                key = (term, page.product, page.franchise)
                counts[key] += 1
                if block.text.strip(" \n。、・「」") == term:
                    isolated.add(key)
                candidate = candidates.setdefault(
                    key,
                    Candidate(
                        japanese=term,
                        product=page.product,
                        franchise=page.franchise,
                        occurrences=[],
                    ),
                )
                occurrence = Occurrence(case_id=page.case_id, block_id=block.id)
                if occurrence not in candidate.occurrences:
                    candidate.occurrences.append(occurrence)
    ranked = sorted(
        (key for key in candidates if counts[key] >= 2 or key in isolated),
        key=lambda key: (
            -int(key in isolated),
            -counts[key],
            key[0],
            key[1],
            key[2] or "",
        ),
    )
    return [candidates[key] for key in ranked[:limit]]


class Label(BaseModel):
    """Read only the value from a Wikibase language value."""

    value: str


class Entity(BaseModel):
    """Keep canonical labels separate from aliases and search snippets."""

    id: str = ""
    lastrevid: int = 0
    labels: dict[str, Label] = Field(default_factory=dict)
    descriptions: dict[str, Label] = Field(default_factory=dict)


class Entities(BaseModel):
    """Validate the relevant fields of a public API response."""

    entities: dict[str, Entity]


class SearchHit(BaseModel):
    """Search results identify entities; their labels are not trusted evidence."""

    id: str


class SearchResults(BaseModel):
    """Parse bounded entity search results."""

    search: list[SearchHit]


def match_wikidata(
    japanese: str, franchise: str | None, raw: str, *, accessed: date | None = None
) -> LookupResult:
    """Accept exact labels with franchise evidence; reject fuzzy/alias matches.

    A shortened Rider name is allowed only when both canonical labels contain
    the corresponding explicit franchise prefix. Conflicting English labels
    remain pending. Every accepted source links to its precise revision.
    """
    if franchise not in PREFIXES:
        return LookupResult(reason="Unknown franchise; automatic acceptance disabled.")
    japanese_prefix, english_prefix = PREFIXES[franchise]
    matches: dict[str, list[Source]] = {}
    for entity in Entities.model_validate_json(raw).entities.values():
        if not re.fullmatch(r"Q[1-9][0-9]*", entity.id) or entity.lastrevid <= 0:
            continue
        source = entity.labels.get("ja", Label(value="")).value
        target = entity.labels.get("en", Label(value="")).value
        description = entity.descriptions.get("en", Label(value="")).value
        english: str | None = None
        if source == japanese_prefix + japanese and target.startswith(english_prefix):
            english = target.removeprefix(english_prefix).strip()
        elif (
            source == japanese
            and english_prefix.strip().lower() in (target + " " + description).lower()
        ):
            english = target.strip()
        if not english or not re.search(r"[A-Za-z]", english):
            continue
        if franchise == "sentai" and "power ranger" in english.lower():
            continue
        matches.setdefault(english, []).append(
            Source(
                url=f"https://www.wikidata.org/w/index.php?title={entity.id}&oldid={entity.lastrevid}",
                title=f"Wikidata {entity.id}: bilingual canonical labels",
                evidence=f"ja: {source}; en: {target}; description: {description}",
                accessed=accessed or date.today(),
                kind="structured_data",
            )
        )
    sources = [source for group in matches.values() for source in group]
    if len(matches) != 1:
        return LookupResult(
            sources=sources,
            reason=(
                "Conflicting exact English labels; spelling remains provisional."
                if matches
                else "No exact bilingual franchise match; spelling remains provisional."
            ),
        )
    english = next(iter(matches))
    return LookupResult(
        english=english,
        sources=matches[english],
        reason="Exact bilingual franchise labels.",
    )


def _write(path: Path, data: bytes) -> None:
    """Replace an artifact atomically on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


class WikidataLookup:
    """No-key public lookup with raw response caching and bounded timeouts."""

    def __init__(self, cache: Path) -> None:
        """Store successful API responses under an ignored artifact directory."""
        self.cache = cache

    def _get(self, parameters: dict[str, str]) -> str:
        parameters = {"format": "json", "maxlag": "5", **parameters}
        key = hashlib.sha256(
            json.dumps(parameters, sort_keys=True).encode()
        ).hexdigest()
        path = self.cache / f"{key}.json"
        if path.exists():
            return path.read_text(encoding="utf-8")
        response = requests.get(
            "https://www.wikidata.org/w/api.php",
            params=parameters,
            headers={
                "User-Agent": "TokuSolutionsGlossary/0.1 (toy manual terminology research)"
            },
            timeout=15,
        )
        response.raise_for_status()
        parsed: object = json.loads(response.text)
        if not isinstance(parsed, dict) or "error" in parsed:
            raise ValueError("Wikidata API returned an error instead of evidence")
        # Validate before caching: malformed/rate-limited responses are retryable.
        if parameters["action"] == "wbsearchentities":
            SearchResults.model_validate_json(response.text)
        else:
            Entities.model_validate_json(response.text)
        _write(path, response.content)
        return response.text

    def lookup(self, japanese: str, franchise: str | None) -> LookupResult:
        """Search original and explicit franchise-prefixed names, then verify labels."""
        if franchise not in PREFIXES:
            return LookupResult(
                reason="Unknown franchise; automatic acceptance disabled."
            )
        try:
            identifiers: set[str] = set()
            prefix = PREFIXES[franchise][0]
            queries = (
                [japanese]
                if japanese.startswith(prefix)
                else [japanese, prefix + japanese]
            )
            for query in queries:
                raw = self._get(
                    {
                        "action": "wbsearchentities",
                        "search": query,
                        "language": "ja",
                        "uselang": "en",
                        "limit": "5",
                    }
                )
                identifiers.update(
                    hit.id
                    for hit in SearchResults.model_validate_json(raw).search
                    if re.fullmatch(r"Q[1-9][0-9]*", hit.id)
                )
            if not identifiers:
                return LookupResult()
            raw = self._get(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(sorted(identifiers)),
                    "languages": "ja|en",
                    "props": "labels|descriptions|info",
                }
            )
            return match_wikidata(japanese, franchise, raw)
        except (requests.RequestException, ValueError) as error:
            return LookupResult(
                failed=True,
                reason=f"Public lookup failed ({type(error).__name__}); retry research.",
            )


def enrich_glossary(
    path: Path, pages: list[ResearchPage], lookup: Lookup, *, limit: int = 20
) -> ResearchReport:
    """Merge verified names and pending records without overwriting curated entries.

    Network requests happen outside the merge lock. Recheck the current cache
    under a filesystem lock so parallel workers cannot lose each other's edits.
    Pending English values retain Japanese as an explicit, unused placeholder.
    Failed requests are reported but are not persisted as permanent no-matches.
    """
    candidates = discover_terms(pages, load_glossary(path), limit=limit)
    if isinstance(lookup, ContextualLookup):
        report = lookup.research(pages, candidates, load_glossary(path), limit=limit)
        candidates, outcomes = report.candidates, report.outcomes
    else:
        outcomes = [lookup.lookup(term.japanese, term.franchise) for term in candidates]
        report = ResearchReport(candidates=candidates, outcomes=outcomes)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        glossary = load_glossary(path)
        for candidate, outcome in zip(candidates, outcomes, strict=True):
            if outcome.failed:
                continue
            existing = [
                entry
                for entry in glossary.entries
                if entry.japanese == candidate.japanese
                and entry.franchise in (None, candidate.franchise)
                and entry.product in (None, candidate.product)
            ]
            conflict = outcome.english is not None and any(
                entry.status == "accepted" and entry.english != outcome.english
                for entry in existing
            )
            # Never overwrite concurrent edits; preserve contradictory evidence
            # as a pending entry alongside the owner's accepted spelling.
            placeholders = [
                entry
                for entry in existing
                if entry.status == "needs_review"
                and entry.id.startswith("auto-")
                and entry.english == entry.japanese
                and not entry.sources
                and entry.product == candidate.product
                and entry.franchise == candidate.franchise
            ]
            if outcome.english and placeholders and not conflict:
                for entry in placeholders:
                    entry.english = outcome.english
                    entry.sources = outcome.sources
                    entry.status = "accepted"
                    entry.notes = outcome.reason
                    report.added_ids.append(entry.id)
                continue
            if existing and not conflict:
                continue
            identifier = (
                "auto-"
                + hashlib.sha256(
                    json.dumps(
                        [candidate.japanese, candidate.franchise, candidate.product],
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()[:20]
            )
            if conflict:
                identifier += "-conflict"
            if any(entry.id == identifier for entry in glossary.entries):
                continue
            accepted = outcome.english is not None and not conflict
            glossary.entries.append(
                Entry(
                    id=identifier,
                    japanese=candidate.japanese,
                    english=outcome.english or candidate.japanese,
                    franchise=candidate.franchise,
                    product=candidate.product,
                    status="accepted" if accepted else "needs_review",
                    sources=outcome.sources,
                    notes=(
                        "Conflicts with a concurrent accepted spelling. "
                        if conflict
                        else ""
                    )
                    + outcome.reason
                    + (
                        ""
                        if outcome.english is not None
                        else " English currently contains a Japanese placeholder and must not be applied."
                    ),
                )
            )
            (report.added_ids if accepted else report.pending_ids).append(identifier)
        if report.added_ids or report.pending_ids:
            _write(path, (glossary.model_dump_json(indent=2) + "\n").encode())
    return report


def freeze_glossary(
    path: Path,
    pages: list[ResearchPage],
    lookup: Lookup,
    snapshot: Path,
    *,
    limit: int = 20,
) -> bytes:
    """Research once per run, retaining failures as pending in its frozen snapshot.

    :param snapshot: Run-local glossary; existing snapshots are reused offline.
    :returns: Exact bytes to include in translation provenance and review checks.
    """
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with snapshot.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if snapshot.exists():
            frozen = snapshot.read_bytes()
            Glossary.model_validate_json(frozen)
            return frozen
        report = enrich_glossary(path, pages, lookup, limit=limit)
        glossary = load_glossary(path)
        for candidate, outcome in zip(report.candidates, report.outcomes, strict=True):
            if not outcome.failed:
                continue
            identifier = (
                "failed-"
                + hashlib.sha256(
                    json.dumps(
                        [candidate.japanese, candidate.franchise, candidate.product],
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()[:20]
            )
            glossary.entries.append(
                Entry(
                    id=identifier,
                    japanese=candidate.japanese,
                    english=candidate.japanese,
                    franchise=candidate.franchise,
                    product=candidate.product,
                    notes=outcome.reason
                    + " No verified English spelling; retry in a new run.",
                )
            )
            report.pending_ids.append(identifier)
        frozen = (glossary.model_dump_json(indent=2) + "\n").encode()
        _write(
            snapshot.with_name("glossary-research.json"),
            (report.model_dump_json(indent=2) + "\n").encode(),
        )
        _write(snapshot, frozen)
        return frozen
