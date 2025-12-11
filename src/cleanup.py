"""Three-stage hybrid translation cleanup: ftfy → rule-based → LLM."""

import os
import re
import json
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class GeminiCleanupResponse(BaseModel):
    """Validated response from Gemini cleanup."""

    remove: list[str] = Field(
        default_factory=list,
        description="Block indices to remove (e.g., ['0-5', '1-3'])",
    )
    corrections: dict[str, str] = Field(
        default_factory=dict,
        description="Block indices mapped to corrected translations",
    )
    product_name: str = Field(
        default="", description="Corrected product name from official source"
    )


class CleanupResult(BaseModel):
    """Result from cleanup operation."""

    success: bool
    original_blocks: int
    cleaned_blocks: int
    removed_blocks: int
    ftfy_fixes: int = 0
    rule_based_removals: int = 0
    llm_corrections: int = 0
    corrected_product_name: Optional[str] = None
    error: Optional[str] = None


def stage1_ftfy_cleanup(translations_data: dict) -> int:
    """
    Stage 1: Fix encoding and OCR text issues using ftfy.

    Args:
        translations_data: The translations.json data (modified in-place)

    Returns:
        Number of blocks fixed
    """
    import ftfy

    fixes = 0
    for page in translations_data["pages"]:
        for block in page["blocks"]:
            original = block["translation"]
            fixed = ftfy.fix_text(original)
            if fixed != original:
                block["translation"] = fixed
                fixes += 1

    return fixes


def stage2_rule_based_cleanup(translations_data: dict) -> int:
    """
    Stage 2: Remove noise blocks using rule-based patterns.

    Removes:
    - Page numbers in parentheses: (1), (2), etc.
    - Lone punctuation marks: ., !, ?, etc.
    - Single letters/numbers
    - Copyright symbols alone: ©, ®, ™
    - Manufacturer names alone: BANDAI
    - Whitespace-only blocks

    Args:
        translations_data: The translations.json data (modified in-place)

    Returns:
        Number of blocks removed
    """
    removed = 0

    # Patterns to remove
    patterns = [
        r"^\(\d+\)$",  # Page numbers: (1), (2), etc.
        r"^[.!?,;:]+$",  # Lone punctuation
        r"^[a-zA-Z0-9]$",  # Single character/digit
        r"^[©®™]+$",  # Copyright symbols alone
        r"^BANDAI$",  # Manufacturer name
        r"^\s*$",  # Whitespace only
    ]

    compiled_patterns = [re.compile(p) for p in patterns]

    for page in translations_data["pages"]:
        blocks_to_keep = []
        for block in page["blocks"]:
            text = block["translation"].strip()

            # Check if text matches any removal pattern
            should_remove = any(pattern.match(text) for pattern in compiled_patterns)

            if not should_remove:
                blocks_to_keep.append(block)
            else:
                removed += 1

        page["blocks"] = blocks_to_keep

    return removed


