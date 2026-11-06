"""Run independently cached layout engines through Temporal for a frozen PDF suite."""

import asyncio
import hashlib
import json
from pathlib import Path

import click
from dotenv import load_dotenv
from pydantic import TypeAdapter
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from src.activities.layout_benchmark import (
    docling_layout_activity,
    google_layout_activity,
    inspect_layout_input_activity,
    pymupdf_layout_activity,
)
from src.layout_models import LayoutBenchmarkInput
from src.temporal_client import connect_temporal
from src.workflows.layout_benchmark_workflow import LayoutBenchmarkWorkflow


async def run(request: LayoutBenchmarkInput, address: str) -> list[str]:
    """Resume an existing execution; completed providers retain per-page caches."""
    identity = (
        "layout-comparison-"
        + hashlib.sha256(
            Path(request.manifest_path).read_bytes() + repr(request).encode()
        ).hexdigest()[:24]
    )
    client = await connect_temporal(address)
    queue = "layout-comparison-" + identity[-24:]
    output = Path(request.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "execution.json").write_text(
        json.dumps(
            {
                "workflow_id": identity,
                "queue": queue,
                "address": address,
                "providers": request.providers,
            },
            indent=2,
        )
        + "\n"
    )
    async with Worker(
        client,
        task_queue=queue,
        workflows=[LayoutBenchmarkWorkflow],
        activities=[
            inspect_layout_input_activity,
            docling_layout_activity,
            pymupdf_layout_activity,
            google_layout_activity,
        ],
        max_concurrent_activities=3,
    ):
        handle = client.get_workflow_handle(identity, result_type=list[str])
        try:
            description = await handle.describe()
        except RPCError as error:
            if error.status != RPCStatusCode.NOT_FOUND:
                raise
            description = None
        if description is None or description.status not in {
            WorkflowExecutionStatus.RUNNING,
            WorkflowExecutionStatus.COMPLETED,
        }:
            handle = await client.start_workflow(
                LayoutBenchmarkWorkflow.run, request, id=identity, task_queue=queue
            )
        outcomes = TypeAdapter(list[str]).validate_python(await handle.result())
    (output / "completed.json").write_text(
        json.dumps({"artifacts": outcomes}, indent=2) + "\n"
    )
    return outcomes


@click.command()
@click.option("--manifest", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option(
    "--docling-python", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option(
    "--provider",
    "providers",
    type=click.Choice(["docling", "pymupdf", "google"]),
    multiple=True,
)
@click.option("--address", default="localhost:7235")
@click.option(
    "--google-processor",
    default="",
    help="Full projects/.../locations/.../processors/... resource.",
)
@click.option("--google-version", default="pretrained-layout-parser-v1.0-2024-06-03")
def main(
    manifest: Path,
    output: Path,
    docling_python: Path,
    providers: tuple[str, ...],
    address: str,
    google_processor: str,
    google_version: str,
) -> None:
    """Run a frozen comparison without modifying public manuals or credentials."""
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    request = LayoutBenchmarkInput(
        str(manifest.resolve()),
        str(output.resolve()),
        str(docling_python.absolute()),
        providers=providers or ("docling", "pymupdf", "google"),
        google_processor=google_processor,
        google_version=google_version,
    )
    outcomes = asyncio.run(run(request, address))
    for outcome in outcomes:
        click.echo(outcome)
    if any(outcome.startswith("FAILED ") for outcome in outcomes):
        raise click.ClickException(
            "Layout providers failed; saved evidence is retained"
        )


if __name__ == "__main__":
    main()
