"""Freeze reusable parser inputs without requiring original source PDFs."""

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from src.quality.layout_inputs import prepare_combined_input
from src.semantic_models import SemanticLayoutInput


def test_scan_fallback_is_canvas_aware_and_rejects_changed_inputs(
    tmp_path: Path,
) -> None:
    """A missing original PDF may use exact scans, with immutable per-page hashes."""
    manual = tmp_path / "manual"
    manual.mkdir()
    image = manual / "page.png"
    Image.new("RGB", (100, 240), "white").save(image)
    source = manual / "translations.json"
    source.write_text(
        json.dumps({"meta": {}, "pages": [{"image": "page.png", "blocks": []}]})
    )
    assets = tmp_path / "assets"
    (assets / "models").mkdir(parents=True)
    (assets / "tessdata").mkdir()
    semantic = SemanticLayoutInput(str(source), str(tmp_path / "run"), 1)
    interrupted = tmp_path / "run/parsing/input/source.pdf"
    interrupted.parent.mkdir(parents=True)
    interrupted.write_bytes(b"interrupted layout-only PDF")
    request = prepare_combined_input(
        semantic, "parsers", Path("/python"), assets, "processor"
    )
    assert request.semantic.pdf_path
    with pymupdf.open(request.semantic.pdf_path) as pdf:
        assert len(pdf) == 1
        assert pdf[0].rect.height / pdf[0].rect.width == pytest.approx(2.4)
    first_manifest = Path(request.parsers[0].manifest_path).read_bytes()
    assert (
        hashlib.sha256(image.read_bytes()).hexdigest()
        == json.loads(first_manifest)["pages"][0]["scan_sha256"]
    )
    resumed = prepare_combined_input(
        semantic, "parsers", Path("/python"), assets, "processor"
    )
    assert resumed == request
    Image.new("RGB", (100, 240), "black").save(image)
    with pytest.raises(ValueError, match="scan changed"):
        prepare_combined_input(
            semantic, "parsers", Path("/python"), assets, "processor"
        )


def test_original_pdf_page_count_is_checked_before_provider_calls(
    tmp_path: Path,
) -> None:
    """A plausible filename cannot authorize mismatched source pages."""
    source = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(source)
    manual = tmp_path / "translations.json"
    Image.new("RGB", (100, 240), "white").save(tmp_path / "page.png")
    manual.write_text(
        json.dumps({"meta": {}, "pages": [{"image": "page.png", "blocks": []}]})
    )
    assets = tmp_path / "assets"
    (assets / "models").mkdir(parents=True)
    (assets / "tessdata").mkdir()
    semantic = SemanticLayoutInput(str(manual), str(tmp_path / "run"), 1, str(source))
    with pytest.raises(ValueError, match="page count"):
        prepare_combined_input(
            semantic, "parsers", Path("/python"), assets, "processor"
        )
