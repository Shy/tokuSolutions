"""Document AI based translation workflow."""

import asyncio
from datetime import timedelta
from dataclasses import dataclass

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.activities import (
        OCRResult,
        PageOCRResult,
        TextBlock,
        TranslationResult,
        SiteOutput,
        ocr_document_activity,
        get_pdf_page_count_activity,
        ocr_page_activity,
        translate_blocks_activity,
        create_overlay_pdf_activity,
        generate_site_activity,
        search_product_url_activity,
        ftfy_cleanup_activity,
        rule_based_cleanup_activity,
        gemini_cleanup_activity,
    )


@dataclass
class DocAITranslateInput:
    pdf_path: str
    manual_name: str  # Computed in CLI, not workflow (determinism)
    output_dir: str  # Computed in CLI, not workflow (determinism)
    source_language: str = "ja"
    target_language: str = "en"
    skip_cleanup: bool = False


@dataclass
class DocAITranslateOutput:
    input_path: str
    output_dir: str
    json_path: str
    html_path: str
    pdf_path: str
    ocr_blocks: int
    translated_blocks: int
    success: bool
    product_url: str = ""
    product_name: str = ""
    error: str | None = None


# Retry policies for different activity types
QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)

API_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    backoff_coefficient=2.0,
)

LLM_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


