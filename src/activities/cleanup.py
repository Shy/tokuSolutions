"""Cleanup activities for improving translation quality."""

from temporalio import activity


@activity.defn
async def ftfy_cleanup_activity(json_path: str) -> dict:
    """
    Stage 1 cleanup: Fix encoding and OCR text issues using ftfy.

    This is a deterministic operation that fixes Unicode encoding problems
    and common OCR corruption patterns.

    Args:
        json_path: Path to translations.json file

    Returns:
        Dict with fixes count
    """
    from src.cleanup import stage1_ftfy_cleanup
    import json

    activity.logger.info(f"Stage 1 (ftfy): {json_path}")

    try:
        # Load translations.json
        with open(json_path, "r", encoding="utf-8") as f:
            translations_data = json.load(f)

        # Run ftfy cleanup
        fixes = stage1_ftfy_cleanup(translations_data)

        # Save updated file
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(translations_data, f, ensure_ascii=False, indent=2)

        activity.logger.info(f"Stage 1 complete: {fixes} blocks fixed")

        return {
            "success": True,
            "fixes": fixes,
        }

    except Exception as e:
        activity.logger.error(f"Stage 1 failed: {e}")
        return {
            "success": False,
            "fixes": 0,
            "error": str(e),
        }


@activity.defn
async def rule_based_cleanup_activity(json_path: str) -> dict:
    """
    Stage 2 cleanup: Remove noise blocks using rule-based patterns.

    This is a deterministic operation that removes:
    - Page numbers in parentheses: (1), (2), etc.
    - Lone punctuation marks
    - Single letters/numbers
    - Copyright symbols alone
    - Manufacturer names alone

    Args:
        json_path: Path to translations.json file

    Returns:
        Dict with removals count
    """
    from src.cleanup import stage2_rule_based_cleanup
    import json

    activity.logger.info(f"Stage 2 (rule-based): {json_path}")

    try:
        # Load translations.json
        with open(json_path, "r", encoding="utf-8") as f:
            translations_data = json.load(f)

        # Run rule-based cleanup
        removals = stage2_rule_based_cleanup(translations_data)

        # Save updated file
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(translations_data, f, ensure_ascii=False, indent=2)

        activity.logger.info(f"Stage 2 complete: {removals} blocks removed")

        return {
            "success": True,
            "removals": removals,
        }

    except Exception as e:
        activity.logger.error(f"Stage 2 failed: {e}")
        return {
            "success": False,
            "removals": 0,
            "error": str(e),
        }


@activity.defn
async def gemini_cleanup_activity(
    json_path: str,
    product_name: str = "",
    product_description: str = "",
) -> dict:
    """
    Stage 3 cleanup: LLM-based intelligent cleanup using Google Gemini.

    This is a non-deterministic operation with Pydantic validation that:
    - Fixes remaining OCR mistakes
    - Improves awkward English phrasing
    - Uses product context for terminology accuracy
    - Validates responses with Pydantic before applying

    This activity sends heartbeats during LLM processing to keep Temporal informed.

    Args:
        json_path: Path to translations.json file
        product_name: Official product name for context
        product_description: Product description for context

    Returns:
        Dict with corrections count and updated product name
    """
    from src.cleanup import stage3_gemini_cleanup
    import json

    activity.logger.info(f"Stage 3 (Gemini): {json_path}")
    activity.logger.info(f"  Product: {product_name}")

    try:
        # Load translations.json
        with open(json_path, "r", encoding="utf-8") as f:
            translations_data = json.load(f)

        # Send heartbeat before LLM call (can take 10-30 seconds)
        activity.heartbeat()

        # Run Gemini cleanup
        corrections, corrected_name, tags, error = stage3_gemini_cleanup(
            translations_data, product_name, product_description
        )

        # Send heartbeat after LLM call
        activity.heartbeat()

        # Apply tags if Gemini provided them
        if tags:
            translations_data["meta"]["tags"] = tags
            activity.logger.info(f"  Applied tags: {tags}")

        # Save updated file
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(translations_data, f, ensure_ascii=False, indent=2)

        activity.logger.info(f"Stage 3 complete: {corrections} corrections")
        if corrected_name:
            activity.logger.info(f"  Updated product name: {corrected_name}")

        return {
            "success": not error,
            "corrections": corrections,
            "corrected_product_name": corrected_name,
            "tags": tags,
            "error": error,
        }

    except Exception as e:
        activity.logger.error(f"Stage 3 failed: {e}")
        return {
            "success": False,
            "corrections": 0,
            "corrected_product_name": None,
            "tags": None,
            "error": str(e),
        }
