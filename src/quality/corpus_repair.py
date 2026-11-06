"""Audit existing translations and repair drafts without rerunning OCR/translation."""

import asyncio
import hashlib
import json
import logging
import math
import re
import shutil
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import cast

import click
from dotenv import load_dotenv
from pydantic import TypeAdapter
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import ActivityIDConflictPolicy, RetryPolicy
from temporalio.worker import Worker

from src.activities.agent_pipeline import (
    apply_agent_cleanup_activity,
    prepare_agent_cleanup_activity,
    save_agent_reply_activity,
)
from src.activities.cleanup import CleanupResult
from src.activities.publication import _prepare, _prepare_audit, _publish
from src.activities.render_audit import _summarize, audit_page_activity
from src.agent_models import AgentCallInput, AgentCleanupPlan, AgentReply
from src.cleanup import stage1_ftfy_cleanup, stage2_rule_based_cleanup
from src.publication_models import (
    AuditPreparation,
    AuditSummary,
    PageAudit,
    StagedManual,
)
from src.quality.cleanup_context import (
    EditBlock,
    ManualData,
    correction_allowed,
    literal_label,
)
from src.quality.core import write_bytes
from src.quality.core import write_json as _write
from src.quality.glossary import (
    Glossary,
    glossary_flags,
    load_glossary,
    resolve_glossary,
)
from src.quality.models import Block
from src.quality.pipeline import infer_franchise
from src.quality.quantities import numeric_tokens
from src.temporal_client import connect_temporal
from src.worker import ACTIVITIES, WORKFLOWS
from src.workflows.agent_workflows import DurableAgentWorkflow

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Issue:
    """An advisory source-linked finding, not a certified translation error."""

    block_id: str
    kind: str
    reason: str


def canonical_box(box: list[float]) -> list[float]:
    """Normalize reversed endpoints only; reject empty or out-of-page geometry."""
    if len(box) != 4 or not all(math.isfinite(value) for value in box):
        raise ValueError("Invalid source box")
    left, top, width, height = box
    normalized = [
        round(min(left, left + width), 4),
        round(min(top, top + height), 4),
        round(abs(width), 4),
        round(abs(height), 4),
    ]
    left, top, width, height = normalized
    if (
        min(left, top) < 0
        or min(width, height) <= 0
        or left + width > 1.001
        or top + height > 1.001
    ):
        raise ValueError("Cannot repair source box without inventing geometry")
    return normalized


def find_issues(data: ManualData, glossary: Glossary, product: str) -> list[Issue]:
    """Find literal-label, scoped terminology and numeric-coverage concerns offline."""
    franchise = infer_franchise(
        product
        + "\n"
        + "\n".join(block["text"] for page in data["pages"] for block in page["blocks"])
    )
    resolution = resolve_glossary(glossary, product=product, franchise=franchise)
    issues = []
    for page_index, page in enumerate(data["pages"]):
        for block_index, block in enumerate(page["blocks"]):
            identifier = f"{page_index}-{block_index}"
            label = literal_label(block["text"])
            if label is not None and label != block["translation"].strip():
                issues.append(
                    Issue(
                        identifier,
                        "literal-label",
                        f"Expected exact source label {label}",
                    )
                )
            missing = numeric_tokens(block["text"]) - numeric_tokens(
                block["translation"]
            )
            if missing:
                issues.append(
                    Issue(
                        identifier,
                        "numbers",
                        f"Source numeric tokens absent from English: {dict(missing)}; context/number words may explain this",
                    )
                )
            if block["text"].strip():
                source = Block(
                    id=identifier,
                    text=block["text"],
                    translation=block["translation"],
                    bbox=(0, 0, 1, 1),
                )
                issues.extend(
                    Issue(identifier, flag.kind, flag.reason)
                    for flag in glossary_flags([source], resolution, glossary)
                )
    return issues


