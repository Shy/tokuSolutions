"""Site generation activities for creating static HTML viewers."""

import os
from dataclasses import dataclass
from pathlib import Path

from temporalio import activity
from dotenv import load_dotenv

import re
import json
import fitz  # pymupdf
from PIL import Image, ImageDraw, ImageFont
import io

from src.html_template import generate_manual_viewer_html, generate_main_index_html
from src.tagging import get_tag_definitions

load_dotenv()


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
    product_url: str = "",
    blog_url: str = "",
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
            # Send heartbeat every 5 pages to keep Temporal informed
            if page_num % 5 == 0:
                activity.heartbeat()

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

        # Tags will be generated by Gemini cleanup with full manual context
        # This provides more accurate tagging than just the product name
        manual_name = output_path.name
        tag_definitions = get_tag_definitions()

        # Write translations.json with enhanced metadata
        json_data = {
            "meta": {
                "manual_name": manual_name,
                "source": str(Path(original_pdf).name),
                "source_lang": source_lang,
                "target_lang": target_lang,
                "source_url": product_url,  # From product search activity
                "blog_url": blog_url,  # Bandai technical blog (translated)
                "pages": len(pages_data),
                "blocks": len(translated_blocks),
                "thumbnail": (
                    pages_data[0]["image"] if pages_data else "pages/page-0.webp"
                ),
                "tags": [],  # Populated by Gemini cleanup activity
                "tag_definitions": tag_definitions,
            },
            "pages": pages_data,
        }
        json_path = output_path / "translations.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        # Regenerate main index for all manuals
        # manuals are in manuals/, meta.json goes to web/
        manuals_root = Path("manuals")
        _generate_main_index(manuals_root)

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
                "blog_url": meta.get("blog_url", ""),
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
            "total_blocks": sum(m["blocks"] for m in manuals),
        },
    }

    # Write meta.json to web/ for fast loading (SPA reads this once)
    web_path = Path("web")
    web_path.mkdir(exist_ok=True)
    meta_path = web_path / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    print(
        f"Updated web/meta.json with {len(manuals)} manual(s), {len(tag_definitions)} tag type(s)"
    )


def _generate_html_viewer(data: dict) -> str:
    """Generate an interactive HTML viewer using external template."""
    title = data.get("meta", {}).get("source", "Translated Document")
    source_url = data.get("meta", {}).get("source_url", "")
    return generate_manual_viewer_html(title, source_url)


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
