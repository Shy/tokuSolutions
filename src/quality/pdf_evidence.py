"""Bounded extraction of official PDF source evidence with page provenance."""

from pathlib import Path

import pymupdf

from src.quality.core import digest, write_artifact, write_bytes
from src.quality.models import Artifact
from src.quality.ocr_recheck import google_ocr


class PDFPageEvidence(Artifact):
    """Text read independently from one numbered source page."""

    page: int
    method: str
    text: str


class PDFEvidence(Artifact):
    """Content-bound provenance; quotations must match within a single page."""

    sha256: str
    pages: list[PDFPageEvidence]


def ocr_pdf_page(
    document: pymupdf.Document, index: int, cache: Path, root: Path
) -> str:
    """Read a scanned cover using Document AI, independently of agent proposals."""
    page = document[index]
    scale = min(300 / 72, 3200 / max(page.rect.width, page.rect.height))
    pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    content = pixels.tobytes("png")
    path = cache / (digest(content) + ".png")
    write_bytes(path, content)
    return "\n".join(block.text for block in google_ocr(root).recognize(path))


def extract_pdf_evidence(content: bytes, cache: Path, root: Path) -> PDFEvidence:
    """Read up to four text pages and OCR at most the first two scanned pages."""
    if len(content) > 10_000_000 or not content.startswith(b"%PDF-"):
        raise ValueError("Invalid or oversized PDF evidence")
    fingerprint = digest(content)
    path = cache / (fingerprint + ".json")
    if path.exists():
        return PDFEvidence.model_validate_json(path.read_bytes())
    pages = []
    with pymupdf.open(stream=content, filetype="pdf") as document:
        if document.needs_pass:
            raise ValueError("Encrypted PDF evidence is unsupported")
        for index in range(min(len(document), 4)):
            text = document[index].get_text().strip()
            method = "pdf-text"
            if not text and index < 2:
                text = ocr_pdf_page(document, index, cache, root)
                method = "documentai"
            if text:
                pages.append(
                    PDFPageEvidence(page=index + 1, method=method, text=text[:48000])
                )
    evidence = PDFEvidence(sha256=fingerprint, pages=pages)
    write_artifact(path, evidence)
    return evidence
