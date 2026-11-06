"""Freeze inputs and run independent machine-translation stages with diagnostics."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import tomllib
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Protocol, runtime_checkable

import pymupdf as fitz
from pydantic import BaseModel

from src.quality.glossary import (
    Glossary,
    glossary_flags,
    matching_terms,
    resolve_case_glossary,
)
from src.quality.models import (
    Block,
    Flag,
    FrozenCase,
    Manifest,
    Manual,
    Result,
    Run,
    RunConfig,
    Suite,
)
from src.quality.research import Lookup, ResearchPage, freeze_glossary


class OCRProvider(Protocol):
    """Recognize regions without applying the production cleanup heuristics."""

    def recognize(self, image: Path) -> list[Block]:
        """Return every recognized nonempty text region and its coordinates."""
        ...


class Translator(Protocol):
    """Translate the same ordered source segments for each candidate model."""

    def translate(self, texts: list[str]) -> list[str]:
        """Return exactly one translation per input, preserving order."""
        ...


@runtime_checkable
class GlossaryTranslator(Translator, Protocol):
    """Translation adapter that can receive accepted contextual terminology."""

    def translate_with_glossary(
        self, texts: list[str], terms: dict[str, str]
    ) -> list[str]:
        """Translate with canonical terms and return one result per input."""
        ...


def digest(content: bytes) -> str:
    """Return the SHA-256 fingerprint of artifact bytes."""
    return hashlib.sha256(content).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    """Resolve a relative artifact path without allowing traversal or symlinks out."""
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("artifact paths must stay within their root")
    return path


def write_artifact(path: Path, artifact: BaseModel) -> None:
    """Atomically persist a validated artifact without partial JSON on failure."""
    write_bytes(path, (artifact.model_dump_json(indent=2) + "\n").encode("utf-8"))


def write_bytes(path: Path, content: bytes) -> None:
    """Atomically persist exact snapshot bytes without partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    """Atomically persist readable Unicode JSON with stable checkpoint bytes."""
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def prepare_suite(root: Path, manifest_path: Path, output: Path) -> Suite:
    """Render and snapshot all cases atomically; never overwrite an existing suite.

    :param root: Repository containing PDFs and published manuals.
    :param manifest_path: Explicit TOML page selection.
    :param output: New directory under ``benchmark-runs`` or outside the repo.
    :returns: Frozen input metadata and content fingerprints.
    """
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root) and not output.is_relative_to(
        root / "benchmark-runs"
    ):
        raise ValueError("in-repo output must be under benchmark-runs")
    if root.is_relative_to(output):
        raise ValueError("output cannot contain the repository")
    if output.exists():
        raise FileExistsError(f"suite already exists: {output}")
    raw_manifest = manifest_path.read_bytes()
    manifest = Manifest.model_validate(tomllib.loads(raw_manifest.decode("utf-8")))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=output.parent, prefix=".prepare-"))
    try:
        frozen_cases: list[FrozenCase] = []
        for case in manifest.cases:
            pdf = safe_path(root, case.pdf)
            snapshot = safe_path(root, case.manual) / "translations.json"
            # Resolve again because translations.json itself may be a symlink.
            if not snapshot.resolve().is_relative_to(root):
                raise ValueError("baseline must remain inside the repository")
            baseline_bytes = snapshot.read_bytes()
            manual = Manual.model_validate_json(baseline_bytes)
            with fitz.open(pdf) as document:
                if len(document) != len(manual.pages):
                    raise ValueError(f"PDF/baseline page count mismatch: {case.id}")
                if case.page > len(document):
                    raise ValueError(f"page outside source PDF: {case.id}")
                source_page = document[case.page - 1]
                scale = min(
                    manifest.dpi / 72,
                    7000 / max(source_page.rect.width, source_page.rect.height),
                )
                pixmap = source_page.get_pixmap(
                    matrix=fitz.Matrix(scale, scale),
                    alpha=False,
                )
                case_dir = staging / "cases" / case.id
                case_dir.mkdir(parents=True)
                image_path = case_dir / "source.png"
                pixmap.save(image_path)
                (case_dir / "original-manual.json").write_bytes(baseline_bytes)
                frozen_cases.append(
                    FrozenCase(
                        **case.model_dump(),
                        image=f"cases/{case.id}/source.png",
                        image_sha256=digest(image_path.read_bytes()),
                        snapshot=f"cases/{case.id}/original-manual.json",
                        snapshot_sha256=digest(baseline_bytes),
                        pdf_sha256=digest(pdf.read_bytes()),
                        width=pixmap.width,
                        height=pixmap.height,
                    )
                )
        suite = Suite(
            name=manifest.name,
            manifest_sha256=digest(raw_manifest),
            dpi=manifest.dpi,
            cases=frozen_cases,
        )
        (staging / "selection.toml").write_bytes(raw_manifest)
        write_artifact(staging / "suite.json", suite)
        staging.rename(output)
        return suite
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def load_suite(directory: Path) -> Suite:
    """Verify frozen images, snapshots, and the selection before using a suite."""
    suite = Suite.model_validate_json((directory / "suite.json").read_bytes())
    raw_selection = (directory / "selection.toml").read_bytes()
    if digest(raw_selection) != suite.manifest_sha256:
        raise ValueError("selection hash mismatch")
    selection = Manifest.model_validate(tomllib.loads(raw_selection.decode("utf-8")))
    case_fields = {"id", "pdf", "manual", "page", "features"}
    if (
        suite.name != selection.name
        or suite.dpi != selection.dpi
        or [case.model_dump(include=case_fields) for case in suite.cases]
        != [case.model_dump() for case in selection.cases]
    ):
        raise ValueError("frozen metadata does not match the original selection")
    for case in suite.cases:
        for name, expected in (
            (case.image, case.image_sha256),
            (case.snapshot, case.snapshot_sha256),
        ):
            if digest(safe_path(directory, name).read_bytes()) != expected:
                raise ValueError(f"frozen asset hash mismatch: {name}")
    return suite


