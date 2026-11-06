"""Compose independent cached parser results before semantic planning."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.activities.combined_layout import _build_index, build_combined_index_activity
from src.combined_models import CombinedLayoutInput
from src.layout_models import LayoutBenchmarkInput
from src.semantic_models import SemanticLayoutInput
from src.workflows.combined_layout_workflow import CombinedLayoutWorkflow


def test_corpus_resume_recovers_a_completed_publication_without_overwriting_edits(
    tmp_path: Path,
) -> None:
    """A crash after publication but before status persistence must remain recoverable."""
    from PIL import Image

    from src.publication_models import StagedManual
    from src.quality.semantic_repair import _completed_publication

    manual = tmp_path / "manual"
    manual.mkdir()
    Image.new("RGB", (100, 100), "white").save(manual / "scan.png")
    value = {
        "meta": {"source": "scan.pdf"},
        "pages": [
            {
                "image": "scan.png",
                "blocks": [
                    {
                        "text": "説明",
                        "translation": "Instructions",
                        "bbox": [0.1, 0.1, 0.2, 0.1],
                    }
                ],
            }
        ],
    }
    snapshot = tmp_path / "source.json"
    snapshot.write_text(json.dumps(value))
    target = manual / "translations.json"
    target.write_bytes(snapshot.read_bytes())
    run = tmp_path / "run"
    audit = run / "audit"
    audit.mkdir(parents=True)
    (run / "published.json").write_text(
        json.dumps(
            {
                "output_dir": str(manual),
                "json_path": str(target),
                "html_path": "web/#manual",
            }
        )
    )
    (audit / "prepared.json").write_text(
        json.dumps(
            {
                "public_dir": "public",
                "audit_dir": str(audit),
                "manual_name": "manual",
                "json_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "renderer_sha256": "renderer",
                "page_count": 1,
                "asset_sha256": {
                    "scan.png": hashlib.sha256(
                        (manual / "scan.png").read_bytes()
                    ).hexdigest()
                },
            }
        )
    )
    (audit / "summary.json").write_text(
        json.dumps({"pages_checked": 1, "warnings": []})
    )
    plan = StagedManual(
        str(run), str(run / "moved-stage"), str(manual), str(snapshot), "old"
    )
    result = _completed_publication(plan)
    assert (
        result is not None and result["status"] == "published" and result["pages"] == 1
    )
    target.write_text(target.read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        _completed_publication(plan)


@pytest.mark.anyio
async def test_combined_workflow_requires_every_parser_before_fusion_and_semantics() -> (
    None
):
    """Required evidence cannot silently disappear behind a benchmark failure string."""
    events: list[str] = []
    jobs = tuple(
        LayoutBenchmarkInput("manifest", provider, "/python", providers=(provider,))
        for provider in ("docling", "google")
    )
    request = CombinedLayoutInput(
        SemanticLayoutInput("manual", "run", 1, "scan.pdf"), jobs, "layout-queue"
    )

    async def activity(function: object, *args: object, **kwargs: object) -> str:
        name = getattr(function, "__name__", "")
        events.append(name)
        if function is build_combined_index_activity:
            assert (
                "docling_layout_activity" in events
                and "google_layout_activity" in events
            )
            return "combined-index.json"
        return "artifact"

    async def child(
        function: object, value: SemanticLayoutInput, **kwargs: object
    ) -> list[str]:
        assert events[-1] == "build_combined_index_activity"
        assert value.layout_index == "combined-index.json"
        events.append("semantic")
        return []

    with (
        patch(
            "src.workflows.combined_layout_workflow.workflow.execute_activity",
            new_callable=AsyncMock,
            side_effect=activity,
        ),
        patch(
            "src.workflows.combined_layout_workflow.workflow.execute_child_workflow",
            new_callable=AsyncMock,
            side_effect=child,
        ),
        patch(
            "src.workflows.combined_layout_workflow.workflow.info",
            return_value=SimpleNamespace(workflow_id="combined"),
        ),
    ):
        await CombinedLayoutWorkflow().run(request)
    assert events[-1] == "semantic"


def test_fusion_rejects_missing_provider_output_before_using_any_manual(
    tmp_path: Path,
) -> None:
    """A cached success marker alone is insufficient without every declared page."""
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"original")
    page = tmp_path / "page.pdf"
    page.write_bytes(b"page")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "pages": [
                    {
                        "page": 1,
                        "pdf": str(page),
                        "sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    request = CombinedLayoutInput(
        SemanticLayoutInput("manual", str(tmp_path / "run"), 1, str(source)),
        tuple(
            LayoutBenchmarkInput(
                str(manifest),
                str(tmp_path / provider),
                "/python",
                providers=(provider,),
            )
            for provider in ("docling", "google")
        ),
    )
    with pytest.raises(ValueError, match="Missing .* output"):
        _build_index(request)


@pytest.fixture
def anyio_backend() -> str:
    """Use the actual Worker's async runtime."""
    return "asyncio"


@pytest.mark.anyio
async def test_page_window_fills_free_slots_while_an_earlier_page_is_slow() -> None:
    """A dense page must not hold up independent later pages in the manual."""
    import asyncio

    from src.workflows.semantic_layout_workflow import SemanticLayoutWorkflow

    release = asyncio.Event()
    started: list[int] = []

    async def child(function: object, **kwargs: object) -> str:
        arguments = kwargs["args"]
        assert isinstance(arguments, list)
        index = arguments[1]
        assert isinstance(index, int)
        started.append(index)
        if index == 3:
            release.set()
        if index == 0:
            await release.wait()
        return f"page-{index}"

    with (
        patch(
            "src.workflows.semantic_layout_workflow.workflow.execute_child_workflow",
            new_callable=AsyncMock,
            side_effect=child,
        ),
        patch(
            "src.workflows.semantic_layout_workflow.workflow.execute_activity",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.workflows.semantic_layout_workflow.workflow.info",
            return_value=SimpleNamespace(workflow_id="sliding"),
        ),
        patch(
            "src.workflows.semantic_layout_workflow.workflow.patched", return_value=True
        ),
    ):
        await asyncio.wait_for(
            SemanticLayoutWorkflow().run(SemanticLayoutInput("manual", "run", 4)),
            timeout=0.2,
        )
    assert started == [0, 1, 2, 3]