def repair_constraints(data: ManualData, glossary: Glossary, product: str) -> Glossary:
    """Resolve exact product IDs before custom display titles reach generic cleanup."""
    scoped = resolve_glossary(
        glossary,
        product=product,
        franchise=infer_franchise(
            product
            + "\n"
            + "\n".join(
                block["text"] for page in data["pages"] for block in page["blocks"]
            )
        ),
    )
    return Glossary(
        entries=[
            entry.model_copy(update={"product": None, "franchise": None})
            for entry in scoped.terms.values()
        ]
    )


def guard_repairs(
    before: ManualData, after: ManualData, glossary: Glossary, product: str
) -> list[Issue]:
    """Preserve owner metadata/source and reject newly introduced numeric/label/name loss."""
    constraints = repair_constraints(before, glossary, product)
    if len(before["pages"]) != len(after["pages"]):
        raise ValueError("Repair changed page coverage")
    rejected = []
    for page_index, (original, changed) in enumerate(
        zip(before["pages"], after["pages"], strict=True)
    ):
        if original.get("image") != changed.get("image") or len(
            original["blocks"]
        ) != len(changed["blocks"]):
            raise ValueError("Repair changed source coverage")
        for block_index, (source, candidate) in enumerate(
            zip(original["blocks"], changed["blocks"], strict=True)
        ):
            if source["text"] != candidate["text"] or source.get(
                "bbox"
            ) != candidate.get("bbox"):
                raise ValueError("Repair changed Japanese or geometry")
            if source["translation"] == candidate["translation"]:
                continue
            identifier = f"{page_index}-{block_index}"
            source_numbers = numeric_tokens(source["text"])
            old_numbers = numeric_tokens(source["translation"])
            new_numbers = numeric_tokens(candidate["translation"])
            new_loss = (source_numbers - new_numbers) - (source_numbers - old_numbers)
            new_invention = new_numbers - (source_numbers | old_numbers)
            editable = EditBlock(
                index=identifier,
                original=source["text"],
                translation=source["translation"],
                bbox=cast(tuple[float, float, float, float], tuple(source["bbox"]))
                if "bbox" in source
                else None,
            )
            if (
                new_loss
                or new_invention
                or not correction_allowed(
                    editable, candidate["translation"], constraints
                )
            ):
                rejected.append(
                    Issue(
                        identifier,
                        "rejected-repair",
                        "New numeric discrepancy or failed literal/glossary guard",
                    )
                )
                candidate["translation"] = source["translation"]
    after["meta"] = deepcopy(before["meta"])
    after["meta"]["pages"] = len(after["pages"])
    after["meta"]["blocks"] = sum(len(page["blocks"]) for page in after["pages"])
    return rejected


def _read(path: Path) -> ManualData:
    return cast(ManualData, json.loads(path.read_bytes()))


