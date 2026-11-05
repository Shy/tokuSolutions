"""Audit summaries distinguish unavailable measurements and advisory layout failures."""

import json
from pathlib import Path

import pytest

from src.activities.render_audit import summarize_audit_activity
from src.publication_models import AuditPreparation, PageAudit


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", ["unavailable", "incomplete-view", "incomplete-region", "none"]
)
async def test_audit_summary_is_honest_about_coverage(
    tmp_path: Path, failure: str
) -> None:
    """A partial audit cannot be described as clean or complete coverage."""
    manual = tmp_path / "public/baseline/manuals/Manual"
    manual.mkdir(parents=True)
    (manual / "translations.json").write_text(
        json.dumps(
            {
                "meta": {},
                "pages": [
                    {
                        "blocks": [
                            {
                                "text": "ON",
                                "translation": "ON",
                                "bbox": [0, 0, 0.1, 0.1],
                            }
                        ],
                        "image": "page.webp",
                    }
                ],
            }
        )
    )
    metrics = [
        {
            "view": view,
            "blocks": [
                {
                    "block_index": 0,
                    "font_px": 8,
                    "overflow": True,
                    "fit_overflow": True,
                    "dom_overflow": True,
                }
            ],
        }
        for view in ("desktop", "mobile", "pdf")
    ]
    if failure == "incomplete-view":
        metrics.pop()
    if failure == "incomplete-region":
        metrics[0]["blocks"] = []
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(metrics))
    prepared = AuditPreparation(
        str(tmp_path / "public"), str(tmp_path), "Manual", "hash", "renderer", 1
    )
    result = await summarize_audit_activity(
        prepared,
        [
            PageAudit(
                0, failure != "unavailable", str(path), "Chrome", error="browser absent"
            )
        ],
    )
    assert result.pages_checked == (1 if failure == "none" else 0)
    assert result.warnings
    assert ("unavailable" in " ".join(result.warnings)) == (failure != "none")
    assert Path(result.report_path).is_file()


@pytest.fixture
def anyio_backend() -> str:
    """Use the Worker's asyncio runtime."""
    return "asyncio"
