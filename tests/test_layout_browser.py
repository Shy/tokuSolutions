"""Opt-in real-browser regression for fractional-width text measurement."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pydantic import TypeAdapter

from src.quality.browser_audit import METRICS_JS, public_server
from src.quality.layout import RegionMetric


@pytest.mark.skipif(
    os.getenv("RUN_LAYOUT_BROWSER_TESTS") != "1",
    reason="Opt-in local Chrome regression",
)
def test_fractional_ocr_box_fits_without_hiding_real_overflow(tmp_path: Path) -> None:
    """Exercise real fonts/DOM layout rather than an integer-width measurement mock."""
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "web/js", tmp_path / "js")
    shutil.copytree(root / "web/assets", tmp_path / "assets")
    (tmp_path / "index.html").write_text("""<!doctype html><style>
@font-face{font-family:'Source Sans 3';src:url('/assets/fonts/SourceSans3-Regular.woff2')}
body{margin:0}.page{position:relative;width:1200px}.page img{width:100%;display:block}.overlay{position:absolute;box-sizing:border-box;padding:2px;border:1px solid transparent;font-family:'Source Sans 3';line-height:1.15;white-space:pre-wrap;word-break:break-word}.text-fit-probe{position:fixed;left:-10000px;top:0;padding:0;box-sizing:border-box;font-family:'Source Sans 3';white-space:pre-wrap;word-break:break-word;visibility:hidden}
.semantic-panel{white-space:pre-line}</style><div class="page"><img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='874' height='1240'%3E%3C/svg%3E"><div class="overlay" data-page-id="0" data-block-id="0" style="left:20.33%;top:10.1%;width:20.5%;height:.97%">That's not pathetic. I understand being unsure...</div><div class="overlay semantic-panel" data-page-id="0" data-block-id="2" style="left:58.44%;top:46.29%;width:38.9288651561737%;height:12.92%"></div><div class="overlay" data-page-id="0" data-block-id="1" style="left:0;top:0;width:.1%;height:.2%">MMMMMMMMM</div></div>""")

    async def verify(origin: str) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome")
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(origin, wait_until="networkidle")
            await page.evaluate(
                r"""async()=>{const {fitRenderedManual}=await import('./js/text-fit-dom.js');const image=document.querySelector('img');await image.decode();const manual={pages:[{blocks:[{translation:"That's not pathetic. I understand being unsure...",bbox:[.2033,.101,.205,.0097]},{translation:'MMMMMMMMM',bbox:[0,0,.001,.002]},{translation:'• Some pronunciations may have different intonation, and due to the product’s characteristics some sounds may be difficult to hear; please understand this in advance.\n• If a malfunction occurs, press the reset button and turn the power off and on again.\n• If the QR code on the back of the Progrize Key peels off, becomes scratched, or gets soiled, the QR code may not be readable, so please take care.\n• Do not attach anything other than the Progrize Key series to the DX Hiden Zero-One Driver. Forcibly attaching other items may cause damage.',bbox:[.5844,.4629,.389288651561737,.1292],layout:'panel'}]}]};document.querySelector('[data-block-id=\"2\"]').textContent=manual.pages[0].blocks[2].translation;await fitRenderedManual({root:document.body,manual,generation:0,isCurrentGeneration:()=>true})}"""
            )
            results = TypeAdapter(list[RegionMetric]).validate_python(
                await page.evaluate(METRICS_JS, ".overlay")
            )
            assert not results[0].overflow
            assert results[0].font_px > 8
            by_index = {result.block_index: result for result in results}
            assert by_index[1].overflow
            assert not by_index[2].overflow
            assert by_index[2].font_px > 12
            await browser.close()

    with public_server(tmp_path) as origin:
        asyncio.run(verify(origin))
