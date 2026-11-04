"""Machine translation finishes in the existing overlay editor."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

import pymupdf as fitz

from src.activities.site_generation import generate_site_activity


class SiteHandoffTests(unittest.IsolatedAsyncioTestCase):
    """The completion link must open the real viewer with its editable data."""

    async def test_generated_manual_links_directly_to_overlay_viewer(self) -> None:
        """Keep source/translation/bounds intact and encode the manual hash."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "scan.pdf"
            with fitz.open() as document:
                document.new_page(width=100, height=100)
                document.save(pdf)
            output = root / "manuals" / "ゼイン Driver #1"
            with (
                patch("src.activities.site_generation.activity.logger"),
                patch("src.activities.site_generation.activity.heartbeat"),
                patch("src.activities.site_generation._generate_main_index"),
            ):
                result = await generate_site_activity(
                    str(pdf),
                    [
                        {
                            "original": "ゼイン",
                            "translated": "Zein",
                            "page": 0,
                            "x": 0.1,
                            "y": 0.2,
                            "width": 0.3,
                            "height": 0.1,
                        }
                    ],
                    str(output),
                )
            self.assertTrue(result.success, result.error)
            route, fragment = result.html_path.split("#", 1)
            self.assertEqual(route, "web/index.html")
            self.assertEqual(unquote(fragment), output.name)
            manual = json.loads(Path(result.json_path).read_text())
            self.assertEqual(
                manual["pages"][0]["blocks"][0],
                {"text": "ゼイン", "translation": "Zein", "bbox": [0.1, 0.2, 0.3, 0.1]},
            )
            self.assertTrue((output / "pages/page-0.webp").is_file())
            self.assertFalse((output / "report.html").exists())
