"""Apply shared glossary research and retain advisory machine-translation notes."""

import hashlib
from pathlib import Path

from src.quality.core import Translator, digest, translate_regions, write_artifact
from src.quality.glossary import (
    Glossary,
    glossary_flags,
    resolve_glossary,
)
from src.quality.models import Artifact, Block, Flag
from src.quality.research import Lookup, ResearchPage, freeze_glossary


class ManualBlock(Artifact):
    """The existing OCR Activity payload, before glossary substitution."""

    text: str
    page: int
    x: float
    y: float
    width: float
    height: float


class ManualTranslation(ManualBlock):
    """Keep original text and geometry beside its translated draft."""

    translation: str


class ManualInput(Artifact):
    """Bind retried Activities to the same source, product, and translation model."""

    product: str
    model: str
    blocks: list[ManualBlock]


class ManualCandidate(Artifact):
    """A machine translation with optional notes for the final human edit."""

    product: str
    franchise: str | None
    model: str
    glossary_sha256: str
    blocks: list[ManualTranslation]
    flags: list[Flag]
    review_path: str


def infer_franchise(text: str) -> str | None:
    """Infer only explicit source franchise names; ambiguity disables acceptance."""
    normalized = text.casefold()
    markers = {
        "kamen-rider": ("仮面ライダー", "kamen rider", "kamen-rider"),
        "sentai": ("スーパー戦隊", "super sentai", "super-sentai"),
        "ultraman": ("ウルトラマン", "ultraman"),
        "power-rangers": ("パワーレンジャー", "power rangers", "power-rangers"),
    }
    found = [
        franchise
        for franchise, terms in markers.items()
        if any(term in normalized for term in terms)
    ]
    return found[0] if len(found) == 1 else None


def translate_manual(
    root: Path,
    blocks: list[ManualBlock],
    product: str,
    run_id: str,
    provider: Translator,
    lookup: Lookup,
    *,
    model: str = "google-tllm",
) -> ManualCandidate:
    """Research, translate, and retain the machine draft and advisory notes.

    :param run_id: Temporal run identifier; retries reuse its frozen evidence.
    :returns: Source-preserving output; terminology flags never require approval.
    """
    directory = (
        root
        / "benchmark-runs"
        / "pipeline"
        / hashlib.sha256(run_id.encode()).hexdigest()[:24]
    )
    input_path = directory / "input.json"
    source = ManualInput(product=product, model=model, blocks=blocks)
    if input_path.exists():
        if ManualInput.model_validate_json(input_path.read_bytes()) != source:
            raise ValueError("translation input changed; use a new workflow run")
    else:
        write_artifact(input_path, source)
    franchise = infer_franchise(
        product + "\n" + "\n".join(block.text for block in blocks)
    )
    regions = [
        Block(
            id=f"p{block.page}-b{index}",
            text=block.text,
            bbox=(block.x, block.y, block.width, block.height),
        )
        for index, block in enumerate(blocks)
    ]
    pages = [
        ResearchPage(
            case_id=f"page-{page}",
            product=product,
            franchise=franchise,
            blocks=[
                region
                for region, original in zip(regions, blocks, strict=True)
                if original.page == page
            ],
        )
        for page in sorted({block.page for block in blocks})
    ]
    frozen = freeze_glossary(
        root / "glossary.json", pages, lookup, directory / "glossary.json"
    )
    glossary = Glossary.model_validate_json(frozen)
    resolution = resolve_glossary(glossary, franchise=franchise, product=product)
    translated_regions = translate_regions(
        provider,
        regions,
        {japanese: entry.english for japanese, entry in resolution.terms.items()},
    )
    candidate = ManualCandidate(
        product=product,
        franchise=franchise,
        model=model,
        glossary_sha256=digest(frozen),
        blocks=[
            ManualTranslation(**block.model_dump(), translation=region.translation)
            for block, region in zip(blocks, translated_regions, strict=True)
        ],
        flags=glossary_flags(translated_regions, resolution, glossary),
        review_path=str((directory / "candidate.json").resolve()),
    )
    write_artifact(Path(candidate.review_path), candidate)
    return candidate
