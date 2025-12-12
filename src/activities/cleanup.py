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
            from src.tagging import get_tag_definitions
            translations_data["meta"]["tags"] = tags
            translations_data["meta"]["tag_definitions"] = get_tag_definitions()
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


@activity.defn
async def cleanup_translations_activity(
    json_path: str,
    product_name: str = "",
    product_description: str = "",
    skip_gemini: bool = False,
) -> dict:
    """
    DEPRECATED: Use ftfy_cleanup_activity, rule_based_cleanup_activity,
    and gemini_cleanup_activity separately instead.

    Clean up translations using three-stage hybrid pipeline.

    Stage 1: ftfy - Fix encoding/OCR errors
    Stage 2: Rule-based - Remove noise (page numbers, symbols)
    Stage 3: Gemini - LLM intelligent corrections (optional)

    Args:
        json_path: Path to translations.json file
        product_name: Official product name from Tokullectibles
        product_description: Product description for LLM context
        skip_gemini: Skip Stage 3 LLM cleanup

    Returns:
        Dict with cleanup statistics
    """
    from src.cleanup import cleanup_translations, CleanupResult
    import json

    activity.logger.info(f"Starting cleanup: {json_path}")
    activity.logger.info(f"  Product name: {product_name}")
    if product_description:
        activity.logger.info(f"  Product description: {product_description[:100]}...")
    activity.logger.info(f"  Skip Gemini: {skip_gemini}")

    try:
        # Load translations.json
        with open(json_path, "r", encoding="utf-8") as f:
            translations_data = json.load(f)

        # Run three-stage cleanup
        result = cleanup_translations(
            translations_data,
            product_name=product_name,
            product_description=product_description,
            skip_gemini=skip_gemini,
        )

        if result.success:
            # Save updated translations.json
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(translations_data, f, ensure_ascii=False, indent=2)

            activity.logger.info(
                f"Cleanup complete: {result.original_blocks} → {result.cleaned_blocks} blocks"
            )
            activity.logger.info(f"  Stage 1 (ftfy): {result.ftfy_fixes} fixes")
            activity.logger.info(
                f"  Stage 2 (rules): {result.rule_based_removals} removals"
            )
            if not skip_gemini:
                activity.logger.info(
                    f"  Stage 3 (Gemini): {result.llm_corrections} corrections"
                )

            return {
                "success": True,
                "original_blocks": result.original_blocks,
                "cleaned_blocks": result.cleaned_blocks,
                "removed_blocks": result.removed_blocks,
                "ftfy_fixes": result.ftfy_fixes,
                "rule_based_removals": result.rule_based_removals,
                "llm_corrections": result.llm_corrections,
                "corrected_product_name": result.corrected_product_name,
                "error": result.error,
            }
        else:
            activity.logger.error(f"Cleanup failed: {result.error}")
            return {
                "success": False,
                "original_blocks": result.original_blocks,
                "cleaned_blocks": result.cleaned_blocks,
                "removed_blocks": 0,
                "error": result.error,
            }

    except Exception as e:
        activity.logger.error(f"Cleanup activity failed: {e}")
        return {
            "success": False,
            "original_blocks": 0,
            "cleaned_blocks": 0,
            "removed_blocks": 0,
            "error": str(e),
        }
