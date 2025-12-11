"""OCR activities using Document AI."""

import os
from dataclasses import dataclass

from temporalio import activity
from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account
from dotenv import load_dotenv

import re
import fitz  # pymupdf

load_dotenv()

# Config from environment
PROJECT_ID = os.getenv("ProjectID")
DOCAI_LOCATION = os.getenv("Location", "us")
DOCAI_PROCESSOR_ID = os.getenv("ProcessorID")
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH")


def get_credentials():
    """Load Google Cloud credentials."""
    if not CREDENTIALS_PATH:
        raise ValueError(
            "CREDENTIALS_PATH environment variable not set. "
            "Please add it to your .env file."
        )
    return service_account.Credentials.from_service_account_file(CREDENTIALS_PATH)


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
class OCRResult:
    """Result from Document AI OCR."""

    pdf_path: str
    pages: int
    blocks: list[TextBlock]
    full_text: str
    success: bool
    error: str | None = None


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

    try:
        # Render page as PNG
        doc = fitz.open(pdf_path)
        page = doc[page_num]

        # Render at 200 DPI for good OCR quality
        dpi = 200
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        # Get PNG bytes
        png_bytes = pix.tobytes("png")
        doc.close()

        # Send to Document AI
        credentials = get_credentials()
        client = documentai.DocumentProcessorServiceClient(credentials=credentials)

        name = f"projects/{PROJECT_ID}/locations/{DOCAI_LOCATION}/processors/{DOCAI_PROCESSOR_ID}"

        raw_document = documentai.RawDocument(
            content=png_bytes,
            mime_type="image/png",
        )

        request = documentai.ProcessRequest(
            name=name,
            raw_document=raw_document,
        )

        result = client.process_document(request=request)
        document = result.document

        # Extract text using blocks from Document AI
        blocks = []
        for doc_page in document.pages:
            for block in doc_page.blocks:
                vertices = block.layout.bounding_poly.normalized_vertices
                if len(vertices) >= 4:
                    x = vertices[0].x
                    y = vertices[0].y
                    width = vertices[2].x - vertices[0].x
                    height = vertices[2].y - vertices[0].y

                    text = _get_text_from_layout(block.layout, document.text)

                    if text.strip() and not _should_skip_block(text):
                        blocks.append(
                            TextBlock(
                                text=text.strip(),
                                page=page_num,
                                x=x,
                                y=y,
                                width=width,
                                height=height,
                                confidence=block.layout.confidence,
                            )
                        )

        activity.logger.info(f"Page {page_num} OCR found {len(blocks)} blocks")

        # Use blocks as-is from Document AI - human intervention can adjust as needed
        return PageOCRResult(
            page_num=page_num,
            blocks=blocks,
            success=True,
        )

    except Exception as e:
        activity.logger.error(f"Page {page_num} OCR failed: {e}")
        return PageOCRResult(
            page_num=page_num,
            blocks=[],
            success=False,
            error=str(e),
        )


@activity.defn
async def ocr_document_activity(pdf_path: str) -> OCRResult:
    """
    Use Document AI to OCR a PDF and extract text with bounding boxes.
    """
    activity.logger.info(f"OCR processing: {pdf_path}")

    try:
        credentials = get_credentials()
        client = documentai.DocumentProcessorServiceClient(credentials=credentials)

        # Read PDF
        with open(pdf_path, "rb") as f:
            content = f.read()

        # Process with Document AI
        name = f"projects/{PROJECT_ID}/locations/{DOCAI_LOCATION}/processors/{DOCAI_PROCESSOR_ID}"

        raw_document = documentai.RawDocument(
            content=content,
            mime_type="application/pdf",
        )

        request = documentai.ProcessRequest(
            name=name,
            raw_document=raw_document,
        )

        result = client.process_document(request=request)
        document = result.document

        # Extract text blocks with positions
        blocks = []
        for page_idx, page in enumerate(document.pages):
            for block in page.blocks:
                # Get bounding box (normalized coordinates)
                vertices = block.layout.bounding_poly.normalized_vertices
                if len(vertices) >= 4:
                    x = vertices[0].x
                    y = vertices[0].y
                    width = vertices[2].x - vertices[0].x
                    height = vertices[2].y - vertices[0].y

                    # Extract text for this block
                    text = _get_text_from_layout(block.layout, document.text)

                    # Skip blocks that shouldn't be translated (single numbers, symbols, etc.)
                    if text.strip() and not _should_skip_block(text):
                        blocks.append(
                            TextBlock(
                                text=text.strip(),
                                page=page_idx,
                                x=x,
                                y=y,
                                width=width,
                                height=height,
                                confidence=block.layout.confidence,
                            )
                        )

        activity.logger.info(f"OCR complete: {len(blocks)} blocks found")

        return OCRResult(
            pdf_path=pdf_path,
            pages=len(document.pages),
            blocks=blocks,
            full_text=document.text,
            success=True,
        )

    except Exception as e:
        activity.logger.error(f"OCR failed: {e}")
        return OCRResult(
            pdf_path=pdf_path,
            pages=0,
            blocks=[],
            full_text="",
            success=False,
            error=str(e),
        )


def _get_text_from_layout(layout, full_text: str) -> str:
    """Extract text from a layout element using text anchors."""
    text = ""
    for segment in layout.text_anchor.text_segments:
        start = int(segment.start_index) if segment.start_index else 0
        end = int(segment.end_index)
        text += full_text[start:end]
    return text


def _should_skip_block(text: str) -> bool:
    """
    Determine if a text block should be skipped (not translated).
    Skip: single numbers, lone symbols, single letters, short all-caps English.
    """
    text = text.strip()

    # Skip empty
    if not text:
        return True

    # Skip single numbers (including circled numbers like ①②③)
    if re.match(r"^[\d①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]+$", text):
        return True

    # Skip lone punctuation or symbols
    if re.match(r"^[・•\-●○■□★☆※→←↑↓↔▲▼◆◇]+$", text):
        return True

    # Skip very short text that's just a number with punctuation (e.g., "3.", "①")
    if re.match(r"^[\d①②③④⑤⑥⑦⑧⑨⑩]+[\.\)）:：]?$", text):
        return True

    # Skip copyright symbols alone
    if re.match(r"^[©®™]+$", text):
        return True

    # Skip single Latin letters (A-Z, a-z)
    if re.match(r"^[A-Za-z]$", text):
        return True

    # Skip short all-caps English words (1-4 chars) - likely labels/abbreviations
    if re.match(r"^[A-Z]{1,4}$", text):
        return True

    return False
