"""Local translation: stage, clean, audit, then publish once without human gates."""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from src.activities.publication import (
        prepare_audit_activity,
        prepare_publication_activity,
        publish_manual_activity,
    )
    from src.activities.render_audit import (
        audit_page_activity,
        summarize_audit_activity,
    )
    from src.publication_models import AuditPreparation, PageAudit
    from src.workflows.cleanup_workflow import CleanupInput, CleanupWorkflow
    from src.workflows.ocr_workflow import OCRInput, OCRWorkflow
    from src.workflows.site_generation_workflow import (
        SiteGenerationInput,
        SiteGenerationWorkflow,
    )
    from src.workflows.translation_workflow import TranslationInput, TranslationWorkflow

LOCAL_RETRY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["ValueError", "ValidationError", "FileNotFoundError"],
)
AUDIT_RETRY = RetryPolicy(
    maximum_attempts=2,
    initial_interval=timedelta(seconds=2),
    non_retryable_error_types=["ValueError", "ValidationError"],
)


@dataclass
class PDFTranslationInput:
    """A local source PDF and final manual directory."""

    pdf_path: str
    manual_name: str
    output_dir: str
    source_language: str = "ja"
    target_language: str = "en"
    skip_cleanup: bool = False
    product_url: str = ""


@dataclass
class PDFTranslationOutput:
    """Machine completion is distinct from advisory quality warnings."""

    input_path: str
    output_dir: str = ""
    json_path: str = ""
    html_path: str = ""
    ocr_blocks: int = 0
    translated_blocks: int = 0
    product_url: str = ""
    product_name: str = ""
    success: bool = False
    error: str | None = None
    notes_path: str = ""
    quality_status: Literal["complete", "complete_with_warnings", "failed"] = "failed"
    warnings: list[str] = field(default_factory=list)
    audit_path: str = ""
    backup_dir: str = ""
    staging_dir: str = ""


@dataclass
class WorkflowProgress:
    """Progress includes the audit and final publication rather than early visibility."""

    phase: str = "initializing"
    ocr_progress: str = ""
    translation_progress: str = ""
    site_progress: str = ""
    cleanup_progress: str = ""
    audit_progress: str = ""
    publish_progress: str = ""
    pages_total: int = 0
    pages_completed: int = 0
    blocks_total: int = 0
    product_url: str = ""
    product_name: str = ""
    manual_name: str = ""