@workflow.defn
class DocAITranslateWorkflow:
    """
    Workflow that uses Document AI for OCR, then translates text blocks
    and overlays them on the original PDF.

    This approach:
    1. Preserves original PDF layout exactly
    2. Extracts text with precise bounding boxes via Document AI
    3. Translates each text block
    4. Overlays translated text on a copy of the original
    """

    @workflow.run
    async def run(self, input: DocAITranslateInput) -> DocAITranslateOutput:
        workflow.logger.info(f"Starting DocAI translation: {input.pdf_path}")
        workflow.logger.info(f"  Manual: {input.manual_name}")
        workflow.logger.info(f"  Output: {input.output_dir}")

        # Step 1: Search for product URL on Tokullectibles
        workflow.logger.info(f"[Product Search] Finding {input.manual_name} on Tokullectibles...")
        search_result = await workflow.execute_activity(
            search_product_url_activity,
            input.manual_name,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=API_RETRY,
        )

        product_url = ""
        product_name = ""
        product_description = ""
        blog_url = ""

        if search_result.get("success"):
            product_url = search_result["url"]
            product_name = search_result["name"]
            product_description = search_result.get("description", "")
            blog_url = search_result.get("blog_url", "")
            workflow.logger.info(f"Found product: {product_name} at {product_url}")
        else:
            workflow.logger.warning(
                f"Product not found on Tokullectibles: {input.manual_name}"
            )

        # Step 2: Get page count
        workflow.logger.info("[PDF Analysis] Getting page count...")
        page_count: int = await workflow.execute_activity(
            get_pdf_page_count_activity,
            input.pdf_path,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=QUICK_RETRY,
        )
        workflow.logger.info(f"Document has {page_count} pages")

        # Step 3: OCR each page in parallel
        workflow.logger.info(
            f"[OCR] Running Document AI on {page_count} pages in parallel..."
        )

        # Fan out - create activity tasks for each page
        ocr_tasks = []
        for page_num in range(page_count):
            task = workflow.execute_activity(
                ocr_page_activity,
                args=[input.pdf_path, page_num],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=API_RETRY,
            )
            ocr_tasks.append(task)

        # Fan in - wait for all pages to complete
        page_results: list[PageOCRResult] = await asyncio.gather(*ocr_tasks)

        # Collect all blocks and check for errors
        all_blocks = []
        failed_pages = []
        for result in page_results:
            if result.success:
                all_blocks.extend(result.blocks)
            else:
                failed_pages.append(result.page_num)

        if failed_pages:
            workflow.logger.warning(f"OCR failed on pages: {failed_pages}")

        workflow.logger.info(
            f"OCR complete: {len(all_blocks)} blocks from {page_count} pages"
        )

        if not all_blocks:
            return DocAITranslateOutput(
                input_path=input.pdf_path,
                output_dir="",
                json_path="",
                html_path="",
                pdf_path="",
                ocr_blocks=0,
                translated_blocks=0,
                success=False,
                error="No text blocks found in document",
            )

        # Step 4: Translate text blocks (using external storage for large data)
        workflow.logger.info("[Translation] Translating text blocks via Google Translate API...")

        # Convert dataclass blocks to dicts and store in SQLite
        blocks_dict = [
            {
                "text": b.text,
                "page": b.page,
                "x": b.x,
                "y": b.y,
                "width": b.width,
                "height": b.height,
            }
            for b in all_blocks
        ]

        translation_result: TranslationResult = await workflow.execute_activity(
            translate_blocks_activity,
            args=[blocks_dict, input.source_language, input.target_language],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=API_RETRY,
        )

        if not translation_result.success:
            return DocAITranslateOutput(
                input_path=input.pdf_path,
                output_dir="",
                json_path="",
                html_path="",
                pdf_path="",
                ocr_blocks=len(all_blocks),
                translated_blocks=0,
                success=False,
                error=f"Translation failed: {translation_result.error}",
            )

        workflow.logger.info(
            f"Translation complete: {len(translation_result.blocks)} blocks"
        )

        # Step 5: Generate static site with HTML viewer, JSON, and PDF
        workflow.logger.info("[Site Generation] Creating viewer, JSON, and WebP images...")

        # Convert translated blocks to dicts and store in SQLite
        translated_dict = [
            {
                "original": b.original,
                "translated": b.translated,
                "page": b.page,
                "x": b.x,
                "y": b.y,
                "width": b.width,
                "height": b.height,
            }
            for b in translation_result.blocks
        ]

        site_result: SiteOutput = await workflow.execute_activity(
            generate_site_activity,
            args=[
                input.pdf_path,
                translated_dict,
                input.output_dir,
                input.source_language,
                input.target_language,
                product_url,
                blog_url,
            ],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=QUICK_RETRY,
        )

        if not site_result.success:
            return DocAITranslateOutput(
                input_path=input.pdf_path,
                output_dir="",
                json_path="",
                html_path="",
                pdf_path="",
                ocr_blocks=len(all_blocks),
                translated_blocks=len(translation_result.blocks),
                success=False,
                error=f"Site generation failed: {site_result.error}",
            )

        # Step 6: Clean up translations in 3 stages (optional)
        if not input.skip_cleanup:
            workflow.logger.info("[Cleanup] Running 3-stage translation cleanup pipeline...")

            # Stage 1: ftfy (deterministic)
            workflow.logger.info("[Cleanup: Stage 1/3] Fixing Unicode/OCR corruption with ftfy...")
            ftfy_result = await workflow.execute_activity(
                ftfy_cleanup_activity,
                site_result.json_path,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=QUICK_RETRY,
            )

            if ftfy_result.get("success"):
                workflow.logger.info(f"[Cleanup: Stage 1/3] ✓ Fixed {ftfy_result['fixes']} encoding issues")
            else:
                workflow.logger.warning(f"[Cleanup: Stage 1/3] Warning: {ftfy_result.get('error')}")

            # Stage 2: Rule-based (deterministic)
            workflow.logger.info("[Cleanup: Stage 2/3] Removing artifacts with rule-based patterns...")
            rules_result = await workflow.execute_activity(
                rule_based_cleanup_activity,
                site_result.json_path,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=QUICK_RETRY,
            )

            if rules_result.get("success"):
                workflow.logger.info(f"[Cleanup: Stage 2/3] ✓ Removed {rules_result['removals']} noise blocks")
            else:
                workflow.logger.warning(f"[Cleanup: Stage 2/3] Warning: {rules_result.get('error')}")

            # Stage 3: Gemini (non-deterministic, optional)
            workflow.logger.info("[Cleanup: Stage 3/3] Applying AI corrections with Gemini 2.5 Flash...")
            gemini_result = await workflow.execute_activity(
                gemini_cleanup_activity,
                args=[site_result.json_path, product_name, product_description],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=LLM_RETRY,
                heartbeat_timeout=timedelta(minutes=2),
            )

            if gemini_result.get("success"):
                workflow.logger.info(f"[Cleanup: Stage 3/3] ✓ Applied {gemini_result['corrections']} AI corrections")
                if gemini_result.get("corrected_product_name"):
                    product_name = gemini_result["corrected_product_name"]
                    workflow.logger.info(f"[Cleanup: Stage 3/3] Updated product name: {product_name}")
            else:
                # Non-fatal - Gemini can fail, warn but continue
                workflow.logger.warning(
                    f"[Cleanup: Stage 3/3] Warning: {gemini_result.get('error', 'Unknown error')}"
                )
        else:
            workflow.logger.info("[Cleanup] Skipped (--skip-cleanup flag)")

        workflow.logger.info(f"Complete: {site_result.output_dir}")

        return DocAITranslateOutput(
            input_path=input.pdf_path,
            output_dir=site_result.output_dir,
            json_path=site_result.json_path,
            html_path=site_result.html_path,
            pdf_path=site_result.pdf_path,
            ocr_blocks=len(all_blocks),
            translated_blocks=len(translation_result.blocks),
            product_url=product_url,
            product_name=product_name,
            success=True,
        )
