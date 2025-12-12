"""Parent workflow orchestrating PDF translation pipeline with child workflows."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.workflows.ocr_workflow import OCRWorkflow, OCRInput, OCROutput
    from src.workflows.translation_workflow import (
        TranslationWorkflow,
        TranslationInput,
        TranslationOutput,
    )
    from src.workflows.site_generation_workflow import (
        SiteGenerationWorkflow,
        SiteGenerationInput,
        SiteGenerationOutput,
    )
    from src.workflows.cleanup_workflow import (
        CleanupWorkflow,
        CleanupInput,
        CleanupOutput,
    )


@dataclass
class PDFTranslationInput:
    """Input for PDF translation workflow."""
    pdf_path: str
    manual_name: str
    output_dir: str
    source_language: str = "ja"
    target_language: str = "en"
    skip_cleanup: bool = False


@dataclass
class PDFTranslationOutput:
    """Output from PDF translation workflow."""
    input_path: str
    output_dir: str
    json_path: str
    html_path: str
    ocr_blocks: int
    translated_blocks: int
    product_url: str
    product_name: str
    success: bool
    error: str | None = None


@dataclass
class WorkflowProgress:
    """Progress state for queries."""
    phase: str  # "ocr", "translation", "site_generation", "cleanup", "complete"
    ocr_progress: str = ""
    translation_progress: str = ""
    site_progress: str = ""
    cleanup_progress: str = ""
    pages_total: int = 0
    blocks_total: int = 0
    product_url: str = ""
    product_name: str = ""
    manual_name: str = ""
    waiting_for_url: bool = False  # True when workflow is blocked waiting for user URL


@workflow.defn
class PDFTranslationWorkflow:
    """
    Parent workflow orchestrating PDF translation using child workflows.

    Pipeline:
    1. OCRWorkflow - Extract text from PDF
    2. TranslationWorkflow - Translate text blocks
    3. SiteGenerationWorkflow - Create web viewer
    4. CleanupWorkflow - Improve translation quality
    """

    def __init__(self):
        self._progress = WorkflowProgress(phase="initializing")
        self._user_provided_url: str = ""  # Store user-provided URL from CLI signal
        self._waiting_for_url: bool = False  # Flag for CLI to detect

    @workflow.run
    async def run(self, input: PDFTranslationInput) -> PDFTranslationOutput:
        workflow.logger.info(f"[PDF Translation] Starting pipeline for {input.manual_name}")

        # Phase 1: OCR
        self._progress.phase = "ocr"
        self._progress.ocr_progress = "Starting OCR workflow..."
        workflow.logger.info("[Phase 1/4] OCR")

        ocr_result: OCROutput = await workflow.execute_child_workflow(
            OCRWorkflow.run,
            OCRInput(pdf_path=input.pdf_path, manual_name=input.manual_name),
            id=f"{workflow.info().workflow_id}-ocr",
            task_queue=workflow.info().task_queue,
        )

        if not ocr_result.success:
            return PDFTranslationOutput(
                input_path=input.pdf_path,
                output_dir="",
                json_path="",
                html_path="",
                ocr_blocks=0,
                translated_blocks=0,
                product_url="",
                product_name="",
                success=False,
                error=f"OCR failed: {ocr_result.error}",
            )

        self._progress.pages_total = ocr_result.page_count
        self._progress.blocks_total = len(ocr_result.blocks)
        self._progress.product_url = ocr_result.product_url
        self._progress.product_name = ocr_result.product_name
        self._progress.manual_name = input.manual_name
        self._progress.ocr_progress = f"Complete: {len(ocr_result.blocks)} blocks from {ocr_result.page_count} pages"

        # Wait for product URL if not found automatically
        final_product_url = ocr_result.product_url
        if not final_product_url:
            workflow.logger.info("Product URL not found, waiting for user input...")
            self._waiting_for_url = True
            self._progress.waiting_for_url = True
            self._progress.ocr_progress = "Complete - Waiting for product URL..."

            # Wait for user to provide URL via signal (30 minute timeout)
            await workflow.wait_condition(
                lambda: not self._waiting_for_url,
                timeout=timedelta(minutes=30)
            )

            # Use user-provided URL (can be empty if user skipped)
            final_product_url = self._user_provided_url
            self._progress.product_url = final_product_url
            workflow.logger.info(f"Continuing with URL: {final_product_url or '(none)'}")

        # Phase 2: Translation
        self._progress.phase = "translation"
        self._progress.translation_progress = f"Translating {len(ocr_result.blocks)} blocks..."
        workflow.logger.info("[Phase 2/4] Translation")

        translation_result: TranslationOutput = await workflow.execute_child_workflow(
            TranslationWorkflow.run,
            TranslationInput(
                blocks=ocr_result.blocks,
                source_lang=input.source_language,
                target_lang=input.target_language,
            ),
            id=f"{workflow.info().workflow_id}-translation",
            task_queue=workflow.info().task_queue,
        )

        if not translation_result.success:
            return PDFTranslationOutput(
                input_path=input.pdf_path,
                output_dir="",
                json_path="",
                html_path="",
                ocr_blocks=len(ocr_result.blocks),
                translated_blocks=0,
                product_url=ocr_result.product_url,
                product_name=ocr_result.product_name,
                success=False,
                error=f"Translation failed: {translation_result.error}",
            )

        self._progress.translation_progress = f"Complete: {len(translation_result.translated_blocks)} blocks"

        # Phase 3: Site Generation
        self._progress.phase = "site_generation"
        self._progress.site_progress = "Generating web viewer..."
        workflow.logger.info("[Phase 3/4] Site Generation")

        site_result: SiteGenerationOutput = await workflow.execute_child_workflow(
            SiteGenerationWorkflow.run,
            SiteGenerationInput(
                pdf_path=input.pdf_path,
                translated_blocks=translation_result.translated_blocks,
                output_dir=input.output_dir,
                source_lang=input.source_language,
                target_lang=input.target_language,
                product_url=final_product_url,  # Use final URL (from user or auto-search)
                blog_url=ocr_result.blog_url,
            ),
            id=f"{workflow.info().workflow_id}-site",
            task_queue=workflow.info().task_queue,
        )

        if not site_result.success:
            return PDFTranslationOutput(
                input_path=input.pdf_path,
                output_dir="",
                json_path="",
                html_path="",
                ocr_blocks=len(ocr_result.blocks),
                translated_blocks=len(translation_result.translated_blocks),
                product_url=ocr_result.product_url,
                product_name=ocr_result.product_name,
                success=False,
                error=f"Site generation failed: {site_result.error}",
            )

        self._progress.site_progress = f"Complete: {site_result.page_count} pages"

        # Phase 4: Cleanup
        if not input.skip_cleanup:
            self._progress.phase = "cleanup"
            self._progress.cleanup_progress = "Running 3-stage cleanup..."
            workflow.logger.info("[Phase 4/4] Cleanup")

            cleanup_result: CleanupOutput = await workflow.execute_child_workflow(
                CleanupWorkflow.run,
                CleanupInput(
                    json_path=site_result.json_path,
                    product_name=ocr_result.product_name,
                    product_description=ocr_result.product_description,
                    skip_gemini=False,
                ),
                id=f"{workflow.info().workflow_id}-cleanup",
                task_queue=workflow.info().task_queue,
            )

            self._progress.cleanup_progress = (
                f"Complete: {cleanup_result.ftfy_fixes} ftfy, "
                f"{cleanup_result.rule_removals} rules, "
                f"{cleanup_result.gemini_corrections} AI corrections"
            )

            # Use corrected product name if available
            final_product_name = (
                cleanup_result.corrected_product_name or ocr_result.product_name
            )
        else:
            self._progress.cleanup_progress = "Skipped"
            final_product_name = ocr_result.product_name

        # Complete
        self._progress.phase = "complete"
        workflow.logger.info(f"[PDF Translation] Complete: {input.output_dir}")

        return PDFTranslationOutput(
            input_path=input.pdf_path,
            output_dir=input.output_dir,
            json_path=site_result.json_path,
            html_path=site_result.html_path,
            ocr_blocks=len(ocr_result.blocks),
            translated_blocks=len(translation_result.translated_blocks),
            product_url=final_product_url,  # Use final URL (from user or auto-search)
            product_name=final_product_name,
            success=True,
        )

    @workflow.signal
    def provide_url(self, url: str):
        """Signal to provide product URL from CLI."""
        self._user_provided_url = url
        self._waiting_for_url = False
        self._progress.waiting_for_url = False
        workflow.logger.info(f"Received URL from user: {url}")

    @workflow.query
    def get_progress(self) -> WorkflowProgress:
        """Query for real-time progress tracking."""
        return self._progress