@workflow.defn(failure_exception_types=[Exception])
class PDFTranslationWorkflow:
    """Run the current local pipeline; historical orchestration is intentionally unsupported."""

    def __init__(self) -> None:
        self._progress = WorkflowProgress()
        self._warnings: list[str] = []
        self._staging = ""

    def _failed(self, input: PDFTranslationInput, message: str) -> PDFTranslationOutput:
        self._progress.phase = "failed"
        return PDFTranslationOutput(
            input_path=input.pdf_path,
            error=message,
            warnings=self._warnings,
            ocr_blocks=self._progress.blocks_total,
            staging_dir=self._staging,
        )

    async def _audit(self, prepared: AuditPreparation, page: int) -> PageAudit:
        try:
            result = await workflow.execute_activity(
                audit_page_activity,
                args=[prepared, page],
                start_to_close_timeout=timedelta(minutes=2),
                heartbeat_timeout=timedelta(seconds=25),
                retry_policy=AUDIT_RETRY,
            )
        except ActivityError as error:
            result = PageAudit(page, False, error=str(error.cause or error))
        self._progress.pages_completed += 1
        self._progress.audit_progress = (
            f"Processed {self._progress.pages_completed}/{prepared.page_count} pages"
        )
        return result

    @workflow.run
    async def run(self, input: PDFTranslationInput) -> PDFTranslationOutput:
        self._progress.manual_name = input.manual_name
        try:
            plan = await workflow.execute_activity(
                prepare_publication_activity,
                input.output_dir,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=LOCAL_RETRY,
            )
            self._staging = plan.staging_dir
            self._progress.phase = "ocr"
            self._progress.ocr_progress = "Extracting every source page"
            ocr = await workflow.execute_child_workflow(
                OCRWorkflow.run,
                OCRInput(input.pdf_path, input.manual_name, input.product_url),
                id=f"{workflow.info().workflow_id}-ocr",
            )
            self._progress.blocks_total = len(ocr.blocks)
            if not ocr.success:
                return self._failed(input, f"OCR failed: {ocr.error}")
            self._progress.pages_total = ocr.page_count
            self._progress.ocr_progress = (
                f"Complete: {ocr.page_count} pages, {len(ocr.blocks)} regions"
            )
            product_url = input.product_url or ocr.product_url
            self._progress.product_url = product_url
            self._progress.product_name = ocr.product_name
            self._progress.phase = "translation"
            self._progress.translation_progress = (
                f"Translating {len(ocr.blocks)} regions"
            )
            translated = await workflow.execute_child_workflow(
                TranslationWorkflow.run,
                TranslationInput(
                    blocks=ocr.blocks,
                    source_lang=input.source_language,
                    target_lang=input.target_language,
                    product=input.manual_name,
                ),
                id=f"{workflow.info().workflow_id}-translation",
            )
            if not translated.success:
                return self._failed(input, f"Translation failed: {translated.error}")
            self._warnings.extend(translated.warnings)
            self._progress.translation_progress = (
                f"Complete: {len(translated.translated_blocks)} regions"
            )
            self._progress.phase = "site_generation"
            self._progress.site_progress = "Rendering scans into private staging"
            site = await workflow.execute_child_workflow(
                SiteGenerationWorkflow.run,
                SiteGenerationInput(
                    pdf_path=input.pdf_path,
                    translated_blocks=translated.translated_blocks,
                    output_dir=plan.staging_dir,
                    source_lang=input.source_language,
                    target_lang=input.target_language,
                    product_url=product_url,
                    blog_url=ocr.blog_url,
                    source_snapshot=plan.source_snapshot,
                ),
                id=f"{workflow.info().workflow_id}-site",
            )
            if not site.success:
                return self._failed(input, f"Site generation failed: {site.error}")
            self._progress.site_progress = f"Complete: {site.page_count} staged pages"
            name = ocr.product_name
            if not input.skip_cleanup:
                self._progress.phase = "cleanup"
                self._progress.cleanup_progress = "Formatting and page-aware AI cleanup"
                cleanup = await workflow.execute_child_workflow(
                    CleanupWorkflow.run,
                    CleanupInput(
                        site.json_path,
                        ocr.product_name,
                        ocr.product_description,
                        glossary_path=translated.glossary_path,
                    ),
                    id=f"{workflow.info().workflow_id}-cleanup",
                )
                if cleanup.error:
                    self._warnings.append(f"Cleanup: {cleanup.error}")
                name = cleanup.corrected_product_name or name
                self._progress.cleanup_progress = (
                    f"Complete: {cleanup.openai_corrections} AI corrections"
                )
            else:
                self._warnings.append("Cleanup skipped by request")
            self._progress.phase = "audit"
            self._progress.pages_completed = 0
            self._progress.audit_progress = (
                "Validating source and preparing frozen browser assets"
            )
            prepared = await workflow.execute_activity(
                prepare_audit_activity,
                args=[plan, ocr.page_count],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=LOCAL_RETRY,
            )
            pages = await asyncio.gather(
                *(self._audit(prepared, page) for page in range(ocr.page_count))
            )
            summary = await workflow.execute_activity(
                summarize_audit_activity,
                args=[prepared, pages],
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=LOCAL_RETRY,
            )
            self._warnings.extend(summary.warnings)
            self._progress.audit_progress = f"Complete: {summary.pages_checked}/{ocr.page_count} measured; layout warnings are advisory"
            self._progress.phase = "publish"
            self._progress.publish_progress = (
                "Publishing validated output and refreshing catalog"
            )
            published = await workflow.execute_activity(
                publish_manual_activity,
                args=[plan, prepared, self._warnings, summary.report_path],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=LOCAL_RETRY,
            )
            self._progress.publish_progress = "Complete: final manual published"
            self._progress.phase = "complete"
            return PDFTranslationOutput(
                input_path=input.pdf_path,
                output_dir=published.output_dir,
                json_path=published.json_path,
                html_path=published.html_path,
                ocr_blocks=len(ocr.blocks),
                translated_blocks=len(translated.translated_blocks),
                product_url=product_url,
                product_name=name,
                success=True,
                notes_path=translated.notes_path,
                quality_status="complete_with_warnings"
                if self._warnings
                else "complete",
                warnings=self._warnings,
                audit_path=summary.report_path,
                backup_dir=published.backup_dir,
            )
        except (ActivityError, ChildWorkflowError) as error:
            return self._failed(input, str(error.cause or error))

    @workflow.query
    def get_progress(self) -> WorkflowProgress:
        """Read the current machine stage without introducing a human gate."""
        return self._progress