def run_path(directory: Path, run_id: str) -> Path:
    """Restrict run IDs to a portable single path component."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", run_id):
        raise ValueError("run id must contain lowercase letters, digits, and hyphens")
    return safe_path(directory, f"runs/{run_id}")


def initialize_run(directory: Path, run_id: str, run: Run) -> Path:
    """Refuse to mix different model settings or upstream runs when resuming."""
    destination = run_path(directory, run_id)
    config_path = destination / "run.json"
    if config_path.exists():
        existing = Run.model_validate_json(config_path.read_bytes())
        if existing != run:
            raise ValueError("run configuration changed; choose a new run id")
    else:
        write_artifact(config_path, run)
    return destination


def case_fingerprint(directory: Path, case: FrozenCase, upstream: bytes = b"") -> str:
    """Bind a page result to selection, source pixels, baseline, and upstream text."""
    return digest(
        (directory / "suite.json").read_bytes()
        + case.model_dump_json().encode()
        + upstream
    )


def read_result(path: Path, case_id: str, stage: str, expected: str) -> Result:
    """Load a result only when its stage, identity, and exact inputs match."""
    result = Result.model_validate_json(path.read_bytes())
    if (result.case_id, result.stage, result.input_sha256) != (
        case_id,
        stage,
        expected,
    ):
        raise ValueError(f"result input hash or identity mismatch: {case_id}")
    return result


def run_ocr(
    directory: Path,
    run_id: str,
    config: RunConfig,
    provider: OCRProvider,
    case_ids: tuple[str, ...] = (),
) -> list[Result]:
    """Recognize selected pages and resume successful pages without calling models."""
    suite = load_suite(directory)
    selected = select_cases(suite, case_ids)
    destination = initialize_run(
        directory,
        run_id,
        Run(
            stage="ocr",
            suite_sha256=digest((directory / "suite.json").read_bytes()),
            config=config,
        ),
    )
    results: list[Result] = []
    for case in selected:
        path = destination / f"{case.id}.json"
        fingerprint = case_fingerprint(directory, case)
        if path.exists():
            result = read_result(path, case.id, "ocr", fingerprint)
        else:
            started = time.monotonic()
            blocks = provider.recognize(safe_path(directory, case.image))
            result = Result(
                case_id=case.id,
                stage="ocr",
                input_sha256=fingerprint,
                blocks=blocks,
                flags=quality_flags(blocks, translated=False),
                elapsed_seconds=time.monotonic() - started,
            )
            write_artifact(path, result)
        results.append(result)
    return results


def select_cases(suite: Suite, case_ids: tuple[str, ...]) -> list[FrozenCase]:
    """Fail on unknown IDs before beginning a model run."""
    unknown = set(case_ids) - {case.id for case in suite.cases}
    if unknown:
        raise ValueError(f"unknown cases: {', '.join(sorted(unknown))}")
    return [case for case in suite.cases if not case_ids or case.id in case_ids]


def translate_regions(
    provider: Translator, blocks: list[Block], terms: dict[str, str]
) -> list[Block]:
    """Translate ordered source regions with only observed accepted terms.

    :returns: New regions retaining all source identities and geometry.
    """
    texts = [block.text for block in blocks]
    observed = {
        japanese: english
        for japanese, english in terms.items()
        if any(matching_terms(text, [japanese]) for text in texts)
    }
    if observed:
        if not isinstance(provider, GlossaryTranslator):
            raise TypeError("provider does not support glossary-aware translation")
        translations = provider.translate_with_glossary(texts, observed)
    else:
        translations = provider.translate(texts)
    if len(translations) != len(blocks):
        raise ValueError("translation result count differs from source count")
    return [
        block.model_copy(update={"translation": translation})
        for block, translation in zip(blocks, translations, strict=True)
    ]


def run_translation(
    directory: Path,
    run_id: str,
    source_run: str,
    config: RunConfig,
    provider: Translator,
    case_ids: tuple[str, ...] = (),
    *,
    glossary_path: Path | None = None,
    research_lookup: Lookup | None = None,
    research_limit: int = 20,
) -> list[Result]:
    """Translate frozen OCR output; all candidates can share exactly the same input."""
    suite = load_suite(directory)
    selected = select_cases(suite, case_ids)
    upstream_dir = run_path(directory, source_run)
    upstream_run = Run.model_validate_json((upstream_dir / "run.json").read_bytes())
    suite_sha256 = digest((directory / "suite.json").read_bytes())
    if upstream_run.stage != "ocr" or upstream_run.suite_sha256 != suite_sha256:
        raise ValueError("translation requires an OCR run from the same frozen suite")
    if research_lookup is not None and glossary_path is None:
        raise ValueError("glossary research requires a glossary path")
    glossary_bytes = glossary_path.read_bytes() if glossary_path is not None else b""
    if research_lookup is not None and glossary_path is not None:
        pages: list[ResearchPage] = []
        # Research the whole frozen selection so partial runs can resume later.
        for case in suite.cases:
            source = read_result(
                upstream_dir / f"{case.id}.json",
                case.id,
                "ocr",
                case_fingerprint(directory, case),
            )
            franchises = set(case.features) & {
                "kamen-rider",
                "sentai",
                "ultraman",
                "power-rangers",
            }
            pages.append(
                ResearchPage(
                    case_id=case.id,
                    product=Path(case.manual).name,
                    franchise=next(iter(franchises)) if len(franchises) == 1 else None,
                    blocks=source.blocks,
                )
            )
        glossary_bytes = freeze_glossary(
            glossary_path,
            pages,
            research_lookup,
            run_path(directory, run_id) / "glossary.json",
            limit=research_limit,
        )
    glossary = (
        Glossary.model_validate_json(glossary_bytes)
        if glossary_path is not None
        else None
    )
    destination = initialize_run(
        directory,
        run_id,
        Run(
            stage="translation",
            suite_sha256=suite_sha256,
            config=config,
            source_run=source_run,
            glossary_sha256=digest(glossary_bytes) if glossary is not None else "",
            glossary_policy="accepted-names-v1" if glossary is not None else "",
        ),
    )
    if glossary is not None:
        snapshot = destination / "glossary.json"
        if snapshot.exists() and snapshot.read_bytes() != glossary_bytes:
            raise ValueError("frozen glossary hash mismatch")
        if not snapshot.exists():
            write_bytes(snapshot, glossary_bytes)
    results: list[Result] = []
    for case in selected:
        upstream_path = upstream_dir / f"{case.id}.json"
        upstream = read_result(
            upstream_path, case.id, "ocr", case_fingerprint(directory, case)
        )
        fingerprint = case_fingerprint(
            directory,
            case,
            upstream_path.read_bytes()
            + (upstream_dir / "run.json").read_bytes()
            + glossary_bytes,
        )
        path = destination / f"{case.id}.json"
        if path.exists():
            result = read_result(path, case.id, "translation", fingerprint)
        else:
            started = time.monotonic()
            resolution = (
                resolve_case_glossary(glossary, case) if glossary is not None else None
            )
            blocks = translate_regions(
                provider,
                upstream.blocks,
                {
                    japanese: entry.english
                    for japanese, entry in resolution.terms.items()
                }
                if resolution is not None
                else {},
            )
            result = Result(
                case_id=case.id,
                stage="translation",
                input_sha256=fingerprint,
                blocks=blocks,
                flags=quality_flags(blocks, translated=True)
                + (
                    glossary_flags(blocks, resolution, glossary)
                    if resolution is not None and glossary is not None
                    else []
                ),
                elapsed_seconds=time.monotonic() - started,
            )
            write_artifact(path, result)
        results.append(result)
    return results


def load_run_glossary(
    directory: Path, run_id: str, run: Run
) -> tuple[Glossary | None, bytes]:
    """Verify a run's own glossary snapshot, independent of today's repo cache."""
    if not run.glossary_sha256:
        if run.glossary_policy:
            raise ValueError("glossary policy requires a snapshot hash")
        return None, b""
    if run.glossary_policy != "accepted-names-v1":
        raise ValueError("unsupported glossary policy")
    raw = (run_path(directory, run_id) / "glossary.json").read_bytes()
    if digest(raw) != run.glossary_sha256:
        raise ValueError("frozen glossary hash mismatch")
    return Glossary.model_validate_json(raw), raw


def quality_flags(blocks: list[Block], *, translated: bool) -> list[Flag]:
    """Flag missing translations, low OCR confidence, and changed numeric tokens.

    These checks request review; they do not measure translation accuracy.
    """
    flags: list[Flag] = []
    for block in blocks:
        issues: list[tuple[str, str]] = []
        if block.confidence is not None and block.confidence < 0.8:
            issues.append(
                ("ocr-confidence", "OCR confidence below 0.8; inspect source crop.")
            )
        if translated:
            if not block.translation.strip():
                issues.append(("missing", "Translation is empty."))
            original_numbers = Counter(
                re.findall(
                    r"\d+(?:[.,]\d+)*", unicodedata.normalize("NFKC", block.text)
                )
            )
            translated_numbers = Counter(
                re.findall(
                    r"\d+(?:[.,]\d+)*", unicodedata.normalize("NFKC", block.translation)
                )
            )
            if original_numbers != translated_numbers:
                issues.append(
                    (
                        "numbers",
                        "Numeric tokens differ; check quantities, units, and references.",
                    )
                )
        for kind, reason in issues:
            flags.append(
                Flag(
                    id=f"{block.id}:{kind}", block_id=block.id, kind=kind, reason=reason
                )
            )
    return flags


def load_candidate(directory: Path, run_id: str) -> tuple[Suite, list[Result], str]:
    """Load complete machine output and verify its source and glossary provenance."""
    suite = load_suite(directory)
    destination = run_path(directory, run_id)
    raw_run = (destination / "run.json").read_bytes()
    run = Run.model_validate_json(raw_run)
    glossary, glossary_bytes = load_run_glossary(directory, run_id, run)
    if run.stage != "translation" or run.source_run is None:
        raise ValueError("preview requires a translation run")
    if run.suite_sha256 != digest((directory / "suite.json").read_bytes()):
        raise ValueError("candidate suite hash mismatch")
    source = run_path(directory, run.source_run)
    raw_source_run = (source / "run.json").read_bytes()
    source_run = Run.model_validate_json(raw_source_run)
    if source_run.stage != "ocr" or source_run.suite_sha256 != run.suite_sha256:
        raise ValueError("candidate has invalid OCR provenance")
    results: list[Result] = []
    content = raw_run + raw_source_run + glossary_bytes
    for case in suite.cases:
        raw_source = (source / f"{case.id}.json").read_bytes()
        read_result(
            source / f"{case.id}.json",
            case.id,
            "ocr",
            case_fingerprint(directory, case),
        )
        path = destination / f"{case.id}.json"
        results.append(
            read_result(
                path,
                case.id,
                "translation",
                case_fingerprint(
                    directory, case, raw_source + raw_source_run + glossary_bytes
                ),
            )
        )
        if glossary is not None:
            result = results[-1]
            resolution = resolve_case_glossary(glossary, case)
            flags = {
                flag.id: flag
                for flag in result.flags
                + glossary_flags(result.blocks, resolution, glossary)
            }
            results[-1] = result.model_copy(update={"flags": list(flags.values())})
        content += path.read_bytes() + raw_source
    return suite, results, digest(content)


def add_review_flag(
    directory: Path,
    run_id: str,
    case_id: str,
    block_id: str,
    kind: str,
    reason: str,
) -> None:
    """Add an advisory human/AI note without blocking machine output.

    :param reason: Specific ambiguity or conflict to inspect against the scan.
    """
    _suite, results, _fingerprint = load_candidate(directory, run_id)
    if not reason.strip():
        raise ValueError("review flag requires a reason")
    result = next((result for result in results if result.case_id == case_id), None)
    if result is None or block_id not in {block.id for block in result.blocks}:
        raise ValueError("review flag must reference an existing case and block")
    flag = Flag(
        id=f"{block_id}:review-{digest((kind + reason).encode())[:16]}",
        block_id=block_id,
        kind=kind,
        reason=reason.strip(),
    )
    if flag.id not in {existing.id for existing in result.flags}:
        result.flags.append(flag)
        write_artifact(run_path(directory, run_id) / f"{case_id}.json", result)
