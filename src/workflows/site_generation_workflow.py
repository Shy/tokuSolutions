"""Site generation workflow - Create static HTML viewer."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.activities import SiteOutput, generate_site_activity


@dataclass
class SiteGenerationInput:
    """Input for site generation workflow."""
    pdf_path: str
    translated_blocks: list[dict]
    output_dir: str
    source_lang: str
    target_lang: str
    product_url: str
    blog_url: str


@dataclass
class SiteGenerationOutput:
    """Output from site generation workflow."""
    json_path: str
    html_path: str
    page_count: int
    block_count: int
    success: bool
    error: str | None = None


QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)


@workflow.defn
class SiteGenerationWorkflow:
    """
    Child workflow for site generation phase.

    Creates:
    - WebP images for each page
    - translations.json with all metadata
    - index.html viewer
    """

    @workflow.run
    async def run(self, input: SiteGenerationInput) -> SiteGenerationOutput:
        workflow.logger.info(f"[Site Generation Workflow] Creating viewer at {input.output_dir}")

        result: SiteOutput = await workflow.execute_activity(
            generate_site_activity,
            args=[
                input.pdf_path,
                input.translated_blocks,
                input.output_dir,
                input.source_lang,
                input.target_lang,
                input.product_url,
                input.blog_url,
            ],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=QUICK_RETRY,
        )

        if not result.success:
            return SiteGenerationOutput(
                json_path="",
                html_path="",
                page_count=0,
                block_count=0,
                success=False,
                error=result.error,
            )

        workflow.logger.info(f"[Site Generation] Complete: {result.page_count} pages")

        return SiteGenerationOutput(
            json_path=result.json_path,
            html_path=result.html_path,
            page_count=result.page_count,
            block_count=result.block_count,
            success=True,
        )
