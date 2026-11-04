"""OCR activities using Document AI."""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz  # pymupdf
from dotenv import load_dotenv
from google.cloud import documentai_v1 as documentai
from temporalio import activity

from src.quality.providers import google_credentials, parse_documentai

load_dotenv()

# Config from environment
PROJECT_ID = os.getenv("ProjectID")
DOCAI_LOCATION = os.getenv("Location", "us")
DOCAI_PROCESSOR_ID = os.getenv("ProcessorID")


@dataclass
class TextBlock:
    """A block of text with its position."""

    text: str
    page: int
    x: float  # normalized 0-1
    y: float  # normalized 0-1
    width: float  # normalized 0-1
    height: float  # normalized 0-1
    confidence: float


@dataclass
class PageOCRResult:
    """Result from OCR on a single page."""

    page_num: int
    blocks: list[TextBlock]
    success: bool
    error: str | None = None


@activity.defn
async def get_pdf_page_count_activity(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


@activity.defn
async def ocr_page_activity(pdf_path: str, page_num: int) -> PageOCRResult:
    """
    OCR a single page from a PDF using Document AI.
    Renders the page as an image and sends to Document AI.
    """
    activity.logger.info(f"OCR processing page {page_num} of {pdf_path}")

    return await asyncio.to_thread(_ocr_page_sync, pdf_path, page_num)


def _ocr_page_sync(pdf_path: str, page_num: int) -> PageOCRResult:
    """Run blocking PDF rendering and Document AI calls outside the worker event loop."""
    try:
        # Render page as PNG
        doc = fitz.open(pdf_path)
        page = doc[page_num]

        # Document AI has a 10,000 pixel limit
        # Calculate DPI to stay under limit while maintaining quality
        page_width = page.rect.width
        page_height = page.rect.height
        max_dimension = max(page_width, page_height)

        # Target 200 DPI for good OCR, but scale down if needed
        dpi = 200
        zoom = dpi / 72

        # Check if rendered size would exceed 10k pixels or result in large file
        rendered_width = page_width * zoom
        rendered_height = page_height * zoom
        max_rendered = max(rendered_width, rendered_height)

        # Reduce resolution for large pages to stay under both pixel and file size limits
        # Target: max 7000 pixels for very large pages to keep JPEG under ~3MB
        if max_rendered > 9500:
            # Very large pages need more aggressive scaling
            zoom = 7000 / max_dimension
            dpi = zoom * 72
            activity.logger.info(
                f"Page {page_num}: Large page detected ({page_width:.0f}x{page_height:.0f}), reducing DPI to {dpi:.0f}"
            )

        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        # Get image bytes - use JPEG for large pages to reduce file size
        # Document AI has ~10-20MB file size limits
        image_bytes = pix.tobytes("png")
        mime_type = "image/png"

        # If image is too large, use JPEG compression
        if len(image_bytes) > 10_000_000:  # 10 MB
            image_bytes = pix.tobytes("jpeg", jpg_quality=95)
            mime_type = "image/jpeg"
            activity.logger.info(
                f"Page {page_num}: Using JPEG compression ({len(image_bytes) / 1024 / 1024:.1f} MB)"
            )

        doc.close()

        # Send to Document AI
        credentials = google_credentials(Path(__file__).resolve().parents[2])
        client = documentai.DocumentProcessorServiceClient(credentials=credentials)

        name = f"projects/{PROJECT_ID}/locations/{DOCAI_LOCATION}/processors/{DOCAI_PROCESSOR_ID}"

        raw_document = documentai.RawDocument(
            content=image_bytes,
            mime_type=mime_type,
        )

        request = documentai.ProcessRequest(
            name=name,
            raw_document=raw_document,
            process_options=documentai.ProcessOptions(
                ocr_config=documentai.OcrConfig(
                    hints=documentai.OcrConfig.Hints(language_hints=["ja", "en"])
                )
            ),
        )

        result = client.process_document(request=request)
        document = result.document
        from src.quality.ocr_source import save_ocr_source

        save_ocr_source(document, name, image_bytes, page_num)

        # Use the same source-preserving polygon extraction as the benchmark.
        blocks = [
            TextBlock(
                text=block.text.strip(),
                page=page_num,
                x=block.bbox[0],
                y=block.bbox[1],
                width=block.bbox[2],
                height=block.bbox[3],
                confidence=block.confidence or 0.0,
            )
            for block in parse_documentai(document)
        ]

        activity.logger.info(f"Page {page_num} OCR found {len(blocks)} blocks")

        # Use blocks as-is from Document AI - human intervention can adjust as needed
        return PageOCRResult(
            page_num=page_num,
            blocks=blocks,
            success=True,
        )

    except Exception as e:
        activity.logger.error(f"Page {page_num} OCR failed: {e}")
        raise
