"""Document AI activities for OCR with bounding boxes."""

import os
from dataclasses import dataclass
from pathlib import Path

from temporalio import activity
from google.cloud import documentai_v1 as documentai
from google.cloud import translate_v3 as translate
from google.oauth2 import service_account
from dotenv import load_dotenv

import re
import json
import fitz  # pymupdf
from PIL import Image, ImageDraw, ImageFont
import io

from html_template import generate_manual_viewer_html, generate_main_index_html

load_dotenv()

# Config from environment
PROJECT_ID = os.getenv("ProjectID")
DOCAI_LOCATION = os.getenv("Location", "us")
DOCAI_PROCESSOR_ID = os.getenv("ProcessorID")
TRANSLATE_LOCATION = "us-central1"
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
class TranslatedBlock:
    """A text block with translation."""

    original: str
    translated: str
    page: int
    x: float
    y: float
    width: float
    height: float


@dataclass
class TranslationResult:
    """Result from translating OCR blocks."""

    blocks: list[TranslatedBlock]
    success: bool
    error: str | None = None


@dataclass
class SiteOutput:
    """Result from generating static site."""

    output_dir: str
    json_path: str
    html_path: str
    pdf_path: str
    page_count: int
    block_count: int
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


@activity.defn
async def translate_blocks_activity(
    blocks: list[dict], source_lang: str = "ja", target_lang: str = "en"
) -> TranslationResult:
    """
    Translate a list of text blocks.
    """
    activity.logger.info(f"Translating {len(blocks)} blocks")

    try:
        credentials = get_credentials()
        client = translate.TranslationServiceClient(credentials=credentials)
        parent = f"projects/{PROJECT_ID}/locations/{TRANSLATE_LOCATION}"

        translated_blocks = []

        # Batch translate for efficiency (max 1024 segments per request)
        batch_size = 100
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i : i + batch_size]
            texts = [b["text"] for b in batch]

            response = client.translate_text(
                request={
                    "parent": parent,
                    "contents": texts,
                    "source_language_code": source_lang,
                    "target_language_code": target_lang,
                    "mime_type": "text/plain",
                }
            )

            for j, translation in enumerate(response.translations):
                block = batch[j]
                translated_blocks.append(
                    TranslatedBlock(
                        original=block["text"],
                        translated=translation.translated_text,
                        page=block["page"],
                        x=block["x"],
                        y=block["y"],
                        width=block["width"],
                        height=block["height"],
                    )
                )

        activity.logger.info(f"Translation complete: {len(translated_blocks)} blocks")

        return TranslationResult(
            blocks=translated_blocks,
            success=True,
        )

    except Exception as e:
        activity.logger.error(f"Translation failed: {e}")
        return TranslationResult(
            blocks=[],
            success=False,
            error=str(e),
        )


