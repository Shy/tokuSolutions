"""Freeze canvas-aware inputs for the same combined pipeline on every manual."""

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pymupdf
from PIL import Image
from pydantic import BaseModel

from src.combined_models import CombinedLayoutInput
from src.layout_models import LayoutBenchmarkInput
from src.quality.cleanup_context import ContextManual
from src.quality.core import write_bytes
from src.semantic_models import SemanticLayoutInput


class _Page(BaseModel):
    page: int
    pdf: str
    sha256: str
    scan_sha256: str


class _Manifest(BaseModel):
    source: str
    source_sha256: str
    pages: list[_Page]
    docling_assets: str


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_combined_input(
    semantic: SemanticLayoutInput,
    parser_queue: str,
    docling_python: Path,
    docling_assets: Path,
    google_processor: str,
) -> CombinedLayoutInput:
    """Freeze original PDF pages, or build a layout-only PDF from exact scans."""
    if not all((docling_assets / folder).is_dir() for folder in ("models", "tessdata")):
        raise ValueError("Docling requires the existing models and tessdata assets")
    manual_path = Path(semantic.json_path)
    manual = ContextManual.model_validate_json(manual_path.read_bytes())
    if len(manual.pages) != semantic.page_count:
        raise ValueError("Semantic input page count mismatch")
    directory = Path(semantic.run_dir) / "parsing"
    inputs = directory / "input"
    inputs.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        manifest = _Manifest.model_validate_json(manifest_path.read_bytes())
        if len(manifest.pages) != len(manual.pages):
            raise ValueError("Frozen parser page count changed")
        if manifest.docling_assets != str(docling_assets.resolve()):
            raise ValueError("Frozen parser assets changed")
        if _hash(Path(manifest.source)) != manifest.source_sha256:
            raise ValueError("Frozen parser source changed")
        for page, record in zip(manual.pages, manifest.pages, strict=True):
            if _hash(manual_path.parent / page.image) != record.scan_sha256:
                raise ValueError("Frozen parser scan changed")
            if _hash(Path(record.pdf)) != record.sha256:
                raise ValueError("Frozen parser page changed")
    else:
        source = Path(semantic.pdf_path) if semantic.pdf_path else inputs / "source.pdf"
        if not semantic.pdf_path or not source.is_file():
            source = inputs / "source.pdf"
            with pymupdf.open() as document:
                for page in manual.pages:
                    with Image.open(manual_path.parent / page.image) as scan:
                        pixels = io.BytesIO()
                        scan.convert("RGB").save(pixels, format="PNG")
                        canvas = document.new_page(width=scan.width, height=scan.height)
                        canvas.insert_image(canvas.rect, stream=pixels.getvalue())
                write_bytes(source, document.tobytes())
        records = []
        with pymupdf.open(source) as document:
            if len(document) != semantic.page_count:
                raise ValueError("Original PDF page count does not match manual")
            for index, page in enumerate(manual.pages):
                original = document[index]
                with Image.open(manual_path.parent / page.image) as scan:
                    if (
                        abs(
                            (original.rect.width / original.rect.height)
                            / (scan.width / scan.height)
                            - 1
                        )
                        > 0.02
                    ):
                        raise ValueError(
                            "Original PDF canvas does not match manual scan"
                        )
                target = inputs / f"page-{index + 1:04d}.pdf"
                with pymupdf.open() as single:
                    single.insert_pdf(document, from_page=index, to_page=index)
                    write_bytes(target, single.tobytes())
                records.append(
                    {
                        "page": index + 1,
                        "pdf": str(target.resolve()),
                        "sha256": _hash(target),
                        "scan_sha256": _hash(manual_path.parent / page.image),
                        "width": original.rect.width,
                        "height": original.rect.height,
                        "native_characters": len(original.get_text()),
                        "vector_paths": len(original.get_drawings()),
                    }
                )
        value = {
            "source": str(source.resolve()),
            "source_sha256": _hash(source),
            "docling_assets": str(docling_assets.resolve()),
            "pages": records,
            "source_kind": "original_pdf"
            if semantic.pdf_path and source == Path(semantic.pdf_path)
            else "published_scans",
        }
        write_bytes(manifest_path, (json.dumps(value, indent=2) + "\n").encode())
        manifest = _Manifest.model_validate(value)
    jobs = tuple(
        LayoutBenchmarkInput(
            str(manifest_path.resolve()),
            str((directory / provider).resolve()),
            str(docling_python.absolute()),
            providers=(provider,),
            google_processor=google_processor if provider == "google" else "",
        )
        for provider in ("docling", "google")
    )
    return CombinedLayoutInput(
        replace(semantic, pdf_path=manifest.source), jobs, parser_queue
    )
