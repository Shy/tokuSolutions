"""Read-only browser auditing with retryable per-page artifact references."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import cast

from playwright.async_api import async_playwright
from pydantic import JsonValue, TypeAdapter
from temporalio import activity

from src.publication_models import AuditPreparation, AuditSummary, PageAudit
from src.quality.browser_audit import capture, public_server
from src.quality.cleanup_context import ContextManual
from src.quality.core import write_bytes
from src.quality.layout import ViewMetrics


async def _heartbeat() -> None:
    while True:
        activity.heartbeat("Rendering page")
        await asyncio.sleep(5)


@activity.defn
async def audit_page_activity(prepared: AuditPreparation, page_index: int) -> PageAudit:
    """Measure desktop/mobile/PDF without model calls or published-manual mutation."""
    if not 0 <= page_index < prepared.page_count:
        raise ValueError("Unknown source page")
    directory = Path(prepared.audit_dir) / "pages" / f"page-{page_index + 1:02d}"
    saved = directory / "result.json"
    if saved.exists():
        return PageAudit(**json.loads(saved.read_text()))
    manual_path = (
        Path(prepared.public_dir)
        / "baseline/manuals"
        / prepared.manual_name
        / "translations.json"
    )
    manual = ContextManual.model_validate_json(manual_path.read_bytes())
    page = manual.pages[page_index]
    data: dict[str, JsonValue] = {
        "meta": manual.meta,
        "pages": [cast(JsonValue, page.model_dump(mode="json"))],
    }
    heartbeat = asyncio.create_task(_heartbeat())
    try:
        async with asyncio.timeout(100):
            with public_server(Path(prepared.public_dir)) as origin:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(
                        channel="chrome", headless=True
                    )
                    try:
                        await capture(
                            browser,
                            origin,
                            Path(prepared.audit_dir),
                            page_index,
                            data,
                            "render",
                            manual_name=prepared.manual_name,
                        )
                        result = PageAudit(
                            page_index,
                            True,
                            str(directory / "render-metrics.json"),
                            browser.version,
                        )
                    finally:
                        await browser.close()
        write_bytes(saved, json.dumps(asdict(result), indent=2).encode())
        return result
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


def _summarize(prepared: AuditPreparation, pages: list[PageAudit]) -> AuditSummary:
    counts = {
        view: {"regions": 0, "fit_overflow": 0, "dom_overflow": 0, "small_text": 0}
        for view in ("desktop", "mobile", "pdf")
    }
    manual = ContextManual.model_validate_json(
        (
            Path(prepared.public_dir)
            / "baseline/manuals"
            / prepared.manual_name
            / "translations.json"
        ).read_bytes()
    )
    warnings: list[str] = []
    checked = 0
    versions = set()
    if sorted(page.page for page in pages) != list(range(prepared.page_count)):
        raise ValueError("Missing or duplicate page audit results")
    for page in pages:
        if not page.success:
            warnings.append(
                f"Browser audit unavailable on page {page.page + 1}: {page.error}"
            )
            continue
        try:
            path = Path(page.metrics_path)
            if not path.resolve().is_relative_to(Path(prepared.audit_dir).resolve()):
                raise ValueError("Invalid audit evidence path")
            views = TypeAdapter(list[ViewMetrics]).validate_json(path.read_bytes())
            if len(views) != 3 or {view.view for view in views} != set(counts):
                raise ValueError("Incomplete viewport coverage")
            for view in views:
                if any(
                    block.dom_overflow is None or block.fit_overflow is None
                    for block in view.blocks
                ):
                    raise ValueError("Incomplete fit/DOM evidence")
                if sorted(block.block_index for block in view.blocks) != list(
                    range(len(manual.pages[page.page].blocks))
                ):
                    raise ValueError("Incomplete region coverage")
        except (OSError, ValueError) as error:
            warnings.append(
                f"Browser audit unavailable on page {page.page + 1}: {error}"
            )
            continue
        checked += 1
        versions.add(page.browser_version)
        for view in views:
            count = counts[view.view]
            count["regions"] += len(view.blocks)
            count["fit_overflow"] += sum(
                bool(block.fit_overflow) for block in view.blocks
            )
            count["dom_overflow"] += sum(
                bool(block.dom_overflow) for block in view.blocks
            )
            count["small_text"] += sum(
                block.font_px < (10 if view.view == "mobile" else 12)
                for block in view.blocks
            )
    for view_name, count in counts.items():
        if count["fit_overflow"] or count["dom_overflow"] or count["small_text"]:
            warnings.append(
                f"{view_name}: {count['dom_overflow']} DOM overflow, {count['fit_overflow']} fit warnings, {count['small_text']} small-text regions (advisory)"
            )
    if len(versions) > 1:
        warnings.append("Browser version changed during auditing")
    report = Path(prepared.audit_dir) / "summary.json"
    write_bytes(
        report,
        json.dumps(
            {
                "pages_total": prepared.page_count,
                "pages_checked": checked,
                "renderer_sha256": prepared.renderer_sha256,
                "browser_versions": sorted(versions),
                "views": counts,
                "warnings": warnings,
                "page_results": [asdict(page) for page in pages],
            },
            ensure_ascii=False,
            indent=2,
        ).encode(),
    )
    return AuditSummary(str(report), checked, warnings)


@activity.defn
async def summarize_audit_activity(
    prepared: AuditPreparation, pages: list[PageAudit]
) -> AuditSummary:
    """Keep layout warnings advisory and explicitly record unavailable measurements."""
    return await asyncio.to_thread(_summarize, prepared, pages)
