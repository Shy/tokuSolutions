"""Resume-safe corpus migration to source-linked semantic panels."""

import asyncio
import hashlib
import json
import logging
import re
import shutil
from contextlib import AsyncExitStack
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

import click
from dotenv import load_dotenv
from pydantic import JsonValue, TypeAdapter
from temporalio.client import WorkflowExecutionStatus
from temporalio.worker import Worker

from src.activities.layout_benchmark import (
    docling_layout_activity,
    google_layout_activity,
    inspect_layout_input_activity,
)
from src.activities.publication import _prepare, _prepare_audit, _publish, _validate
from src.publication_models import AuditPreparation, PublishedManual, StagedManual
from src.quality.cleanup_context import ManualData
from src.quality.core import write_bytes
from src.quality.core import write_json as _write
from src.quality.corpus_repair import _audit
from src.quality.layout_inputs import prepare_combined_input
from src.quality.page_grouping import PagePlan
from src.quality.semantic_layout import ungroup_page
from src.semantic_models import SemanticLayoutInput
from src.temporal_client import connect_temporal
from src.worker import ACTIVITIES, WORKFLOWS
from src.workflows.combined_layout_workflow import CombinedLayoutWorkflow
from src.workflows.semantic_layout_workflow import SemanticLayoutWorkflow

ROOT = Path(__file__).resolve().parents[2]


