"""Local, recoverable publication after source validation and browser auditing."""

import asyncio
import fcntl
import hashlib
import json
import math
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from PIL import Image
from temporalio import activity

from src.activities.site_generation import _generate_main_index
from src.publication_models import AuditPreparation, PublishedManual, StagedManual
from src.quality.cleanup_context import ContextManual
from src.quality.core import write_bytes

ROOT = Path(__file__).resolve().parents[2]


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _renderer_hash(directory: Path) -> str:
    fingerprint = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix in {
            ".js",
            ".html",
            ".css",
            ".woff2",
            ".woff",
            ".svg",
            ".png",
        }:
            fingerprint.update(
                str(path.relative_to(directory)).encode() + path.read_bytes()
            )
    return fingerprint.hexdigest()


def _assets_match(directory: Path, audit: AuditPreparation) -> bool:
    return all(
        _hash(directory / image) == checksum
        for image, checksum in audit.asset_sha256.items()
    )


def _prepare(output_dir: str, run_id: str) -> StagedManual:
    target = Path(output_dir)
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    if target.parent != (ROOT / "manuals").resolve():
        raise ValueError("Output must be one directory inside manuals/")
    run_dir = (
        ROOT
        / "benchmark-runs/publication"
        / hashlib.sha256(run_id.encode()).hexdigest()[:24]
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "stage.json"
    if plan_path.exists():
        return StagedManual(**json.loads(plan_path.read_text()))
    plan = StagedManual(
        str(run_dir),
        str(run_dir / "staging" / target.name),
        str(target),
        str(run_dir / "source.json"),
        _hash(target / "translations.json"),
    )
    Path(plan.staging_dir).mkdir(parents=True, exist_ok=True)
    write_bytes(plan_path, json.dumps(asdict(plan), indent=2).encode())
    return plan


@activity.defn
async def prepare_publication_activity(output_dir: str) -> StagedManual:
    """Reserve run-local staging without touching the currently published manual."""
    run_id = activity.info().workflow_run_id
    if not run_id:
        raise ValueError("Publication requires a Workflow run")
    return await asyncio.to_thread(_prepare, output_dir, run_id)


def _validate(plan: StagedManual, page_count: int) -> ContextManual:
    stage = Path(plan.staging_dir)
    data = ContextManual.model_validate_json((stage / "translations.json").read_bytes())
    original = ContextManual.model_validate_json(
        Path(plan.source_snapshot).read_bytes()
    )
    if (
        len(data.pages) != page_count
        or len(original.pages) != page_count
        or page_count < 1
    ):
        raise ValueError("Incomplete page coverage")
    if data.meta.get("source") != original.meta.get("source"):
        raise ValueError("Source identity changed")
    for page, source in zip(data.pages, original.pages, strict=True):
        if page.layout_provenance is not None:
            from src.quality.semantic_layout import validate_grouped_page

            comparison_source = (
                source.model_copy(
                    update={
                        "blocks": source.source_blocks,
                        "source_blocks": None,
                        "layout_provenance": None,
                    }
                )
                if source.source_blocks is not None
                else source
            )
            validate_grouped_page(comparison_source, page)
        elif len(page.blocks) != len(source.blocks) or page.image != source.image:
            raise ValueError("Source regions or image identity changed")
        image = (stage / page.image).resolve()
        if not image.is_relative_to(stage.resolve()):
            raise ValueError("Page image escapes manual directory")
        if not image.is_file():
            raise ValueError("Missing page image")
        try:
            with Image.open(image) as scan:
                scan.verify()
        except OSError as error:
            raise ValueError("Unreadable page image") from error
        for index, block in enumerate(page.blocks):
            if page.layout_provenance is None and (
                block.text != source.blocks[index].text
                or block.bbox != source.blocks[index].bbox
            ):
                raise ValueError("Original Japanese or geometry changed")
            if not block.translation.strip():
                raise ValueError("Empty English translation")
            if not block.bbox or not all(math.isfinite(value) for value in block.bbox):
                raise ValueError("Invalid geometry")
            left, top, width, height = block.bbox
            if (
                min(left, top) < 0
                or min(width, height) <= 0
                or left + width > 1.001
                or top + height > 1.001
            ):
                raise ValueError("Invalid geometry bounds")
    return data


def _prepare_audit(plan: StagedManual, page_count: int) -> AuditPreparation:
    manual = _validate(plan, page_count)
    directory = Path(plan.run_dir) / "audit"
    manifest = directory / "prepared.json"
    checksum = _hash(Path(plan.staging_dir) / "translations.json")
    if manifest.exists():
        result = AuditPreparation(**json.loads(manifest.read_text()))
        if result.json_sha256 != checksum or not _assets_match(
            Path(plan.staging_dir), result
        ):
            raise ValueError("Staged manual changed after audit preparation")
        return result
    public = directory / "public"
    if public.exists():
        shutil.rmtree(public)
    web = public / "baseline/web"
    target = public / "baseline/manuals" / Path(plan.final_dir).name
    shutil.copytree(
        ROOT / "web", web, ignore=shutil.ignore_patterns(".DS_Store", ".github")
    )
    shutil.copytree(plan.staging_dir, target)
    # Keep the audit viewer scoped to this manual; no repository or cache server.
    write_bytes(
        web / "meta.json",
        json.dumps(
            {
                "manuals": [
                    {
                        "name": target.name,
                        "source": target.name,
                        "pages": page_count,
                        "blocks": 0,
                        "thumbnail": target.name + "/pages/page-0.webp",
                        "tags": [],
                    }
                ],
                "tags": {},
                "stats": {
                    "total_manuals": 1,
                    "total_pages": page_count,
                    "total_blocks": 0,
                },
            }
        ).encode(),
    )
    result = AuditPreparation(
        str(public),
        str(directory),
        target.name,
        checksum,
        _renderer_hash(web),
        page_count,
        {
            page.image: _hash(Path(plan.staging_dir) / page.image)
            for page in manual.pages
        },
    )
    write_bytes(manifest, json.dumps(asdict(result), indent=2).encode())
    return result


@activity.defn
async def prepare_audit_activity(
    plan: StagedManual, page_count: int
) -> AuditPreparation:
    """Validate source completeness and freeze a public-only browser input package."""
    return await asyncio.to_thread(_prepare_audit, plan, page_count)


def _publish(
    plan: StagedManual, audit: AuditPreparation, warnings: list[str], audit_path: str
) -> PublishedManual:
    run_dir = Path(plan.run_dir)
    stage = Path(plan.staging_dir)
    target = Path(plan.final_dir)
    backup = run_dir / "previous-manual"
    done = run_dir / "published.json"
    lock_path = ROOT / "benchmark-runs/publication/publication.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if done.exists():
                result = PublishedManual(**json.loads(done.read_text()))
                _generate_main_index(ROOT / "manuals")
                return result
            if _renderer_hash(ROOT / "web") != audit.renderer_sha256:
                raise ValueError(
                    "Renderer changed after browser audit; staged output retained"
                )
            intent = run_dir / "publishing.json"
            if not intent.exists():
                _validate(plan, audit.page_count)
                if _hash(stage / "translations.json") != audit.json_sha256:
                    raise ValueError("Staged manual changed after browser audit")
                write_bytes(intent, json.dumps({"sha256": audit.json_sha256}).encode())
            # Recover a worker loss after the directory move but before index/result.
            moved = (
                not stage.exists()
                and _hash(target / "translations.json") == audit.json_sha256
            )
            if not moved:
                _validate(plan, audit.page_count)
                if _hash(stage / "translations.json") != audit.json_sha256:
                    raise ValueError("Staged manual changed after browser audit")
                if not _assets_match(stage, audit):
                    raise ValueError("Source images changed after browser audit")
                current = _hash(target / "translations.json")
                if target.exists() and current != plan.previous_sha256:
                    raise ValueError(
                        "Published manual changed during translation; staged output retained"
                    )
                if not target.exists() and plan.previous_sha256 and not backup.exists():
                    raise ValueError("Published manual changed during translation")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if backup.exists():
                        raise ValueError(
                            "Backup conflict; refusing to discard either manual"
                        )
                    os.replace(target, backup)
                try:
                    os.replace(stage, target)
                except OSError:
                    if backup.exists() and not target.exists():
                        os.replace(backup, target)
                    raise
            result = PublishedManual(
                str(target),
                str(target / "translations.json"),
                f"web/index.html#{quote(target.name, safe='')}",
                str(backup) if backup.exists() else "",
            )
            write_bytes(
                run_dir / "quality-summary.json",
                json.dumps(
                    {
                        "status": "complete_with_warnings" if warnings else "complete",
                        "warnings": warnings,
                        "audit_path": audit_path,
                        "renderer_sha256": audit.renderer_sha256,
                    },
                    indent=2,
                ).encode(),
            )
            # A result marker makes result-loss retries idempotent; reindex is safe to retry.
            write_bytes(done, json.dumps(asdict(result), indent=2).encode())
            _generate_main_index(ROOT / "manuals")
            return result
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@activity.defn
async def publish_manual_activity(
    plan: StagedManual, audit: AuditPreparation, warnings: list[str], audit_path: str
) -> PublishedManual:
    """Publish validated data once, preserving a backup and concurrent editor changes."""
    return await asyncio.to_thread(_publish, plan, audit, warnings, audit_path)
