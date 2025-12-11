"""Temporal worker for PDF translation."""

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from src.workflow import DocAITranslateWorkflow
from src.activities import (
    ocr_document_activity,
    get_pdf_page_count_activity,
    ocr_page_activity,
    translate_blocks_activity,
    create_overlay_pdf_activity,
    generate_site_activity,
    search_product_url_activity,
    cleanup_translations_activity,
)

TASK_QUEUE = "pdf-translation"


async def main():
    client = await Client.connect("localhost:7233")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DocAITranslateWorkflow],
        activities=[
            ocr_document_activity,
            get_pdf_page_count_activity,
            ocr_page_activity,
            translate_blocks_activity,
            create_overlay_pdf_activity,
            generate_site_activity,
            search_product_url_activity,
            cleanup_translations_activity,
        ],
    )

    print(f"Worker started, listening on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