def _completed_publication(plan: StagedManual) -> dict[str, JsonValue] | None:
    """Recover a durable publication receipt without trusting stale runner state."""
    directory = Path(plan.run_dir)
    receipt = directory / "published.json"
    if not receipt.exists():
        return None
    publication = TypeAdapter(PublishedManual).validate_json(receipt.read_bytes())
    audit = TypeAdapter(AuditPreparation).validate_json(
        (directory / "audit/prepared.json").read_bytes()
    )
    target = Path(plan.final_dir)
    if hashlib.sha256(
        (target / "translations.json").read_bytes()
    ).hexdigest() != audit.json_sha256 or any(
        hashlib.sha256((target / image).read_bytes()).hexdigest() != checksum
        for image, checksum in audit.asset_sha256.items()
    ):
        raise ValueError("Published manual changed after its durable receipt")
    grouped = _validate(replace(plan, staging_dir=plan.final_dir), audit.page_count)
    report = directory / "audit/summary.json"
    summary = TypeAdapter(dict[str, JsonValue]).validate_json(report.read_bytes())
    if summary["pages_checked"] != audit.page_count:
        raise ValueError("Published manual has incomplete audit coverage")
    return TypeAdapter(dict[str, JsonValue]).validate_python(
        {
            "status": "published",
            "pages": audit.page_count,
            "regions_after": sum(len(page.blocks) for page in grouped.pages),
            "publication": asdict(publication),
            "audit": {
                "report_path": str(report),
                "pages_checked": audit.page_count,
                "warnings": summary["warnings"],
            },
            "recovery": "Verified durable publication receipt after runner interruption",
        }
    )


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
    run_id: str,
    manual_names: tuple[str, ...],
    regroup: bool = False,
    layout_index: Path | None = None,
    google_processor: str = "",
    docling_python: Path | None = None,
    docling_assets: Path | None = None,
    address: str = "localhost:7233",
    manual_concurrency: int = 3,
) -> None:
    """Repair independent manuals, audit, publish with backups, and retain failures."""
    if not 1 <= manual_concurrency <= 8:
        raise ValueError("Manual concurrency must be between one and eight")
    directory = ROOT / "benchmark-runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    status_path = directory / "status.json"
    state = json.loads(status_path.read_bytes()) if status_path.exists() else {}
    await asyncio.to_thread(warm_semantic_cache, ROOT)
    combined = bool(google_processor)
    if combined and (docling_python is None or docling_assets is None or layout_index):
        raise ValueError(
            "Combined parsing requires Docling paths and no prebuilt layout index"
        )
    if combined:
        configuration = {
            "manuals": sorted(manual_names),
            "regroup": regroup,
            "google_processor": google_processor,
            "docling_python": str(docling_python),
            "docling_assets": str(docling_assets),
            "address": address,
        }
        frozen_configuration = directory / "configuration.json"
        if (
            frozen_configuration.exists()
            and json.loads(frozen_configuration.read_bytes()) != configuration
        ):
            raise ValueError("Corpus configuration changed; choose a new run ID")
        _write(frozen_configuration, configuration)
    client = await connect_temporal(address)
    queue = "semantic-" + hashlib.sha256(run_id.encode()).hexdigest()[:16]
    slots = asyncio.Semaphore(3)
    manuals = asyncio.Semaphore(manual_concurrency)
    paths = (
        [ROOT / "manuals" / name / "translations.json" for name in manual_names]
        if manual_names
        else sorted((ROOT / "manuals").glob("*/translations.json"))
    )
    frozen_glossary = directory / "glossary.json"
    if not frozen_glossary.exists():
        shutil.copy2(ROOT / "glossary.json", frozen_glossary)
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(
            Worker(
                client,
                task_queue=queue,
                workflows=WORKFLOWS,
                activities=ACTIVITIES,
                max_concurrent_activities=8 if combined else 6,
                graceful_shutdown_timeout=timedelta(minutes=5),
            )
        )
        if combined:
            await stack.enter_async_context(
                Worker(
                    client,
                    task_queue=queue + "-parsers",
                    activities=[
                        inspect_layout_input_activity,
                        docling_layout_activity,
                        google_layout_activity,
                    ],
                    max_concurrent_activities=2,
                    graceful_shutdown_timeout=timedelta(minutes=5),
                )
            )

        async def repair(path: Path) -> None:
            name = path.parent.name
            async with manuals:
                record = state.setdefault(name, {})
                if record.get("status") == "published":
                    return
                try:
                    plan = _prepare(str(path.parent), f"{run_id}:{name}")
                    completed = _completed_publication(plan)
                    if completed is not None:
                        record.update(completed)
                        record.pop("error", None)
                        _write(status_path, state)
                        print(f"RECOVERED PUBLICATION {name}", flush=True)
                        return
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
                    record["workflow_id"] = identifier
                    _write(status_path, state)
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
                        semantic = SemanticLayoutInput(
                            str(stage / "translations.json"),
                            plan.run_dir,
                            len(baseline["pages"]),
                            str(pdf) if pdf.is_file() else "",
                            str(frozen_glossary),
                            str(layout_index.absolute()) if layout_index else "",
                        )
                        if combined:
                            assert (
                                docling_python is not None
                                and docling_assets is not None
                            )
                            request = await asyncio.to_thread(
                                prepare_combined_input,
                                semantic,
                                queue + "-parsers",
                                docling_python,
                                docling_assets,
                                google_processor,
                            )
                            _write(
                                Path(plan.run_dir) / "combined-input.json",
                                asdict(request),
                            )
                            handle = await client.start_workflow(
                                CombinedLayoutWorkflow.run,
                                request,
                                id=identifier,
                                task_queue=queue,
                            )
                        else:
                            handle = await client.start_workflow(
                                SemanticLayoutWorkflow.run,
                                semantic,
                                id=identifier,
                                task_queue=queue,
                            )
                    warnings = await handle.result()
                    grouped = _validate(plan, len(baseline["pages"]))
                    if combined:
                        for before, after in zip(
                            baseline["pages"], grouped.pages, strict=True
                        ):
                            if before.get("layout_provenance", {}).get("ocr_lines") != (
                                after.layout_provenance or {}
                            ).get("ocr_lines"):
                                raise ValueError(
                                    "Combined regeneration changed existing OCR lines"
                                )
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
                    if summary.pages_checked != len(grouped.pages):
                        raise ValueError(
                            "Browser audit did not cover every page; publication withheld"
                        )
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
    "--google-processor",
    default="",
    help="Enable combined Docling + Google v1.0 parsing.",
)
@click.option("--docling-python", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--docling-assets", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option("--address", default="localhost:7233")
@click.option("--manual-concurrency", type=click.IntRange(1, 8), default=3)
@click.option(
    "--layout-index",
    type=click.Path(exists=True, path_type=Path),
    help="Frozen structural layouts for exactly one selected manual.",
)
@click.option(
    "--regroup",
    is_flag=True,
    help="Explicitly regenerate panels in already grouped manuals; replaces previous prose edits.",
)
def main(
    run_id: str,
    manual_names: tuple[str, ...],
    regroup: bool,
    layout_index: Path | None,
    google_processor: str,
    docling_python: Path | None,
    docling_assets: Path | None,
    address: str,
    manual_concurrency: int,
) -> None:
    """Repair the corpus using the same semantic stage as new translations."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
        raise click.ClickException("Run ID must be a simple directory name")
    if layout_index and len(manual_names) != 1:
        raise click.ClickException("A layout index requires exactly one --manual")
    load_dotenv(ROOT / ".env", override=True)
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(
        run(
            run_id,
            manual_names,
            regroup,
            layout_index,
            google_processor,
            docling_python,
            docling_assets,
            address,
            manual_concurrency,
        )
    )


if __name__ == "__main__":
    main()