@activity.defn
async def create_overlay_pdf_activity(
    original_pdf: str,
    translated_blocks: list[dict],
    output_path: str,
) -> str:
    """
    Create a PDF with translated text overlaid on the original.
    Uses image-based approach for reliability.
    """
    activity.logger.info(
        f"Creating overlay PDF: {output_path} with {len(translated_blocks)} blocks"
    )

    try:
        doc = fitz.open(original_pdf)
        output_doc = fitz.open()

        # Group blocks by page
        blocks_by_page: dict[int, list[dict]] = {}
        for block in translated_blocks:
            page_num = block["page"]
            if page_num not in blocks_by_page:
                blocks_by_page[page_num] = []
            blocks_by_page[page_num].append(block)

        # Process each page
        dpi = 150  # Balance between quality and size
        zoom = dpi / 72

        for page_num in range(len(doc)):
            page = doc[page_num]

            # Render page to image
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            # Convert to PIL Image
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            draw = ImageDraw.Draw(img)

            img_width, img_height = img.size

            # Draw overlays for this page
            if page_num in blocks_by_page:
                for block in blocks_by_page[page_num]:
                    # Convert normalized coords to pixel coords
                    x = int(block["x"] * img_width)
                    y = int(block["y"] * img_height)
                    width = int(block["width"] * img_width)
                    height = int(block["height"] * img_height)

                    # Skip very small blocks
                    if width < 10 or height < 10:
                        continue

                    # Draw semi-transparent white background (85% opacity = 217/255)
                    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                    overlay_draw = ImageDraw.Draw(overlay)
                    overlay_draw.rectangle(
                        [x, y, x + width, y + height],
                        fill=(255, 255, 255, 217),  # 85% opacity
                    )
                    img = Image.alpha_composite(img.convert("RGBA"), overlay)
                    draw = ImageDraw.Draw(img)

                    # Calculate font size - minimum 10 for readability
                    fontsize = max(10, min(14, int(height * 0.5)))

                    # Draw text with word wrapping
                    text = block["translated"]
                    font = _get_font(fontsize)
                    _draw_wrapped_text(
                        draw, text, x + 2, y + 2, width - 4, height - 4, font, fontsize
                    )

            # Convert back to PDF page
            img_bytes = io.BytesIO()
            img.save(img_bytes, format="PNG")
            img_bytes.seek(0)

            # Create new page with image
            img_doc = fitz.open(stream=img_bytes.read(), filetype="png")
            rect = img_doc[0].rect

            # Create PDF page with same dimensions
            pdf_page = output_doc.new_page(width=rect.width, height=rect.height)
            pdf_page.insert_image(rect, stream=img_bytes.getvalue())

        # Save output
        output_dir = Path(output_path).parent
        output_dir.mkdir(exist_ok=True)
        output_doc.save(output_path)
        output_doc.close()
        doc.close()

        activity.logger.info(f"Overlay PDF created: {output_path}")
        return output_path

    except Exception as e:
        activity.logger.error(f"Failed to create overlay PDF: {e}")
        raise


@activity.defn
async def generate_site_activity(
    original_pdf: str,
    translated_blocks: list[dict],
    output_dir: str,
    source_lang: str = "ja",
    target_lang: str = "en",
) -> SiteOutput:
    """
    Generate a static site with:
    - Page images (WebP)
    - translations.json (editable source of truth)
    - index.html (interactive viewer)
    - document.pdf (overlay PDF for download)
    """
    activity.logger.info(f"Generating static site: {output_dir}")

    try:
        output_path = Path(output_dir)
        pages_dir = output_path / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(original_pdf)
        page_count = len(doc)

        # Group blocks by page
        blocks_by_page: dict[int, list[dict]] = {}
        for idx, block in enumerate(translated_blocks):
            page_num = block["page"]
            if page_num not in blocks_by_page:
                blocks_by_page[page_num] = []
            # Add block ID for referencing in JSON
            block_with_id = {**block, "id": f"b{idx}"}
            blocks_by_page[page_num].append(block_with_id)

        # Render page images
        dpi = 150
        zoom = dpi / 72
        pages_data = []

        for page_num in range(page_count):
            page = doc[page_num]
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            # Save as WebP
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            img_path = pages_dir / f"page-{page_num}.webp"
            img.save(img_path, "WEBP", quality=85)

            # Build page data for JSON with nested bounds structure
            page_blocks = blocks_by_page.get(page_num, [])
            pages_data.append(
                {
                    "image": f"pages/page-{page_num}.webp",
                    "blocks": [
                        {
                            "text": b["original"],
                            "translation": b["translated"],
                            "bbox": [
                                round(b["x"], 4),
                                round(b["y"], 4),
                                round(b["width"], 4),
                                round(b["height"], 4),
                            ],
                        }
                        for b in page_blocks
                    ],
                }
            )

        doc.close()

        # Write translations.json with enhanced metadata
        json_data = {
            "meta": {
                "manual_name": output_path.name,
                "source": str(Path(original_pdf).name),
                "source_lang": source_lang,
                "target_lang": target_lang,
                "source_url": "",  # Can be added later with add-url command
                "pages": len(pages_data),
                "blocks": len(translated_blocks),
                "thumbnail": (
                    pages_data[0]["image"] if pages_data else "pages/page-0.webp"
                ),
            },
            "pages": pages_data,
        }
        json_path = output_path / "translations.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        # Regenerate main index for all manuals
        _generate_main_index(output_path.parent)

        # Note: Per-manual HTML no longer generated
        # Use viewer.html?manual=NAME to view any manual

        activity.logger.info(f"Static site generated: {output_dir}")

        return SiteOutput(
            output_dir=str(output_path),
            json_path=str(json_path),
            html_path=f"viewer.html?manual={output_path.name}",  # Link to dynamic viewer
            pdf_path="",  # No longer generating PDFs
            page_count=page_count,
            block_count=len(translated_blocks),
            success=True,
        )

    except Exception as e:
        activity.logger.error(f"Failed to generate site: {e}")
        return SiteOutput(
            output_dir=output_dir,
            json_path="",
            html_path="",
            pdf_path="",
            page_count=0,
            block_count=0,
            success=False,
            error=str(e),
        )


