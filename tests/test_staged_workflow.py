"""The local pipeline publishes only after cleanup, integrity checks and machine auditing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.activities.ocr import PageOCRResult, TextBlock
from src.activities.publication import (
    prepare_audit_activity,
    prepare_publication_activity,
    publish_manual_activity,
)
from src.activities.render_audit import audit_page_activity, summarize_audit_activity
from src.publication_models import (
    AuditPreparation,
    AuditSummary,
    PageAudit,
    PublishedManual,
    StagedManual,
)
from src.workflows.cleanup_workflow import CleanupOutput
from src.workflows.ocr_workflow import OCRInput, OCROutput, OCRWorkflow
from src.workflows.pdf_translation_workflow import (
    PDFTranslationInput,
    PDFTranslationWorkflow,
)
from src.workflows.site_generation_workflow import (
    SiteGenerationInput,
    SiteGenerationOutput,
)
from src.workflows.translation_workflow import TranslationOutput


@pytest.mark.anyio
@pytest.mark.parametrize("advisory", [False, True])
async def test_publish_is_last_and_quality_warnings_are_nonblocking(
    advisory: bool,
) -> None:
    """Warnings change quality status without inserting a human decision."""
    events: list[str] = []
    plan = StagedManual("run", "stage/Manual", "manuals/Manual", "source.json", "")
    prepared = AuditPreparation("public", "audit", "Manual", "hash", "renderer", 2)

    async def activity(function: object, *args: object, **kwargs: object) -> object:
        if function is prepare_publication_activity:
            events.append("stage")
            return plan
        if function is prepare_audit_activity:
            events.append("validate")
            return prepared
        if function is audit_page_activity:
            events.append("audit")
            return PageAudit(0, True)
        if function is summarize_audit_activity:
            events.append("summary")
            return AuditSummary(
                "audit/summary.json", 2, ["mobile overflow"] if advisory else []
            )
        if function is publish_manual_activity:
            events.append("publish")
            return PublishedManual(
                "manuals/Manual",
                "manuals/Manual/translations.json",
                "web/index.html#Manual",
            )
        raise AssertionError(function)

    async def child(function: object, *args: object, **kwargs: object) -> object:
        name = getattr(function, "__qualname__", "").split(".")[0]
        events.append(name)
        if name == "OCRWorkflow":
            return OCROutput([], 2, "", "Manual", "", "", True)
        if name == "TranslationWorkflow":
            return TranslationOutput([], True)
        if name == "SiteGenerationWorkflow":
            request = args[0]
            assert isinstance(request, SiteGenerationInput)
            assert request.output_dir == plan.staging_dir
            assert request.source_snapshot == plan.source_snapshot
            return SiteGenerationOutput(
                "stage/Manual/translations.json", "", 2, 0, True
            )
        if name == "CleanupWorkflow":
            return CleanupOutput(
                0, 0, 1, None, None, True, "AI unavailable" if advisory else None
            )
        if name == "SemanticLayoutWorkflow":
            return []
        raise AssertionError(function)

    with (
        patch(
            "src.workflows.pdf_translation_workflow.workflow.info",
            return_value=SimpleNamespace(workflow_id="run"),
        ),
        patch(
            "src.workflows.pdf_translation_workflow.workflow.execute_activity",
            new_callable=AsyncMock,
            side_effect=activity,
        ),
        patch(
            "src.workflows.pdf_translation_workflow.workflow.execute_child_workflow",
            new_callable=AsyncMock,
            side_effect=child,
        ),
    ):
        result = await PDFTranslationWorkflow().run(
            PDFTranslationInput("scan.pdf", "Manual", "manuals/Manual")
        )
    assert result.success
    assert result.quality_status == (
        "complete_with_warnings" if advisory else "complete"
    )
    assert result.audit_path == "audit/summary.json"
    assert events == [
        "stage",
        "OCRWorkflow",
        "TranslationWorkflow",
        "SiteGenerationWorkflow",
        "CleanupWorkflow",
        "SemanticLayoutWorkflow",
        "validate",
        "audit",
        "audit",
        "summary",
        "publish",
    ]
    if advisory:
        assert "AI unavailable" in " ".join(result.warnings)


@pytest.mark.anyio
async def test_partial_ocr_does_not_proceed_to_translation_or_publication() -> None:
    """One failed OCR page is a technical failure even when another page has text."""
    with (
        patch("src.workflows.ocr_workflow.workflow.logger"),
        patch(
            "src.workflows.ocr_workflow.workflow.execute_activity",
            new_callable=AsyncMock,
            side_effect=[
                2,
                PageOCRResult(0, [TextBlock("ON", 0, 0, 0, 0.1, 0.1, 0.9)], True),
                PageOCRResult(1, [], False, "provider failure"),
            ],
        ),
    ):
        result = await OCRWorkflow().run(
            OCRInput("scan.pdf", "Manual", "https://example.com/manual")
        )
    assert not result.success
    assert result.failed_pages == [1]
    assert "2" in (result.error or "")


@pytest.fixture
def anyio_backend() -> str:
    """Use the Worker's asyncio runtime."""
    return "asyncio"


def test_cli_and_worker_use_the_same_fresh_local_queue() -> None:
    """Do not strand new runs on the retired queue or accidentally consume old jobs."""
    import src.cli
    import src.worker

    assert src.cli.TASK_QUEUE == src.worker.TASK_QUEUE == "manual-translation"


@pytest.mark.anyio
async def test_translation_must_preserve_every_ocr_region() -> None:
    """A provider must not silently omit regions before the source snapshot exists."""
    from src.activities.translation import TranslationResult
    from src.workflows.translation_workflow import TranslationInput, TranslationWorkflow

    with patch(
        "src.workflows.translation_workflow.workflow.execute_activity",
        new_callable=AsyncMock,
        return_value=TranslationResult([], True),
    ):
        result = await TranslationWorkflow().run(
            TranslationInput(
                [
                    {
                        "text": "ON",
                        "page": 0,
                        "x": 0,
                        "y": 0,
                        "width": 0.1,
                        "height": 0.1,
                    }
                ],
                source_lang="en",
            )
        )
    assert not result.success
    assert "source regions" in (result.error or "")
