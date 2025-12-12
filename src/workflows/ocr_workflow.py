"""OCR workflow - Extract text from PDF using Document AI."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.activities import (
        PageOCRResult,
        get_pdf_page_count_activity,
        ocr_page_activity,
        search_product_url_activity,
    )


@dataclass
class OCRInput:
    """Input for OCR workflow."""
    pdf_path: str
    manual_name: str


@dataclass
class OCROutput:
    """Output from OCR workflow."""
    blocks: list[dict]  # All OCR'd text blocks
    page_count: int
    product_url: str
    product_name: str
    product_description: str
    blog_url: str
    success: bool
    error: str | None = None


# Retry policies
API_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    backoff_coefficient=2.0,
)

QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)


@workflow.defn
class OCRWorkflow:
    """
    Child workflow for OCR phase.

    Performs:
    1. Product search on Tokullectibles
    2. PDF page count extraction
    3. Parallel OCR of all pages using Document AI
    """

    @workflow.run
    async def run(self, input: OCRInput) -> OCROutput:
        workflow.logger.info(f"[OCR Workflow] Starting for {input.manual_name}")

        # Step 1: Search for product URL
        workflow.logger.info("[Product Search] Searching Tokullectibles...")
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
            workflow.logger.info(f"Found: {product_name}")
        else:
            workflow.logger.warning(f"Product not found: {input.manual_name}")

        # Step 2: Get page count
        workflow.logger.info("[PDF Analysis] Getting page count...")
        page_count: int = await workflow.execute_activity(
            get_pdf_page_count_activity,
            input.pdf_path,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=QUICK_RETRY,
        )
        workflow.logger.info(f"Pages: {page_count}")

        # Step 3: OCR all pages in parallel (fan-out/fan-in)
        workflow.logger.info(f"[OCR] Processing {page_count} pages in parallel...")

        ocr_tasks = []
        for page_num in range(page_count):
            task = workflow.execute_activity(
                ocr_page_activity,
                args=[input.pdf_path, page_num],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=API_RETRY,
            )
            ocr_tasks.append(task)

        # Wait for all pages
        page_results: list[PageOCRResult] = await asyncio.gather(*ocr_tasks)

        # Collect blocks
        all_blocks = []
        failed_pages = []
        for result in page_results:
            if result.success:
                # Convert TextBlock dataclasses to dicts
                for block in result.blocks:
                    all_blocks.append({
                        "text": block.text,
                        "page": block.page,
                        "x": block.x,
                        "y": block.y,
                        "width": block.width,
                        "height": block.height,
                    })
            else:
                failed_pages.append(result.page_num)

        if failed_pages:
            workflow.logger.warning(f"OCR failed on pages: {failed_pages}")

        workflow.logger.info(f"[OCR] Complete: {len(all_blocks)} blocks")

        if not all_blocks:
            return OCROutput(
                blocks=[],
                page_count=page_count,
                product_url=product_url,
                product_name=product_name,
                product_description=product_description,
                blog_url=blog_url,
                success=False,
                error="No text blocks found",
            )

        return OCROutput(
            blocks=all_blocks,
            page_count=page_count,
            product_url=product_url,
            product_name=product_name,
            product_description=product_description,
            blog_url=blog_url,
            success=True,
        )