def _generate_main_index(output_root: Path):
    """Generate meta.json containing all site metadata (manuals list, tags, sitemap data).

    Note: This only updates meta.json. The SPA files (index.html, app.js, styles.css)
    are maintained separately and should not be overwritten.
    """
    manuals = []
    tag_definitions = {}

    # Scan output directory for manual folders
    for folder in sorted(output_root.iterdir()):
        if not folder.is_dir():
            continue

        # Check if it has translations.json
        json_path = folder / "translations.json"
        if not json_path.exists():
            continue

        # Read metadata
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            meta = data.get("meta", {})
            pages = data.get("pages", [])

            # Get first page image for thumbnail
            thumbnail = pages[0]["image"] if pages else "pages/page-0.webp"

            manual_info = {
                "name": folder.name,
                "source": meta.get("source", folder.name),
                "pages": len(pages),
                "blocks": sum(len(p.get("blocks", [])) for p in pages),
                "thumbnail": f"{folder.name}/{thumbnail}",
                "source_url": meta.get("source_url", ""),
            }

            # Add tags if present
            if "tags" in meta:
                manual_info["tags"] = meta["tags"]

            # Collect tag definitions (same for all manuals, just take first one)
            if not tag_definitions and "tag_definitions" in meta:
                tag_definitions = meta["tag_definitions"]

            manuals.append(manual_info)
        except Exception as e:
            print(f"Warning: Could not read {json_path}: {e}")
            continue

    # Build meta.json with everything needed for site
    meta_data = {
        "manuals": manuals,
        "tags": tag_definitions,
        "stats": {
            "total_manuals": len(manuals),
            "total_pages": sum(m["pages"] for m in manuals),
            "total_blocks": sum(m["blocks"] for m in manuals)
        }
    }

    # Write meta.json for fast loading (SPA reads this once)
    meta_path = output_root / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    print(f"Updated meta.json with {len(manuals)} manual(s), {len(tag_definitions)} tag type(s)")


def _generate_html_viewer(data: dict) -> str:
    """Generate an interactive HTML viewer using external template."""
    title = data.get("meta", {}).get("source", "Translated Document")
    source_url = data.get("meta", {}).get("source_url", "")
    return generate_manual_viewer_html(title, source_url)