def stage3_gemini_cleanup(
    translations_data: dict,
    product_name: str = "",
    product_description: str = "",
    model: str = "gemini-2.5-flash",
) -> tuple[int, Optional[str], Optional[str]]:
    """
    Stage 3: LLM-based intelligent cleanup using Google Gemini via AI Studio API.

    Args:
        translations_data: The translations.json data (modified in-place)
        product_name: Official product name from Tokullectibles
        product_description: Product description for context
        model: Gemini model to use (default: gemini-2.5-flash)

    Returns:
        Tuple of (corrections_count, corrected_product_name, error)
    """
    # Use Google AI Studio API (simple API key authentication)
    try:
        import google.generativeai as genai

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return (0, None, "GEMINI_API_KEY or GOOGLE_API_KEY not set in environment")

        genai.configure(api_key=api_key)
    except ImportError:
        return (0, None, "google-generativeai not installed")
    except Exception as e:
        return (0, None, f"Gemini API init failed: {str(e)[:100]}")

    # Build cleanup prompt with product context
    product_context = f"""Product Name: {product_name if product_name else translations_data['meta'].get('manual_name', 'Unknown')}
Product URL: {translations_data['meta'].get('source_url', '')}"""

    if product_description:
        product_context += f"\nProduct Description: {product_description}"

    prompt = f"""You are helping clean up OCR translations of a Japanese toy instruction manual.

{product_context}

Task: Review these translation blocks and provide a JSON response with:
1. "remove": array of indices for blocks that should be removed (any remaining noise like partial text, artifact characters)
2. "corrections": object mapping block indices to corrected translations (fix OCR errors, improve phrasing)
3. "product_name": corrected product name based on the official name from the product page

Rules for removal:
- Remove any remaining noise not caught by earlier cleanup (broken text fragments, artifacts)
- Keep all actual instructions, warnings, feature descriptions, part names, assembly steps

Rules for corrections:
- Fix obvious OCR mistakes (spaces in words, broken characters)
- Improve awkward English phrasing while keeping technical accuracy
- Use official product name terminology from: {product_name}
- Use product context from description to improve terminology accuracy
- Do NOT translate proper nouns or product feature names

Current product name: {translations_data['meta'].get('manual_name', '')}
Official product name: {product_name}

Blocks to review:
"""

    # Sample blocks for review (first 100)
    blocks_sample = []
    block_index = 0
    for page_idx, page in enumerate(translations_data["pages"]):
        for block_idx, block in enumerate(page["blocks"]):
            if block_index >= 100:
                break
            blocks_sample.append(
                {
                    "index": f"{page_idx}-{block_idx}",
                    "original": block["text"],
                    "translation": block["translation"],
                }
            )
            block_index += 1

    prompt += json.dumps(blocks_sample, ensure_ascii=False, indent=2)
    prompt += '\n\nRespond with ONLY valid JSON: {"remove": ["0-5", "1-3"], "corrections": {"0-1": "corrected text"}, "product_name": "Official Name"}'

    try:
        # Call Gemini via AI Studio API
        model_obj = genai.GenerativeModel(model)
        response = model_obj.generate_content(prompt)

        # Parse response
        llm_response = response.text

        # Extract JSON from response
        json_start = llm_response.find("{")
        json_end = llm_response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            raw_json = json.loads(llm_response[json_start:json_end])
        else:
            return (0, None, "Could not parse LLM response")

        # Validate with Pydantic
        try:
            cleanup_data = GeminiCleanupResponse.model_validate(raw_json)
        except Exception as validation_error:
            return (
                0,
                None,
                f"Pydantic validation failed: {str(validation_error)[:100]}",
            )

        # Apply removals
        removed_count = 0
        remove_indices = set(cleanup_data.remove)
        for page_idx in range(len(translations_data["pages"]) - 1, -1, -1):
            page = translations_data["pages"][page_idx]
            for block_idx in range(len(page["blocks"]) - 1, -1, -1):
                index_key = f"{page_idx}-{block_idx}"
                if index_key in remove_indices:
                    page["blocks"].pop(block_idx)
                    removed_count += 1

        # Apply corrections
        corrections_count = 0
        for index_key, corrected_text in cleanup_data.corrections.items():
            page_idx, block_idx = map(int, index_key.split("-"))
            if page_idx < len(translations_data["pages"]):
                page = translations_data["pages"][page_idx]
                if block_idx < len(page["blocks"]):
                    page["blocks"][block_idx]["translation"] = corrected_text
                    corrections_count += 1

        # Get product name
        corrected_name = cleanup_data.product_name.strip()
        if corrected_name and corrected_name != translations_data["meta"].get(
            "manual_name", ""
        ):
            translations_data["meta"]["manual_name"] = corrected_name
            return (corrections_count, corrected_name, None)

        return (corrections_count, None, None)

    except Exception as e:
        return (0, None, str(e))


def cleanup_translations(
    translations_data: dict,
    product_name: str = "",
    product_description: str = "",
    skip_gemini: bool = False,
    gemini_model: str = "gemini-2.5-flash",
) -> CleanupResult:
    """
    Three-stage hybrid cleanup pipeline.

    Stage 1: ftfy - Fix encoding and OCR text issues
    Stage 2: Rule-based - Remove noise (page numbers, symbols, etc.)
    Stage 3: Gemini - LLM-based intelligent corrections (optional)

    Args:
        translations_data: The translations.json data (modified in-place)
        product_name: Official product name from Tokullectibles
        product_description: Product description for LLM context
        skip_gemini: Skip Stage 3 LLM cleanup
        gemini_model: Gemini model name

    Returns:
        CleanupResult with detailed statistics
    """
    original_blocks = sum(len(page["blocks"]) for page in translations_data["pages"])

    try:
        # Stage 1: ftfy text fixing
        ftfy_fixes = stage1_ftfy_cleanup(translations_data)

        # Stage 2: Rule-based removal
        rule_based_removals = stage2_rule_based_cleanup(translations_data)

        # Stage 3: Gemini cleanup (optional)
        llm_corrections = 0
        corrected_name = None
        gemini_error = None

        if not skip_gemini:
            llm_corrections, corrected_name, gemini_error = stage3_gemini_cleanup(
                translations_data, product_name, product_description, gemini_model
            )

        # Update block counts in meta
        cleaned_blocks = sum(len(page["blocks"]) for page in translations_data["pages"])
        translations_data["meta"]["blocks"] = cleaned_blocks

        total_removed = original_blocks - cleaned_blocks

        return CleanupResult(
            success=True,
            original_blocks=original_blocks,
            cleaned_blocks=cleaned_blocks,
            removed_blocks=total_removed,
            ftfy_fixes=ftfy_fixes,
            rule_based_removals=rule_based_removals,
            llm_corrections=llm_corrections,
            corrected_product_name=corrected_name,
            error=gemini_error,  # Non-fatal - Gemini errors don't fail the whole cleanup
        )

    except Exception as e:
        return CleanupResult(
            success=False,
            original_blocks=original_blocks,
            cleaned_blocks=original_blocks,
            removed_blocks=0,
            error=str(e),
        )
