"""Resume-safe corpus migration to source-linked semantic panels."""

import asyncio
import hashlib
import json
import logging
import re
import shutil
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import click
from dotenv import load_dotenv
from pydantic import TypeAdapter
from temporalio.client import WorkflowExecutionStatus
from temporalio.worker import Worker

from src.activities.publication import _prepare, _prepare_audit, _publish, _validate
from src.quality.cleanup_context import ManualData
from src.quality.core import write_bytes
from src.quality.corpus_repair import _audit
from src.quality.page_grouping import PagePlan
from src.quality.semantic_layout import ungroup_page
from src.semantic_models import SemanticLayoutInput
from src.temporal_client import connect_temporal
from src.worker import ACTIVITIES, WORKFLOWS
from src.workflows.semantic_layout_workflow import SemanticLayoutWorkflow

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, value: object) -> None:
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def warm_semantic_cache(root: Path) -> None:
    """Reuse exact-input replies and identical scan OCR across run IDs."""
    for page_dir in (root / "benchmark-runs/publication").glob("*/semantic/page-*"):
        image, lines = page_dir / "scan.jpg", page_dir / "lines.json"
        if image.exists() and lines.exists():
            target = (
                root
                / "benchmark-runs/semantic-lines"
                / (hashlib.sha256(image.read_bytes()).hexdigest() + ".json")
            )
            if not target.exists():
                write_bytes(target, lines.read_bytes())
        for path in page_dir.glob("*.reply.json"):
            saved = json.loads(path.read_bytes())
            fingerprint = saved.get("request_sha256", "")
            if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
                continue
            target = root / "benchmark-runs/semantic-replies" / (fingerprint + ".json")
            if not target.exists():
                write_bytes(target, path.read_bytes())


async def run(
    run_id: str, manual_names: tuple[str, ...], regroup: bool = False
) -> None:
    """Repair independent manuals, audit, publish with backups, and retain failures."""
    directory = ROOT / "benchmark-runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    status_path = directory / "status.json"
    state = json.loads(status_path.read_bytes()) if status_path.exists() else {}
    await asyncio.to_thread(warm_semantic_cache, ROOT)
    client = await connect_temporal()
    queue = "semantic-" + hashlib.sha256(run_id.encode()).hexdigest()[:16]
    slots = asyncio.Semaphore(3)
    manuals = asyncio.Semaphore(3)
    paths = (
        [ROOT / "manuals" / name / "translations.json" for name in manual_names]
        if manual_names
        else sorted((ROOT / "manuals").glob("*/translations.json"))
    )
    frozen_glossary = directory / "glossary.json"
    if not frozen_glossary.exists():
        shutil.copy2(ROOT / "glossary.json", frozen_glossary)
    async with Worker(
        client,
        task_queue=queue,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
        max_concurrent_activities=6,
        graceful_shutdown_timeout=timedelta(minutes=5),
    ):

        async def repair(path: Path) -> None:
            name = path.parent.name
            async with manuals:
                record = state.setdefault(name, {})
                if record.get("status") == "published":
                    return
                try:
                    plan = _prepare(str(path.parent), f"{run_id}:{name}")
                    stage = Path(plan.staging_dir)
                    baseline_path = Path(plan.source_snapshot)
                    if not baseline_path.exists():
                        baseline = json.loads(path.read_bytes())
                        if baseline["meta"].get("semantic_layout") == 1 and not regroup:
                            record["status"] = "already_grouped"
                            _write(status_path, state)
                            return
                        shutil.copytree(path.parent, stage, dirs_exist_ok=True)
                        write_bytes(baseline_path, path.read_bytes())
                        if regroup:
                            restored = TypeAdapter(ManualData).validate_python(baseline)
                            restored["pages"] = [
                                ungroup_page(page) for page in restored["pages"]
                            ]
                            _write(stage / "translations.json", restored)
                    baseline = json.loads(baseline_path.read_bytes())
                    pdf = ROOT / "pdfs" / str(baseline["meta"].get("source", ""))
                    if not pdf.is_file():
                        pdf = ROOT / "pdfs" / f"{name}.pdf"
                    record.update(
                        status="grouping",
                        run_dir=plan.run_dir,
                        pages=len(baseline["pages"]),
                        regions_before=sum(
                            len(page["blocks"]) for page in baseline["pages"]
                        ),
                    )
                    _write(status_path, state)
                    identifier = (
                        "semantic-repair-"
                        + hashlib.sha256(f"{run_id}:{name}".encode()).hexdigest()[:26]
                    )
                    print(f"GROUP {name}: {record['pages']} pages", flush=True)
                    handle = client.get_workflow_handle(
                        identifier, result_type=list[str]
                    )
                    try:
                        description = await handle.describe()
                    except Exception as error:
                        if "not found" not in str(error).lower():
                            raise
                        description = None
                    if description is None or description.status not in {
                        WorkflowExecutionStatus.RUNNING,
                        WorkflowExecutionStatus.COMPLETED,
                    }:
                        handle = await client.start_workflow(
                            SemanticLayoutWorkflow.run,
                            SemanticLayoutInput(
                                str(stage / "translations.json"),
                                plan.run_dir,
                                len(baseline["pages"]),
                                str(pdf) if pdf.is_file() else "",
                                str(frozen_glossary),
                            ),
                            id=identifier,
                            task_queue=queue,
                        )
                    warnings = await handle.result()
                    grouped = _validate(plan, len(baseline["pages"]))
                    record.update(
                        status="auditing",
                        regions_after=sum(len(page.blocks) for page in grouped.pages),
                        guides_removed=sum(
                            len(
                                PagePlan.model_validate(
                                    (page.layout_provenance or {})["plan"]
                                ).furigana
                            )
                            for page in grouped.pages
                        ),
                        warnings=warnings,
                    )
                    _write(status_path, state)
                    prepared = await asyncio.to_thread(
                        _prepare_audit, plan, len(grouped.pages)
                    )
                    summary = await _audit(client, prepared, queue, slots)
                    published = await asyncio.to_thread(
                        _publish,
                        plan,
                        prepared,
                        warnings + summary.warnings,
                        summary.report_path,
                    )
                    record.update(
                        status="published",
                        audit=asdict(summary),
                        publication=asdict(published),
                    )
                    _write(status_path, state)
                    print(
                        f"DONE {name}: {record['regions_before']} -> {record['regions_after']} regions; {record['guides_removed']} furigana omitted",
                        flush=True,
                    )
                except Exception as error:  # noqa: BLE001 — preserve failures while independent manuals finish.
                    record.update(status="failed", error=str(error))
                    _write(status_path, state)
                    print(f"FAILED {name}: {error}", flush=True)

        await asyncio.gather(*(repair(path) for path in paths))
    failures = [name for name, record in state.items() if record["status"] == "failed"]
    if failures:
        raise RuntimeError("Unpublished repairs retained: " + ", ".join(failures))
    print(f"COMPLETE {status_path}", flush=True)


@click.command()
@click.option("--run-id", required=True)
@click.option("--manual", "manual_names", multiple=True)
@click.option(
    "--regroup",
    is_flag=True,
    help="Explicitly regenerate panels in already grouped manuals; replaces previous prose edits.",
)
def main(run_id: str, manual_names: tuple[str, ...], regroup: bool) -> None:
    """Repair the corpus using the same semantic stage as new translations."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
        raise click.ClickException("Run ID must be a simple directory name")
    load_dotenv(ROOT / ".env", override=True)
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(run_id, manual_names, regroup))


if __name__ == "__main__":
    main()
