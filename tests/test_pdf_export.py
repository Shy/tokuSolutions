"""Opt-in Chrome checks of actual static-site downloads against desktop pixels."""

import asyncio
import json
import os
import shutil
from pathlib import Path
from urllib.parse import quote

import pymupdf
import pytest
from PIL import Image, ImageChops, ImageStat
from playwright.async_api import async_playwright

from src.quality.browser_audit import public_server


@pytest.mark.skipif(
    os.getenv("RUN_PDF_BROWSER_TESTS") != "1", reason="Opt-in actual PDF downloads"
)
@pytest.mark.parametrize(
    "manual_name",
    ["Zero One Limited Clear", "GoKaiKeys", "CSM Lockseed Charmant Set Manual"],
)
def test_download_matches_every_desktop_page(tmp_path: Path, manual_name: str) -> None:
    """Compare every PDF page with a native desktop capture, including lazy pages."""
    root = Path(__file__).resolve().parents[1]
    public = tmp_path / "public"
    shutil.copytree(root / "web", public / "web")
    shutil.copytree(root / "manuals" / manual_name, public / "manuals" / manual_name)
    manual = json.loads(
        (public / "manuals" / manual_name / "translations.json").read_text()
    )
    evidence = (
        Path(os.getenv("PDF_EVIDENCE_DIR", str(tmp_path / "evidence"))) / manual_name
    )
    evidence.mkdir(parents=True, exist_ok=True)

    async def verify(origin: str) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome")
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900}, device_scale_factor=2
            )
            page = await context.new_page()
            page.set_default_timeout(120000)
            await page.goto(
                origin + "/web/#" + quote(manual_name), wait_until="networkidle"
            )
            await page.wait_for_selector("#page-0 .overlay")
            await page.evaluate("""async()=>{
const version=new URL(document.querySelector('script[type=module]').src).search;
const {state}=await import('./js/state.js'+version);
const {fitRenderedManual}=await import('./js/text-fit-dom.js'+version);
for(const image of document.querySelectorAll('.page img')){
  if(image.dataset.src){image.src=image.dataset.src;delete image.dataset.src;}
  await image.decode();
}
await fitRenderedManual({root:document.getElementById('pagePanel'),manual:state.currentManual,generation:0,isCurrentGeneration:()=>true});
}""")
            await page.evaluate("""async()=>{
await import('./vendor/html-to-image.js');
window.exportedSvgPages=[];window.exportedNativePages=[];
const original=HTMLImageElement.prototype.decode;
HTMLImageElement.prototype.decode=async function(){if(this.src.startsWith('data:image/svg+xml')&&this.src.includes('foreignObject')){window.exportedSvgPages.push(this.src);window.exportedNativePages.push(window.lineMetrics(document.querySelector('iframe').contentDocument))}return original.call(this)};
window.lineMetrics=root=>[...root.querySelectorAll('.overlay')].map(overlay=>{
 const range=document.createRange();range.selectNodeContents(overlay);
 return {text:overlay.textContent,width:overlay.getBoundingClientRect().width,whiteSpace:getComputedStyle(overlay).whiteSpace,font:getComputedStyle(overlay).fontSize,lines:[...range.getClientRects()].map(rect=>rect.width)};
});
window.desktopLines=[...document.querySelectorAll('.page')].map(window.lineMetrics);
}""")
            await page.add_style_tag(
                content="html,body,#viewerView,.viewer-container,.page-panel {overflow:visible !important}.page-label{display:none}"
            )
            for index in range(len(manual["pages"])):
                # Align the capture origin to the device pixel grid. The page's
                # internal geometry stays unchanged; toolbar offsets are chrome.
                await page.locator(f"#page-{index}").evaluate(
                    "element=>{element.style.transform=`translateY(${-element.getBoundingClientRect().top % 0.5}px)`}"
                )
                await page.locator(f"#page-{index}").screenshot(
                    path=str(evidence / f"desktop-{index + 1}.png"),
                    animations="disabled",
                )
            async with page.expect_download() as pending:
                await page.locator("#downloadPdfBtn").click()
            download = await pending.value
            pdf_path = evidence / "manual.pdf"
            await download.save_as(pdf_path)
            assert await download.failure() is None
            assert await page.locator("iframe").count() == 0
            line_comparison = await page.evaluate("""async()=>{
const results=[];
for(const [index,svg] of window.exportedSvgPages.entries()){
 const frame=document.createElement('iframe');frame.style.cssText='position:fixed;left:-100000px;width:1280px;height:900px';
 const ready=new Promise(resolve=>frame.onload=resolve);
 frame.srcdoc=decodeURIComponent(svg.slice(svg.indexOf(',')+1));document.body.appendChild(frame);await ready;await frame.contentDocument.fonts.ready;
 const actual=window.lineMetrics(frame.contentDocument);
 results.push({index,actual,native:window.exportedNativePages[index],expected:window.desktopLines[index]});frame.remove();
}
return results;
}""")
            (evidence / "line-comparison.json").write_text(
                json.dumps(line_comparison, indent=2)
            )
            for comparison in line_comparison:
                assert len(comparison["actual"]) == len(comparison["expected"])
                for actual_block, expected_block in zip(
                    comparison["actual"], comparison["expected"], strict=True
                ):
                    assert actual_block["font"] == expected_block["font"]
                    assert actual_block["lines"] == pytest.approx(
                        expected_block["lines"], abs=0.05
                    )
            measurements: list[dict[str, float | int]] = []
            with pymupdf.open(pdf_path) as document:
                assert document.page_count == len(manual["pages"])
                for index, pdf_page in enumerate(document):
                    pixmap = pdf_page.get_pixmap(
                        matrix=pymupdf.Matrix(192 / 72, 192 / 72), alpha=False
                    )
                    actual = Image.frombytes(
                        "RGB", (pixmap.width, pixmap.height), pixmap.samples
                    )
                    actual.save(evidence / f"pdf-{index + 1}.png")
                    expected = Image.open(
                        evidence / f"desktop-{index + 1}.png"
                    ).convert("RGB")
                    # Fractional CSS page heights can differ by one raster row.
                    assert actual.width == expected.width
                    assert abs(actual.height - expected.height) <= 1
                    bounds = (0, 0, actual.width, min(actual.height, expected.height))
                    difference = ImageChops.difference(
                        actual.crop(bounds), expected.crop(bounds)
                    )
                    mean_error = sum(ImageStat.Stat(difference).mean) / 3
                    # Chrome element screenshots round scrolling/clip origins,
                    # whereas SVG capture starts at the page's local origin.
                    # Permit only a two-device-pixel registration offset. Fonts
                    # and every line width are independently exact-checked above.
                    bottom = min(actual.height, expected.height) - 3
                    registered_error, vertical_offset = min(
                        (
                            sum(
                                ImageStat.Stat(
                                    ImageChops.difference(
                                        expected.crop((0, 3, actual.width, bottom)),
                                        actual.crop(
                                            (
                                                0,
                                                3 + offset,
                                                actual.width,
                                                bottom + offset,
                                            )
                                        ),
                                    )
                                ).mean
                            )
                            / 3,
                            offset,
                        )
                        for offset in range(-2, 3)
                    )
                    measurements.append(
                        {
                            "page": index + 1,
                            "mean_channel_error": mean_error,
                            "registered_channel_error": registered_error,
                            "vertical_offset": vertical_offset,
                        }
                    )
                    assert registered_error < 5.0, measurements
            (evidence / "comparison.json").write_text(
                json.dumps(measurements, indent=2)
            )
            await browser.close()

    with public_server(public) as origin:
        asyncio.run(verify(origin))


