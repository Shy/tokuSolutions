"""Staged publication preserves edited manuals and refuses incomplete output."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from src.activities.publication import (
    prepare_audit_activity,
    prepare_publication_activity,
    publish_manual_activity,
)


def document() -> dict[str, object]:
    """Use a minimal valid, source-preserving manual."""
    return {
        "meta": {"source": "scan.pdf"},
        "pages": [
            {
                "image": "pages/page-0.webp",
                "blocks": [
                    {
                        "text": "取扱説明書",
                        "translation": "Instruction manual",
                        "bbox": [0.1, 0.1, 0.5, 0.1],
                    }
                ],
            }
        ],
    }


def fill_stage(stage_dir: str, source_snapshot: str) -> None:
    """Write a completed staged translation and immutable pre-cleanup source."""
    directory = Path(stage_dir)
    (directory / "pages").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 150), "white").save(directory / "pages/page-0.webp")
    raw = json.dumps(document()).encode()
    (directory / "translations.json").write_bytes(raw)
    Path(source_snapshot).write_bytes(raw)


@pytest.mark.anyio
async def test_staging_is_invisible_until_publication_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    """Publish once, generate a complete catalog, and retain replaced data as a backup."""
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "web/index.html").write_text("viewer")
    existing = tmp_path / "manuals/Example"
    existing.mkdir(parents=True)
    (existing / "translations.json").write_text(
        json.dumps({"meta": {"source": "old.pdf"}, "pages": []})
    )
    with (
        patch("src.activities.publication.ROOT", tmp_path),
        patch(
            "src.activities.publication.activity.info",
            return_value=SimpleNamespace(workflow_run_id="test-run"),
        ),
    ):
        plan = await prepare_publication_activity("manuals/Example")
        fill_stage(plan.staging_dir, plan.source_snapshot)
        assert (
            json.loads((existing / "translations.json").read_text())["meta"]["source"]
            == "old.pdf"
        )
        audit = await prepare_audit_activity(plan, 1)
        result = await publish_manual_activity(
            plan, audit, [], str(Path(plan.run_dir) / "audit-summary.json")
        )
        again = await publish_manual_activity(
            plan, audit, [], str(Path(plan.run_dir) / "audit-summary.json")
        )
    assert result == again
    assert (
        json.loads((existing / "translations.json").read_text())["meta"]["source"]
        == "scan.pdf"
    )
    assert (
        json.loads((Path(result.backup_dir) / "translations.json").read_text())["meta"][
            "source"
        ]
        == "old.pdf"
    )
    assert (
        json.loads((tmp_path / "web/meta.json").read_text())["stats"]["total_manuals"]
        == 1
    )


@pytest.mark.anyio
async def test_concurrent_edits_are_not_overwritten(tmp_path: Path) -> None:
    """A human or another run changing the target wins over stale staged output."""
    existing = tmp_path / "manuals/Example"
    existing.mkdir(parents=True)
    path = existing / "translations.json"
    path.write_text(json.dumps(document()))
    with (
        patch("src.activities.publication.ROOT", tmp_path),
        patch(
            "src.activities.publication.activity.info",
            return_value=SimpleNamespace(workflow_run_id="conflict"),
        ),
    ):
        plan = await prepare_publication_activity("manuals/Example")
        fill_stage(plan.staging_dir, plan.source_snapshot)
        audit = await prepare_audit_activity(plan, 1)
        path.write_text("new edit")
        with pytest.raises(ValueError, match="changed"):
            await publish_manual_activity(
                plan, audit, [], str(Path(plan.run_dir) / "audit-summary.json")
            )
    assert path.read_text() == "new edit"
    assert Path(plan.staging_dir).exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "damage", ["missing-image", "source-change", "empty-english", "missing-page"]
)
async def test_integrity_errors_never_reach_publication(
    tmp_path: Path, damage: str
) -> None:
    """Technical incompleteness is a failure rather than a readability warning."""
    with (
        patch("src.activities.publication.ROOT", tmp_path),
        patch(
            "src.activities.publication.activity.info",
            return_value=SimpleNamespace(workflow_run_id=damage),
        ),
    ):
        plan = await prepare_publication_activity("manuals/Example")
        fill_stage(plan.staging_dir, plan.source_snapshot)
        path = Path(plan.staging_dir) / "translations.json"
        data = json.loads(path.read_text())
        if damage == "missing-image":
            (Path(plan.staging_dir) / "pages/page-0.webp").unlink()
        elif damage == "source-change":
            data["pages"][0]["blocks"][0]["text"] = "違う"
        elif damage == "empty-english":
            data["pages"][0]["blocks"][0]["translation"] = ""
        else:
            data["pages"] = []
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError):
            await prepare_audit_activity(plan, 1)
    assert not (tmp_path / "manuals/Example").exists()


@pytest.mark.anyio
async def test_failed_directory_swap_restores_previous_manual(tmp_path: Path) -> None:
    """A publication error after backup does not leave the old manual missing."""
    existing = tmp_path / "manuals/Example"
    existing.mkdir(parents=True)
    old = json.dumps(document())
    (existing / "translations.json").write_text(old)
    import os

    replace = os.replace
    with (
        patch("src.activities.publication.ROOT", tmp_path),
        patch(
            "src.activities.publication.activity.info",
            return_value=SimpleNamespace(workflow_run_id="rollback"),
        ),
    ):
        plan = await prepare_publication_activity("manuals/Example")
        fill_stage(plan.staging_dir, plan.source_snapshot)
        audit = await prepare_audit_activity(plan, 1)

        def fail_stage(source: str | Path, target: str | Path) -> None:
            if Path(source) == Path(plan.staging_dir):
                raise OSError("disk error")
            replace(source, target)

        with (
            patch("src.activities.publication.os.replace", side_effect=fail_stage),
            pytest.raises(OSError),
        ):
            await publish_manual_activity(
                plan, audit, [], str(Path(plan.run_dir) / "audit-summary.json")
            )
    assert (existing / "translations.json").read_text() == old


@pytest.fixture
def anyio_backend() -> str:
    """Temporal and browser boundaries use asyncio."""
    return "asyncio"


@pytest.fixture(autouse=True)
def public_web(tmp_path: Path) -> None:
    """Provide only the fixture's public viewer input."""
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "web/index.html").write_text("viewer")


@pytest.mark.anyio
@pytest.mark.parametrize("changed", ["image", "renderer"])
async def test_audited_inputs_cannot_change_before_publication(
    tmp_path: Path, changed: str
) -> None:
    """Do not publish stale quality evidence for changed images or renderer assets."""
    with (
        patch("src.activities.publication.ROOT", tmp_path),
        patch(
            "src.activities.publication.activity.info",
            return_value=SimpleNamespace(workflow_run_id=changed),
        ),
    ):
        plan = await prepare_publication_activity("manuals/Example")
        fill_stage(plan.staging_dir, plan.source_snapshot)
        audit = await prepare_audit_activity(plan, 1)
        if changed == "image":
            Image.new("RGB", (100, 150), "black").save(
                Path(plan.staging_dir) / "pages/page-0.webp"
            )
        else:
            (tmp_path / "web/index.html").write_text("new renderer")
        with pytest.raises(ValueError, match="changed"):
            await publish_manual_activity(plan, audit, [], "audit.json")
    assert not (tmp_path / "manuals/Example").exists()