async def _audit(
    client: Client, prepared: AuditPreparation, queue: str, slots: asyncio.Semaphore
) -> AuditSummary:
    async def one(page: int) -> PageAudit:
        async with slots:
            try:
                return await client.execute_activity(
                    audit_page_activity,
                    args=[prepared, page],
                    id="corpus-audit-"
                    + hashlib.sha256(
                        f"{prepared.audit_dir}:{page}".encode()
                    ).hexdigest()[:32],
                    task_queue=queue,
                    id_conflict_policy=ActivityIDConflictPolicy.USE_EXISTING,
                    start_to_close_timeout=timedelta(minutes=2),
                    schedule_to_close_timeout=timedelta(minutes=10),
                    heartbeat_timeout=timedelta(seconds=25),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
            except Exception as error:  # noqa: BLE001 — unavailable browser is advisory.
                return PageAudit(page, False, error=str(error))

    pages = await asyncio.gather(*(one(page) for page in range(prepared.page_count)))
    return await asyncio.to_thread(_summarize, prepared, pages)


async def repair_batches(
    client: Client,
    queue: str,
    stage_path: Path,
    product: str,
    constraints: Path,
    targets: list[str] | None,
    slots: asyncio.Semaphore,
) -> CleanupResult:
    """Reuse immutable batch replies and retry only missing model work on recovery."""
    candidates = [
        path.parent
        for path in (ROOT / "benchmark-runs/agent-pipeline/cleanup").glob(
            "*/source-path.txt"
        )
        if path.read_text() == str(stage_path.resolve())
        and (path.parent / "plan.json").exists()
    ]
    if candidates:
        directory = max(
            candidates, key=lambda path: len(list(path.glob("reply-*.json")))
        )
        plan = AgentCleanupPlan.model_validate_json(
            (directory / "plan.json").read_bytes()
        )
    else:
        plan = await client.execute_activity(
            prepare_agent_cleanup_activity,
            args=[str(stage_path), product, "", str(constraints), targets],
            id="corpus-prepare-v2-"
            + hashlib.sha256(str(stage_path).encode()).hexdigest()[:24],
            task_queue=queue,
            start_to_close_timeout=timedelta(minutes=3),
        )
    if plan.error:
        return {"success": False, "error": plan.error}

    async def batch(index: int) -> None:
        if (Path(plan.directory) / f"reply-{index}.json").exists():
            return
        async with slots:
            identifier = (
                "corpus-batch-v2-"
                + hashlib.sha256(f"{plan.directory}:{index}".encode()).hexdigest()[:28]
            )
            handle = client.get_workflow_handle(identifier, result_type=AgentReply)
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
                    DurableAgentWorkflow.run,
                    AgentCallInput(
                        kind="cleanup",
                        instructions="",
                        prompt="",
                        model="",
                        cleanup_plan=plan,
                        batch_index=index,
                    ),
                    id=identifier,
                    task_queue=queue,
                )
            reply = await handle.result()
            await client.execute_activity(
                save_agent_reply_activity,
                args=[plan, index, reply],
                id=identifier + "-save",
                task_queue=queue,
                start_to_close_timeout=timedelta(minutes=1),
            )
            print(
                f"BATCH {stage_path.parent.name} {index + 1}/{plan.batch_count}",
                flush=True,
            )

    outcomes = await asyncio.gather(
        *(batch(index) for index in range(plan.batch_count)), return_exceptions=True
    )
    errors = [str(result) for result in outcomes if isinstance(result, BaseException)]
    if errors:
        return {"success": False, "error": "; ".join(errors)}
    # Restoring a private frozen draft makes recovery independent of interrupted
    # formatting/application; the published manual remains protected by its hash.
    write_bytes(stage_path, (Path(plan.directory) / "source.json").read_bytes())
    return await client.execute_activity(
        apply_agent_cleanup_activity,
        plan,
        id="corpus-apply-v2-"
        + hashlib.sha256(plan.directory.encode()).hexdigest()[:24],
        task_queue=queue,
        start_to_close_timeout=timedelta(minutes=2),
    )