@pytest.mark.skipif(
    os.getenv("RUN_PDF_BROWSER_TESTS") != "1", reason="Opt-in actual PDF downloads"
)
def test_mobile_edits_settings_and_failed_assets(tmp_path: Path) -> None:
    """Exercise current edits, source-ledger purity, mobile geometry and failure cleanup."""
    root = Path(__file__).resolve().parents[1]
    public = tmp_path / "public"
    name = "Zero One Limited Clear"
    shutil.copytree(root / "web", public / "web")
    shutil.copytree(root / "manuals" / name, public / "manuals" / name)
    manual = json.loads((public / "manuals" / name / "translations.json").read_text())
    manual["pages"] = manual["pages"][:1]
    (public / "manuals" / name / "translations.json").write_text(json.dumps(manual))

    async def verify(origin: str) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome")
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(60000)
            await page.goto(origin + "/web/#" + quote(name), wait_until="networkidle")
            await page.wait_for_selector("#page-0 .overlay")

            async def download_pdf(filename: str) -> bytes:
                async with page.expect_download() as pending:
                    await page.locator("#downloadPdfBtn").click()
                download = await pending.value
                path = tmp_path / filename
                await download.save_as(path)
                with pymupdf.open(path) as document:
                    assert document.page_count == 1
                    assert document[0].rect.width == 900
                    return bytes(document[0].get_pixmap().samples)

            desktop_pixels = await download_pdf("desktop.pdf")
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await download_pdf("mobile.pdf") == desktop_pixels
            await page.set_viewport_size({"width": 1280, "height": 900})
            await page.locator("#editTranslationsBtn").click()
            await page.locator("#page-0 .overlay").first.click(force=True)
            await page.locator("#inspectorTranslation").fill("Current local PDF edit")
            edited_pixels = await download_pdf("edited.pdf")
            assert edited_pixels != desktop_pixels
            exported = await page.evaluate("""async()=>{
const version=new URL(document.querySelector('script[type=module]').src).search;
const {state}=await import('./js/state.js'+version);
const {toPersistedManual}=await import('./js/manual-data.js'+version);
return toPersistedManual(state.currentManual);
}""")
            assert (
                exported["pages"][0]["blocks"][0]["translation"]
                == "Current local PDF edit"
            )
            assert (
                exported["pages"][0]["source_blocks"]
                == manual["pages"][0]["source_blocks"]
            )
            assert (
                exported["pages"][0]["layout_provenance"]
                == manual["pages"][0]["layout_provenance"]
            )
            assert [block["bbox"] for block in exported["pages"][0]["blocks"]] == [
                block["bbox"] for block in manual["pages"][0]["blocks"]
            ]
            await page.locator("#exitEditingBtn").click()
            await page.locator("#increaseTextSizeBtn").click()
            assert await download_pdf("large.pdf") != edited_pixels
            await page.locator("#translationsBtn").click()
            assert await download_pdf("source-only.pdf") != desktop_pixels
            for pattern in ("**/pages/*.webp", "**/fonts/*.woff2"):
                await page.route(pattern, lambda route: route.abort())
                await page.locator("#downloadPdfBtn").click()
                await page.wait_for_function(
                    "document.getElementById('pdfStatus').textContent.startsWith('PDF download failed:')"
                )
                assert await page.locator("iframe").count() == 0
                assert await page.locator("#downloadPdfBtn").is_enabled()
                await page.unroute(pattern)
            await browser.close()

    with public_server(public) as origin:
        asyncio.run(verify(origin))
