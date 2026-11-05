"""Shared read-only browser measurement for production audits and layout research."""

import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote

from playwright.async_api import Browser, ViewportSize
from pydantic import JsonValue, TypeAdapter

from src.quality.cleanup_context import ContextManual
from src.quality.core import write_bytes
from src.quality.layout import RegionMetric, ViewMetrics

VIEWS: dict[str, ViewportSize] = {
    "desktop": {"width": 1280, "height": 900},
    "mobile": {"width": 390, "height": 844},
}


@contextmanager
def public_server(directory: Path) -> Iterator[str]:
    """Serve only an isolated public artifact directory on an ephemeral loopback port."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


METRICS_JS = """(selector) => [...document.querySelectorAll(selector)].map(element => ({
block_index:Number(element.dataset.blockId ?? element.dataset.blockIndex),
font_px:parseFloat(getComputedStyle(element).fontSize),
overflow:element.dataset.fitStatus==='overflow' || element.scrollHeight>element.clientHeight+1 || element.scrollWidth>element.clientWidth+1,
fit_overflow:element.dataset.fitStatus==='overflow',
dom_overflow:element.scrollHeight>element.clientHeight+1 || element.scrollWidth>element.clientWidth+1
}))"""


async def capture(
    browser: Browser,
    origin: str,
    output: Path,
    page_index: int,
    data: dict[str, JsonValue],
    phase: str,
    *,
    variant: str = "baseline",
    manual_name: str,
) -> list[ViewMetrics]:
    """Measure the actual shipped renderer, with one source page and isolated storage."""
    directory = output / "public" / "pages" / f"page-{page_index + 1:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    context = await browser.new_context(
        viewport=VIEWS["desktop"], device_scale_factor=1
    )
    page = await context.new_page()
    await page.route(
        re.compile(r"/manuals/[^/]+/translations\.json(?:\?.*)?$"),
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(data, ensure_ascii=False),
        ),
    )
    metrics: list[ViewMetrics] = []
    try:
        await page.goto(
            origin + "/" + variant + "/web/#" + quote(manual_name, safe=""),
            wait_until="networkidle",
        )
        await page.wait_for_selector("#page-0 img")
        await page.wait_for_function(
            "count=>document.querySelectorAll('#page-0 .overlay').length===count",
            arg=len(ContextManual.model_validate(data).pages[0].blocks),
        )
        await page.evaluate(
            "async()=>{await document.fonts.ready;await document.querySelector('#page-0 img').decode()}"
        )
        for view, viewport in VIEWS.items():
            await page.set_viewport_size(viewport)
            await page.evaluate(
                "()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"
            )
            await page.evaluate("""async()=>{
const {state}=await import('./js/state.js'+new URL(document.querySelector('script[type=module]').src).search);
const {fitRenderedManual}=await import('./js/text-fit-dom.js'+new URL(document.querySelector('script[type=module]').src).search);
await fitRenderedManual({root:document.getElementById('pagePanel'),manual:state.currentManual,generation:0,isCurrentGeneration:()=>true,readerScale:1});
}""")
            values = TypeAdapter(list[RegionMetric]).validate_python(
                await page.evaluate(METRICS_JS, "#page-0 .overlay")
            )
            metrics.append(
                ViewMetrics(
                    view=cast(Literal["desktop", "mobile"], view), blocks=values
                )
            )
            # Keep the measured CSS width/font sizes while unmasking the scroll
            # container for a complete native-pixel element screenshot. Without
            # this, Chrome captures a blank area below the visible panel.
            unclipping = await page.add_style_tag(
                content="html,body,#viewerView,.viewer-container,.page-panel {overflow:visible !important}"
            )
            await page.locator("#page-0").screenshot(
                path=str(directory / f"{phase}-{view}.png"),
                animations="disabled",
                scale="css",
            )
            await unclipping.evaluate("element=>element.remove()")
        await page.set_viewport_size(VIEWS["desktop"])
        await page.evaluate("""async()=>{
const {state}=await import('./js/state.js'+new URL(document.querySelector('script[type=module]').src).search);
const {createDesktopCapture}=await import('./js/pdf-export.js'+new URL(document.querySelector('script[type=module]').src).search);
window.auditPdfCapture=await createDesktopCapture();
await window.auditPdfCapture.renderPage(state.currentManual.pages[0],state.currentManualName,{scale:1,correctedTextVisible:true});
window.auditPdfCapture.frame.style.left='0';
window.auditPdfCapture.frame.style.zIndex='10000';
}""")
        pdf_frame = page.frames[-1]
        values = TypeAdapter(list[RegionMetric]).validate_python(
            await pdf_frame.evaluate(METRICS_JS, ".overlay")
        )
        metrics.append(ViewMetrics(view="pdf", blocks=values))
        await pdf_frame.add_style_tag(
            content="html,body,#viewerView,.viewer-container,.page-panel {overflow:visible !important}"
        )
        await pdf_frame.locator(".page").screenshot(
            path=str(directory / f"{phase}-pdf.png"),
            animations="disabled",
            scale="css",
        )
        write_bytes(
            output / "pages" / f"page-{page_index + 1:02d}" / f"{phase}-metrics.json",
            TypeAdapter(list[ViewMetrics]).dump_json(metrics, indent=2),
        )
        return metrics
    finally:
        await context.close()