async def run(root: Path, run_id: str, legacy: set[str], apply: bool) -> None:
    """Resume a local corpus pass, using Temporal for paid agents and browser jobs."""
    directory = root / "benchmark-runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "status.json"
    state = (
        cast(dict[str, dict[str, object]], json.loads(state_path.read_bytes()))
        if state_path.exists()
        else {}
    )
    frozen = directory / "glossary.json"
    if not frozen.exists():
        shutil.copy2(root / "glossary.json", frozen)
    glossary = load_glossary(frozen)
    queue = "corpus-v2-" + hashlib.sha256(run_id.encode()).hexdigest()[:16]
    client = await connect_temporal()
    slots = asyncio.Semaphore(3)
    manuals = sorted((root / "manuals").glob("*/translations.json"))
    plans: dict[str, StagedManual] = {}
    baselines: dict[str, ManualData] = {}
    async with Worker(
        client,
        task_queue=queue,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
        max_concurrent_activities=18,
        graceful_shutdown_timeout=timedelta(minutes=5),
    ):
        # The first pass is read-only with respect to every published manual.
        for path in manuals:
            name = path.parent.name
            if state.get(name, {}).get("status") == "published":
                continue
            plan = await asyncio.to_thread(
                _prepare, str(path.parent), f"{run_id}:{name}"
            )
            plans[name] = plan
            original = Path(plan.run_dir) / "original.json"
            if not original.exists():
                shutil.copy2(path, original)
            baseline = _read(original)
            geometry = 0
            for page in baseline["pages"]:
                for block in page["blocks"]:
                    normalized = canonical_box(block["bbox"])
                    geometry += normalized != block["bbox"]
                    block["bbox"] = normalized
            baselines[name] = baseline
            stage = Path(plan.staging_dir)
            if not (stage / "translations.json").exists():
                shutil.copytree(path.parent, stage, dirs_exist_ok=True)
                _write(stage / "translations.json", baseline)
            if not Path(plan.source_snapshot).exists():
                _write(Path(plan.source_snapshot), baseline)
            issues = find_issues(baseline, glossary, name)
            record = state.setdefault(name, {})
            record.update(
                status="auditing",
                older_cleanup=name in legacy,
                geometry_repairs=geometry,
                issues_before=[asdict(issue) for issue in issues],
                run_dir=plan.run_dir,
            )
            # Separate audit namespace prevents before/after fingerprint collisions.
            before_dir = Path(plan.run_dir) / "before"
            before_stage = before_dir / "manual"
            if not before_stage.exists():
                shutil.copytree(stage, before_stage)
                _write(before_stage / "translations.json", baseline)
            before_plan = StagedManual(
                str(before_dir),
                str(before_stage),
                plan.final_dir,
                plan.source_snapshot,
                plan.previous_sha256,
            )
            prepared = await asyncio.to_thread(
                _prepare_audit, before_plan, len(baseline["pages"])
            )
            record["audit_before_preparation"] = asdict(prepared)
            _write(state_path, state)

        async def audit_before(name: str) -> None:
            prepared = TypeAdapter(AuditPreparation).validate_python(
                state[name]["audit_before_preparation"]
            )
            summary = await _audit(client, prepared, queue, slots)
            state[name]["audit_before"] = asdict(summary)
            state[name]["status"] = "audited"
            _write(state_path, state)
            print(
                f"AUDITED {name}: {summary.pages_checked}/{prepared.page_count} pages",
                flush=True,
            )

        await asyncio.gather(*(audit_before(name) for name in plans))
        if not apply:
            print(f"Audit complete: {state_path}", flush=True)
            return
        cleanup_slots = asyncio.Semaphore(8)
        model_slots = asyncio.Semaphore(6)

        async def repair(name: str) -> None:
            async with cleanup_slots:
                plan = plans[name]
                record = state[name]
                baseline = baselines[name]
                stage_path = Path(plan.staging_dir) / "translations.json"
                draft = _read(stage_path)
                stage1_ftfy_cleanup(draft)
                stage2_rule_based_cleanup(draft)
                guard_repairs(baseline, draft, glossary, name)
                _write(stage_path, draft)
                issues = find_issues(draft, glossary, name)
                targets = sorted(
                    {
                        issue.block_id
                        for issue in issues
                        if issue.kind
                        in {"numbers", "glossary-mismatch", "literal-label"}
                    }
                )
                identifier = (
                    "corpus-cleanup-"
                    + hashlib.sha256(f"{run_id}:{name}".encode()).hexdigest()[:28]
                )
                constraints_path = Path(plan.run_dir) / "repair-glossary.json"
                _write(
                    constraints_path,
                    repair_constraints(baseline, glossary, name).model_dump(
                        mode="json"
                    ),
                )
                if name in legacy or targets:
                    record.update(
                        status="repairing",
                        workflow_id=identifier,
                        targeted_regions=len(targets),
                    )
                    _write(state_path, state)
                    print(
                        f"REPAIR {name}: {'full legacy cleanup' if name in legacy else str(len(targets)) + ' targeted regions'}",
                        flush=True,
                    )
                    old_identifier = record.get("legacy_workflow_id") or identifier
                    old_handle = client.get_workflow_handle(str(old_identifier))
                    try:
                        old_description = await old_handle.describe()
                        if old_description.status == WorkflowExecutionStatus.RUNNING:
                            await old_handle.terminate(
                                "Recovering saved corpus batches on isolated v2 queue"
                            )
                    except Exception as error:
                        if "not found" not in str(error).lower():
                            raise
                    record["legacy_workflow_id"] = str(old_identifier)
                    result = await repair_batches(
                        client,
                        queue,
                        stage_path,
                        str(baseline["meta"].get("manual_name", name)),
                        constraints_path,
                        None if name in legacy else targets,
                        model_slots,
                    )
                    record["cleanup"] = result
                    if not result.get("success"):
                        raise RuntimeError(
                            f"{name}: AI cleanup incomplete: {result.get('error')}"
                        )
                changed = _read(stage_path)
                rejected = guard_repairs(baseline, changed, glossary, name)
                record["rejected_repairs"] = [asdict(issue) for issue in rejected]
                changes = sum(
                    old["translation"] != new["translation"]
                    for before_page, after_page in zip(
                        baseline["pages"], changed["pages"], strict=True
                    )
                    for old, new in zip(
                        before_page["blocks"], after_page["blocks"], strict=True
                    )
                )
                record["english_regions_changed"] = changes
                remaining = find_issues(changed, glossary, name)
                record["issues_after"] = [asdict(issue) for issue in remaining]
                _write(stage_path, changed)
                if (
                    changes
                    or record["geometry_repairs"]
                    or changed["meta"] != baseline["meta"]
                ):
                    prepared = await asyncio.to_thread(
                        _prepare_audit, plan, len(changed["pages"])
                    )
                    summary = await _audit(client, prepared, queue, slots)
                    published = await asyncio.to_thread(
                        _publish, plan, prepared, summary.warnings, summary.report_path
                    )
                    record.update(
                        status="published",
                        audit_after=asdict(summary),
                        publication=asdict(published),
                    )
                else:
                    record["status"] = "unchanged"
                _write(state_path, state)
                print(
                    f"DONE {name}: {changes} English regions changed; {record['geometry_repairs']} reversed boxes normalized; {len(remaining)} advisory findings remain",
                    flush=True,
                )

        async def guarded_repair(name: str) -> None:
            try:
                await repair(name)
            except Exception as error:  # noqa: BLE001 — retain failed drafts and finish independent manuals.
                state[name].update(status="failed", error=str(error))
                _write(state_path, state)
                print(f"FAILED {name}: {error}", flush=True)

        await asyncio.gather(*(guarded_repair(name) for name in plans))
        failed = [name for name in plans if state[name]["status"] == "failed"]
        if failed:
            raise RuntimeError(
                "Incomplete repairs retained in staging: " + ", ".join(failed)
            )
    print(f"Corpus complete: {state_path}", flush=True)


@click.command()
@click.option("--run-id", required=True)
@click.option("--legacy-list", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--apply", is_flag=True, help="Repair and publish after the read-only corpus audit."
)
def main(run_id: str, legacy_list: Path | None, apply: bool) -> None:
    """Audit the corpus, optionally applying scoped repairs with local backups."""
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", run_id):
        raise click.ClickException("Run ID must be a simple directory name")
    load_dotenv(ROOT / ".env", override=True)
    legacy = set(json.loads(legacy_list.read_bytes())) if legacy_list else set()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(ROOT, run_id, legacy, apply))


if __name__ == "__main__":
    main()
