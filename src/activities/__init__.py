"""Activities package - re-exports all activities for backward compatibility."""

# OCR activities and dataclasses
from src.activities.ocr import (
    TextBlock,
    OCRResult,
    PageOCRResult,
    get_pdf_page_count_activity,
    ocr_page_activity,
    ocr_document_activity,
)

# Translation activities and dataclasses
from src.activities.translation import (
    TranslatedBlock,
    TranslationResult,
    translate_blocks_activity,
)

# Site generation activities and dataclasses
from src.activities.site_generation import (
    SiteOutput,
    create_overlay_pdf_activity,
    generate_site_activity,
    _generate_main_index,  # Used by cli.py
)

# Cleanup activities
from src.activities.cleanup import (
    ftfy_cleanup_activity,
    rule_based_cleanup_activity,
    gemini_cleanup_activity,
    cleanup_translations_activity,  # Deprecated, kept for backward compatibility
)

# Product search activities
from src.activities.product_search import (
    search_product_url_activity,
)

__all__ = [
    # OCR
    "TextBlock",
    "OCRResult",
    "PageOCRResult",
    "get_pdf_page_count_activity",
    "ocr_page_activity",
    "ocr_document_activity",
    # Translation
    "TranslatedBlock",
    "TranslationResult",
    "translate_blocks_activity",
    # Site generation
    "SiteOutput",
    "create_overlay_pdf_activity",
    "generate_site_activity",
    "_generate_main_index",
    # Cleanup
    "ftfy_cleanup_activity",
    "rule_based_cleanup_activity",
    "gemini_cleanup_activity",
    "cleanup_translations_activity",
    # Product search
    "search_product_url_activity",
]
