"""Document AI based translation workflow."""

import asyncio
from datetime import timedelta
from dataclasses import dataclass

from temporalio import workflow

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
        cleanup_translations_activity,
    )


@dataclass
class DocAITranslateInput:
    pdf_path: str
    source_language: str = "ja"
    target_language: str = "en"
    output_path: str | None = None
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

        # Determine output directory and manual name early
        from pathlib import Path

        input_path = Path(input.pdf_path)
        if input.output_path:
            output_dir = input.output_path
        else:
            output_dir = f"manuals/{input_path.stem}"
        manual_name = Path(output_dir).name

        # Step 1: Search for product URL on Tokullectibles
        workflow.logger.info(f"Step 1: Searching for product URL: {manual_name}...")
        search_result = await workflow.execute_activity(
            search_product_url_activity,
            manual_name,
            start_to_close_timeout=timedelta(seconds=30),
        )

        product_url = ""
        product_name = ""
        product_description = ""

        if search_result.get("success"):
            product_url = search_result["url"]
            product_name = search_result["name"]
            product_description = search_result.get("description", "")
            workflow.logger.info(f"Found product: {product_name} at {product_url}")
        else:
            workflow.logger.warning(
                f"Product not found on Tokullectibles: {manual_name}"
            )

        # Step 2: Get page count
        workflow.logger.info("Step 2: Getting page count...")
        page_count: int = await workflow.execute_activity(
            get_pdf_page_count_activity,
            input.pdf_path,
            start_to_close_timeout=timedelta(minutes=1),
        )
        workflow.logger.info(f"Document has {page_count} pages")

        # Step 3: OCR each page in parallel
        workflow.logger.info(
            f"Step 3: Running OCR on {page_count} pages in parallel..."
        )

        # Fan out - create activity tasks for each page
        ocr_tasks = []
        for page_num in range(page_count):
            task = workflow.execute_activity(
                ocr_page_activity,
                args=[input.pdf_path, page_num],
                start_to_close_timeout=timedelta(minutes=2),
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

        # Step 4: Translate text blocks
        workflow.logger.info("Step 4: Translating blocks...")

        # Convert dataclass blocks to dicts for serialization
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
        workflow.logger.info("Step 5: Generating static site...")

        # Convert translated blocks to dicts
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
                output_dir,
                input.source_language,
                input.target_language,
                product_url,
            ],
            start_to_close_timeout=timedelta(minutes=5),
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

        # Step 6: Clean up translations (optional)
        if not input.skip_cleanup:
            workflow.logger.info("Step 6: Cleaning up translations...")

            cleanup_result = await workflow.execute_activity(
                cleanup_translations_activity,
                args=[
                    site_result.json_path,
                    product_name,
                    product_description,
                    False,
                ],  # False = use Gemini
                start_to_close_timeout=timedelta(minutes=3),
            )

            if cleanup_result.get("success"):
                workflow.logger.info(
                    f"Cleanup complete: {cleanup_result['original_blocks']} → {cleanup_result['cleaned_blocks']} blocks"
                )
            else:
                # Non-fatal - log warning but continue
                workflow.logger.warning(
                    f"Cleanup had issues: {cleanup_result.get('error', 'Unknown error')}"
                )
        else:
            workflow.logger.info("Step 6: Skipping cleanup (--skip-cleanup)")

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