def _generate_html_viewer_old(data: dict) -> str:
    """OLD VERSION - Generate an interactive HTML viewer with split view."""
    title = data.get("meta", {}).get("source", "Translated Document")
    source_url = data.get("meta", {}).get("source_url", "")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Translated Toy Manuals</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f3eb;
            color: #333;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }}
        header {{
            margin-bottom: 2rem;
        }}
        h1 {{
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
            color: #1a202c;
        }}
        .subtitle {{
            color: #718096;
            font-size: 1rem;
            margin-bottom: 1.5rem;
        }}
        .controls {{
            display: flex;
            gap: 1rem;
            align-items: center;
            margin-bottom: 2rem;
            flex-wrap: wrap;
        }}
        .search-box {{
            flex: 1;
            min-width: 250px;
        }}
        .search-box input {{
            width: 100%;
            padding: 0.6rem 1rem;
            border: 1px solid #cbd5e0;
            border-radius: 6px;
            font-size: 0.95rem;
            transition: border-color 0.2s;
        }}
        .search-box input:focus {{
            outline: none;
            border-color: #4a5568;
        }}
        .stats {{
            color: #718096;
            font-size: 0.9rem;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
            display: block;
        }}
        .card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
        .card-link {{
            text-decoration: none;
            color: inherit;
            display: block;
        }}
        .thumbnail {{
            width: 100%;
            height: 200px;
            object-fit: cover;
            background: #e2e8f0;
        }}
        .card-content {{
            padding: 1rem;
        }}
        .card-title {{
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: #1a202c;
            line-height: 1.4;
        }}
        .card-meta {{
            font-size: 0.85rem;
            color: #718096;
            display: flex;
            gap: 0.75rem;
            margin-bottom: 0.5rem;
            flex-wrap: wrap;
        }}
        .badge {{
            background: #e2e8f0;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 500;
        }}
        .card-actions {{
            display: flex;
            gap: 0.5rem;
            padding-top: 0.5rem;
            border-top: 1px solid #f0f0f0;
        }}
        .card-actions a {{
            font-size: 0.8rem;
            color: #4a5568;
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 0.25rem;
        }}
        .card-actions a:hover {{
            color: #2d3748;
            text-decoration: underline;
        }}
        .no-results {{
            text-align: center;
            padding: 3rem;
            color: #718096;
        }}
        footer {{
            padding-top: 2rem;
            border-top: 1px solid #e2e8f0;
            text-align: center;
            color: #718096;
            font-size: 0.875rem;
        }}
        .hidden {{
            display: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Translated Toy Manuals</h1>
            <p class="subtitle">Japanese instruction manuals translated to English using Document AI</p>
            <div class="controls">
                <div class="search-box">
                    <input type="text" id="searchInput" placeholder="Search manuals by name..." />
                </div>
                <div class="stats">
                    <span id="statsDisplay">{len(manuals)} manuals</span>
                </div>
            </div>
        </header>

        <div class="grid" id="manualsGrid"></div>

        <div class="no-results hidden" id="noResults">
            No manuals found matching your search.
        </div>

        <footer>
            <p>Generated by Document AI Translation System</p>
        </footer>
    </div>

    <script>
        const manuals = {manuals_json};
        const grid = document.getElementById('manualsGrid');
        const searchInput = document.getElementById('searchInput');
        const statsDisplay = document.getElementById('statsDisplay');
        const noResults = document.getElementById('noResults');

        function renderManuals(filteredManuals) {{
            grid.innerHTML = '';

            if (filteredManuals.length === 0) {{
                grid.classList.add('hidden');
                noResults.classList.remove('hidden');
                statsDisplay.textContent = '0 manuals';
                return;
            }}

            grid.classList.remove('hidden');
            noResults.classList.add('hidden');

            filteredManuals.forEach(manual => {{
                const card = document.createElement('div');
                card.className = 'card';

                const sourceLink = manual.source_url
                    ? `<a href="${{manual.source_url}}" target="_blank" rel="noopener">📄 Original</a>`
                    : '';

                card.innerHTML = `
                    <a href="${{manual.url}}" class="card-link">
                        <img src="${{manual.thumbnail}}" alt="${{manual.source}}" class="thumbnail" loading="lazy">
                        <div class="card-content">
                            <div class="card-title">${{manual.source}}</div>
                            <div class="card-meta">
                                <span class="badge">${{manual.pages}} pages</span>
                                <span class="badge">${{manual.blocks}} blocks</span>
                            </div>
                            ${{sourceLink ? `<div class="card-actions">${{sourceLink}}</div>` : ''}}
                        </div>
                    </a>
                `;
                grid.appendChild(card);
            }});

            statsDisplay.textContent = `${{filteredManuals.length}} manual${{filteredManuals.length === 1 ? '' : 's'}}`;
        }}

        function filterManuals(query) {{
            const lowerQuery = query.toLowerCase();
            return manuals.filter(manual =>
                manual.source.toLowerCase().includes(lowerQuery) ||
                manual.name.toLowerCase().includes(lowerQuery)
            );
        }}

        searchInput.addEventListener('input', (e) => {{
            const filtered = filterManuals(e.target.value);
            renderManuals(filtered);
        }});

        // Initial render
        renderManuals(manuals);
    </script>
</body>
</html>
"""

    # Write index.html
    index_path = output_root / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_html)

    print(f"Main index generated: {index_path}")


def _generate_html_viewer(data: dict) -> str:
    """Generate an interactive HTML viewer with split view."""
    title = data.get("meta", {}).get("source", "Translated Document")
    source_url = data.get("meta", {}).get("source_url", "")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f3eb;
            color: #333;
            min-height: 100vh;
        }}
        header {{
            background: #fff;
            padding: 0.75rem 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
            border-bottom: 1px solid #ddd;
        }}
        h1 {{ font-size: 1.25rem; font-weight: 600; color: #333; }}
        .controls {{
            display: flex;
            gap: 1rem;
            align-items: center;
        }}
        .controls label {{
            display: flex;
            align-items: center;
            gap: 0.4rem;
            cursor: pointer;
            font-size: 0.85rem;
        }}
        .btn {{
            background: #4a5568;
            color: white;
            border: none;
            padding: 0.4rem 0.8rem;
            border-radius: 4px;
            cursor: pointer;
            text-decoration: none;
            font-size: 0.85rem;
        }}
        .btn:hover {{ background: #2d3748; }}
        .btn-primary {{ background: #3182ce; }}
        .btn-primary:hover {{ background: #2c5282; }}

        .container {{
            display: flex;
            height: calc(100vh - 50px);
        }}

        /* Left panel - Page viewer */
        .page-panel {{
            flex: 1;
            overflow-y: auto;
            padding: 1.5rem;
            background: #eae8e0;
        }}
        .page {{
            position: relative;
            margin-bottom: 1.5rem;
            background: #fff;
            border-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .page img {{
            width: 100%;
            display: block;
            border-radius: 4px;
        }}
        .overlay {{
            position: absolute;
            background: rgba(255,255,255,0.85);
            padding: 2px;
            font-size: 10px;
            line-height: 1.15;
            color: #000;
            cursor: pointer;
            transition: all 0.15s;
            border: 1px solid transparent;
            overflow: hidden;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        .overlay:hover, .overlay.highlight {{
            background: rgba(255,248,220,0.98);
            border-color: #d69e2e;
            z-index: 20;
        }}
        .overlay.highlight {{
            box-shadow: 0 0 0 2px #d69e2e;
        }}
        .page-label {{
            position: absolute;
            bottom: 8px;
            right: 8px;
            background: rgba(0,0,0,0.6);
            color: #fff;
            padding: 3px 10px;
            border-radius: 3px;
            font-size: 0.8rem;
        }}

        /* Right panel - Text list */
        .text-panel {{
            width: 380px;
            min-width: 320px;
            background: #fff;
            border-left: 1px solid #ddd;
            display: flex;
            flex-direction: column;
        }}
        .text-panel-header {{
            padding: 0.75rem 1rem;
            border-bottom: 1px solid #eee;
            background: #fafafa;
        }}
        .text-panel-header h2 {{
            font-size: 0.9rem;
            font-weight: 600;
            color: #555;
        }}
        .page-nav {{
            display: flex;
            gap: 0.5rem;
            margin-top: 0.5rem;
            flex-wrap: wrap;
        }}
        .page-nav button {{
            padding: 0.25rem 0.5rem;
            font-size: 0.75rem;
            background: #eee;
            border: 1px solid #ddd;
            border-radius: 3px;
            cursor: pointer;
        }}
        .page-nav button:hover {{ background: #ddd; }}
        .page-nav button.active {{ background: #3182ce; color: white; border-color: #3182ce; }}

        .text-list {{
            flex: 1;
            overflow-y: auto;
            padding: 0.5rem;
        }}
        .text-entry {{
            padding: 0.6rem 0.75rem;
            margin-bottom: 0.4rem;
            background: #fafafa;
            border-radius: 4px;
            border: 1px solid #eee;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .text-entry:hover, .text-entry.highlight {{
            background: #fffbeb;
            border-color: #d69e2e;
        }}
        .text-entry.highlight {{
            box-shadow: 0 0 0 2px #d69e2e;
        }}
        .original {{
            font-size: 0.8rem;
            color: #666;
            margin-bottom: 0.25rem;
            padding-bottom: 0.25rem;
            border-bottom: 1px dashed #ddd;
        }}
        .translated {{
            font-size: 0.85rem;
            color: #222;
        }}
        .hidden {{ display: none !important; }}
    </style>
</head>
<body>
    <header>
        <div style="display: flex; align-items: center; gap: 0.5rem;">
            <a href="../index.html" class="btn" style="background: #718096;">← Back</a>
            <h1>{title}</h1>
        </div>
        <div class="controls">
            <label>
                <input type="checkbox" id="showOverlays" checked>
                Show Overlays
            </label>
            {f'<a href="{source_url}" target="_blank" rel="noopener" class="btn" style="background: #2d3748;">📄 Original</a>' if source_url else ''}
            <a href="translations.json" class="btn">JSON</a>
        </div>
    </header>

    <div class="container">
        <div class="page-panel" id="pagePanel"></div>
        <div class="text-panel">
            <div class="text-panel-header">
                <h2>Translations</h2>
                <div class="page-nav" id="pageNav"></div>
            </div>
            <div class="text-list" id="textList"></div>
        </div>
    </div>

    <script>
        const data = {json.dumps(data, ensure_ascii=False)};
        let currentPage = 0;

        const pagePanel = document.getElementById('pagePanel');
        const textList = document.getElementById('textList');
        const pageNav = document.getElementById('pageNav');
        const showOverlaysCheckbox = document.getElementById('showOverlays');

        // Clean text: collapse multiple spaces but preserve line breaks
        function cleanText(text) {{
            return text.replace(/[^\\S\\n]+/g, ' ').trim();
        }}

        // Format text for overlay: split at bullet points for better wrapping
        function formatOverlayText(text) {{
            // Replace bullet markers with newline + marker for better line breaks
            return text
                .replace(/[^\\S\\n]+/g, ' ')  // collapse spaces but keep newlines
                .replace(/\\s*([・•])/g, '\\n$1')  // newline before bullets
                .trim();
        }}

        function renderPageNav() {{
            pageNav.innerHTML = '';
            data.pages.forEach((page, idx) => {{
                const btn = document.createElement('button');
                btn.textContent = idx + 1;
                btn.className = idx === currentPage ? 'active' : '';
                btn.onclick = () => goToPage(idx);
                pageNav.appendChild(btn);
            }});
        }}

        function goToPage(idx) {{
            currentPage = idx;
            renderPageNav();
            renderTextList();
            // Scroll to page
            const pageEl = document.getElementById(`page-${{idx}}`);
            if (pageEl) pageEl.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }}

        function renderPages() {{
            pagePanel.innerHTML = '';
            data.pages.forEach((page, idx) => {{
                const pageDiv = document.createElement('div');
                pageDiv.className = 'page';
                pageDiv.id = `page-${{idx}}`;

                const img = document.createElement('img');
                img.src = page.image;
                img.alt = `Page ${{idx + 1}}`;
                img.onload = () => renderOverlays(pageDiv, page, idx);

                const label = document.createElement('div');
                label.className = 'page-label';
                label.textContent = `${{idx + 1}} / ${{data.pages.length}}`;

                pageDiv.appendChild(img);
                pageDiv.appendChild(label);
                pagePanel.appendChild(pageDiv);
            }});
        }}

        function renderOverlays(container, page, pageIdx) {{
            container.querySelectorAll('.overlay').forEach(el => el.remove());

            page.blocks.forEach((block, blockIdx) => {{
                const overlay = document.createElement('div');
                overlay.className = 'overlay';
                overlay.dataset.page = pageIdx;
                overlay.dataset.block = blockIdx;

                overlay.style.left = (block.bounds.x * 100) + '%';
                overlay.style.top = (block.bounds.y * 100) + '%';
                overlay.style.width = (block.bounds.w * 100) + '%';
                overlay.style.height = (block.bounds.h * 100) + '%';

                const text = formatOverlayText(block.translated);
                overlay.textContent = text;

                // Auto-shrink font to fit in box
                requestAnimationFrame(() => {{
                    let fontSize = 12;
                    const minFontSize = 6;
                    overlay.style.fontSize = fontSize + 'px';

                    while (fontSize > minFontSize &&
                           (overlay.scrollHeight > overlay.clientHeight ||
                            overlay.scrollWidth > overlay.clientWidth)) {{
                        fontSize -= 0.5;
                        overlay.style.fontSize = fontSize + 'px';
                    }}
                }});

                overlay.addEventListener('mouseenter', () => highlightEntry(pageIdx, blockIdx));
                overlay.addEventListener('mouseleave', () => clearHighlights());
                overlay.addEventListener('click', () => {{
                    currentPage = pageIdx;
                    renderPageNav();
                    renderTextList();
                    scrollToEntry(blockIdx);
                }});

                container.appendChild(overlay);
            }});
        }}

        function renderTextList() {{
            textList.innerHTML = '';
            const page = data.pages[currentPage];
            if (!page) return;

            page.blocks.forEach((block, blockIdx) => {{
                const entry = document.createElement('div');
                entry.className = 'text-entry';
                entry.dataset.page = currentPage;
                entry.dataset.block = blockIdx;

                entry.innerHTML = `
                    <div class="original">${{escapeHtml(block.original)}}</div>
                    <div class="translated">${{escapeHtml(cleanText(block.translated))}}</div>
                `;

                entry.addEventListener('mouseenter', () => highlightOverlay(currentPage, blockIdx));
                entry.addEventListener('mouseleave', () => clearHighlights());

                textList.appendChild(entry);
            }});
        }}

        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}

        function highlightEntry(pageIdx, blockIdx) {{
            clearHighlights();
            if (pageIdx !== currentPage) return;
            const entry = textList.querySelector(`[data-page="${{pageIdx}}"][data-block="${{blockIdx}}"]`);
            if (entry) {{
                entry.classList.add('highlight');
                entry.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
            }}
        }}

        function highlightOverlay(pageIdx, blockIdx) {{
            clearHighlights();
            const overlay = pagePanel.querySelector(`[data-page="${{pageIdx}}"][data-block="${{blockIdx}}"]`);
            if (overlay) {{
                overlay.classList.add('highlight');
            }}
            const entry = textList.querySelector(`[data-page="${{pageIdx}}"][data-block="${{blockIdx}}"]`);
            if (entry) entry.classList.add('highlight');
        }}

        function clearHighlights() {{
            document.querySelectorAll('.highlight').forEach(el => el.classList.remove('highlight'));
        }}

        function scrollToEntry(blockIdx) {{
            const entry = textList.querySelector(`[data-block="${{blockIdx}}"]`);
            if (entry) entry.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        }}

        showOverlaysCheckbox.addEventListener('change', (e) => {{
            document.querySelectorAll('.overlay').forEach(el => {{
                el.classList.toggle('hidden', !e.target.checked);
            }});
        }});

        // Initialize
        renderPages();
        renderPageNav();
        renderTextList();

        // Update current page on scroll
        pagePanel.addEventListener('scroll', () => {{
            const pages = pagePanel.querySelectorAll('.page');
            for (let i = pages.length - 1; i >= 0; i--) {{
                const rect = pages[i].getBoundingClientRect();
                if (rect.top < 200) {{
                    if (currentPage !== i) {{
                        currentPage = i;
                        renderPageNav();
                        renderTextList();
                    }}
                    break;
                }}
            }}
        }});
    </script>
</body>
</html>"""


def _get_font(size: int):
    """Get a font at the given size, trying several options."""
    # Try Arial first (usually available on macOS)
    font_paths = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _detect_list_item(text: str) -> tuple[str, str]:
    """
    Detect if text starts with a list marker.
    Returns (marker, rest_of_text) or ("", text) if no marker.
    """
    # Numbered lists: 1. 2. ① ② (1) etc.
    numbered = re.match(r"^(\d+[\.\)]\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*|\(\d+\)\s*)", text)
    if numbered:
        return numbered.group(), text[numbered.end() :]

    # Bullet points: ・ • - ● ○ ■ □ ★ ☆ ※
    bullet = re.match(r"^([・•\-●○■□★☆※]\s*)", text)
    if bullet:
        return bullet.group(), text[bullet.end() :]

    return "", text


def _split_list_items(text: str) -> list[str]:
    """
    Split text that contains multiple list items into separate items.
    e.g., "1. First 2. Second" -> ["1. First", "2. Second"]
    """
    # Pattern to find list markers that appear mid-text
    # Match: space or start, then number+dot/paren, or bullet characters
    pattern = r"(?:^|(?<=\s))(\d+[\.\)]\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*|\(\d+\)\s*|[・•●○■□★☆※]\s*)"

    # Find all matches with positions
    matches = list(re.finditer(pattern, text))

    if len(matches) <= 1:
        return [text]

    # Split at each marker position
    items = []
    for i, match in enumerate(matches):
        start = match.start()
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(text)

        item = text[start:end].strip()
        if item:
            items.append(item)

    return items if items else [text]


def _draw_wrapped_text(
    draw,
    text: str,
    x: int,
    y: int,
    max_width: int,
    max_height: int,
    font,
    fontsize: int,
):
    """Draw text with word wrapping, auto-shrinking font if needed."""

    def get_text_width(txt, fnt):
        """Get width of text using getbbox."""
        try:
            bbox = fnt.getbbox(txt)
            return bbox[2] - bbox[0] if bbox else len(txt) * fontsize // 2
        except Exception:
            return len(txt) * fontsize // 2

    def wrap_single_item(txt, fnt, mw):
        """Wrap a single text item (possibly with list marker)."""
        marker, content = _detect_list_item(txt)
        indent = get_text_width(marker, fnt) if marker else 0

        words = content.split()
        lines = []
        current_line = []
        first_line = True

        for word in words:
            test_line = " ".join(current_line + [word])
            effective_width = mw if first_line else mw - indent
            line_width = get_text_width(test_line, fnt)

            if line_width <= effective_width:
                current_line.append(word)
            else:
                if current_line:
                    if first_line and marker:
                        lines.append((marker + " ".join(current_line), 0))
                        first_line = False
                    else:
                        lines.append((" ".join(current_line), indent if marker else 0))
                current_line = [word]

        if current_line:
            if first_line and marker:
                lines.append((marker + " ".join(current_line), 0))
            else:
                lines.append((" ".join(current_line), indent if marker else 0))

        return lines

    def wrap_text_with_lists(txt, fnt, mw):
        """Wrap text, splitting multiple list items onto separate lines."""
        # First split into separate list items
        items = _split_list_items(txt)

        all_lines = []
        for item in items:
            item_lines = wrap_single_item(item, fnt, mw)
            all_lines.extend(item_lines)

        return all_lines

    # Try progressively smaller fonts until text fits
    current_fontsize = fontsize
    min_fontsize = 8  # Increased minimum for readability

    while current_fontsize >= min_fontsize:
        font = _get_font(current_fontsize)
        lines = wrap_text_with_lists(text, font, max_width)
        line_height = current_fontsize + 2
        total_height = len(lines) * line_height

        if total_height <= max_height:
            break

        current_fontsize -= 1

    # Draw lines
    line_height = current_fontsize + 2
    current_y = y

    for line_text, indent in lines:
        try:
            draw.text((x + indent, current_y), line_text, fill=(0, 0, 0), font=font)
        except Exception:
            draw.text((x + indent, current_y), line_text, fill=(0, 0, 0))
        current_y += line_height


# Quick test function
async def test_ocr(pdf_path: str):
    """Test OCR on a PDF."""
    result = await ocr_document_activity(pdf_path)
    print(f"Success: {result.success}")
    print(f"Pages: {result.pages}")
    print(f"Blocks: {len(result.blocks)}")
    if result.blocks:
        print(f"\nFirst 5 blocks:")
        for block in result.blocks[:5]:
            print(
                f"  Page {block.page}: '{block.text[:50]}...' @ ({block.x:.2f}, {block.y:.2f})"
            )
    if result.error:
        print(f"Error: {result.error}")
    return result


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) > 1:
        asyncio.run(test_ocr(sys.argv[1]))
    else:
        print("Usage: python docai_activities.py <pdf_path>")
