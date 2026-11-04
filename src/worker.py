"""Temporal worker for PDF translation."""

import asyncio

from temporalio.worker import Worker

from src.activities.agent_pipeline import (
    apply_agent_cleanup_activity,
    load_agent_request_activity,
    load_instruction_request_activity,
    merge_glossary_agent_activity,
    prepare_agent_cleanup_activity,
    prepare_glossary_agent_activity,
    save_agent_reply_activity,
)
from src.activities.cleanup import ftfy_cleanup_activity, rule_based_cleanup_activity
from src.activities.ocr import get_pdf_page_count_activity, ocr_page_activity
from src.activities.product_search import search_product_url_activity
from src.activities.publication import (
    prepare_audit_activity,
    prepare_publication_activity,
    publish_manual_activity,
)
from src.activities.render_audit import audit_page_activity, summarize_audit_activity
from src.activities.site_generation import generate_site_activity
from src.activities.translation import translate_blocks_activity
from src.temporal_client import TASK_QUEUE, connect_temporal
from src.workflows.agent_workflows import (
    AgentCleanupWorkflow,
    DurableAgentWorkflow,
    GlossaryAgentWorkflow,
)

# Import all workflows (parent + children)
from src.workflows.cleanup_workflow import CleanupWorkflow
from src.workflows.ocr_workflow import OCRWorkflow
from src.workflows.pdf_translation_workflow import PDFTranslationWorkflow
from src.workflows.site_generation_workflow import SiteGenerationWorkflow
from src.workflows.translation_workflow import TranslationWorkflow

WORKFLOWS = (
    DurableAgentWorkflow,
    AgentCleanupWorkflow,
    GlossaryAgentWorkflow,
    PDFTranslationWorkflow,
    OCRWorkflow,
    TranslationWorkflow,
    SiteGenerationWorkflow,
    CleanupWorkflow,
)

ACTIVITIES = (
    prepare_publication_activity,
    prepare_audit_activity,
    audit_page_activity,
    summarize_audit_activity,
    publish_manual_activity,
    prepare_agent_cleanup_activity,
    load_agent_request_activity,
    load_instruction_request_activity,
    save_agent_reply_activity,
    apply_agent_cleanup_activity,
    prepare_glossary_agent_activity,
    merge_glossary_agent_activity,
    get_pdf_page_count_activity,
    ocr_page_activity,
    translate_blocks_activity,
    generate_site_activity,
    search_product_url_activity,
    ftfy_cleanup_activity,
    rule_based_cleanup_activity,
)

__all__ = ["ACTIVITIES", "TASK_QUEUE", "WORKFLOWS", "main"]


async def main() -> None:
    client = await connect_temporal()

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        max_concurrent_activities=1,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
    )

    print(f"Worker started, listening on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
