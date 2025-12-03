#!/usr/bin/env python
"""
Toku - Unified CLI for toy manual translation system.

Commands:
  worker    Start Temporal workers
  translate Translate a PDF manual
  serve     Start web viewer server
  add-url   Add source URL to a manual
  reindex   Regenerate main index
"""

import asyncio
import sys
import time
from pathlib import Path

import click
from temporalio.client import Client

from docai_workflow import DocAITranslateWorkflow, DocAITranslateInput

TASK_QUEUE = "pdf-translation"


@click.group()
def cli():
    """Toku - Toy manual translation system."""
    pass


@cli.command()
@click.option(
    "--count", "-c", default=3, help="Number of workers to start (default: 3)"
)
def worker(count):
    """Start Temporal worker(s)."""
    from worker import main as worker_main

    if count == 1:
        click.echo("Starting 1 worker...")
        asyncio.run(worker_main())
    else:
        import subprocess

        click.echo(f"Starting {count} workers...")
        processes = []
        for i in range(count):
            proc = subprocess.Popen(
                [sys.executable, "worker.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append(proc)
            click.echo(f"  Worker {i+1} started (PID: {proc.pid})")

        click.echo("\nPress Ctrl+C to stop all workers...")
        try:
            for proc in processes:
                proc.wait()
        except KeyboardInterrupt:
            click.echo("\nStopping workers...")
            for proc in processes:
                proc.terminate()


@cli.command()
@click.argument("pdf_path", type=click.Path(exists=True))
@click.option("--source-lang", default="ja", help="Source language (default: ja)")
@click.option("--target-lang", default="en", help="Target language (default: en)")
@click.option(
    "--workers", "-w", default=3, help="Number of workers to start (default: 3)"
)
def translate(pdf_path, source_lang, target_lang, workers):
    """Translate a PDF manual using Document AI.

    Automatically starts workers, runs translation, and stops workers when done.
    """
    import subprocess

    worker_processes = []

    try:
        # Start workers
        click.echo(f"Starting {workers} worker(s)...")
        for _ in range(workers):
            proc = subprocess.Popen(
                [sys.executable, "worker.py"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            worker_processes.append(proc)

        click.echo(f"✓ Workers started\n")

        # Give workers a moment to initialize
        time.sleep(2)

        async def run_translation():
            client = await Client.connect("localhost:7233")

            input_data = DocAITranslateInput(
                pdf_path=pdf_path,
                source_language=source_lang,
                target_language=target_lang,
            )

            # Create unique workflow ID
            filename = Path(pdf_path).name.replace(" ", "_")
            timestamp = int(time.time())
            workflow_id = f"docai-translate-{filename}-{timestamp}"

            click.echo(f"Translating: {pdf_path}")
            click.echo(f"  Source: {source_lang} → Target: {target_lang}\n")

            # Run workflow
            result = await client.execute_workflow(
                DocAITranslateWorkflow.run,
                input_data,
                id=workflow_id,
                task_queue=TASK_QUEUE,
            )

            return result

        result = asyncio.run(run_translation())

        click.echo("")
        if result.success:
            click.secho("✓ Translation successful!", fg="green", bold=True)
            click.echo(f"  OCR Blocks: {result.ocr_blocks}")
            click.echo(f"  Translated Blocks: {result.translated_blocks}")
            click.echo(f"  Output: {result.output_dir}")
            click.echo(f"  Viewer: {result.html_path}")
        else:
            click.secho("✗ Translation failed", fg="red", bold=True)
            click.echo(f"  Error: {result.error}")
            sys.exit(1)

    finally:
        # Always stop workers
        click.echo("\nStopping workers...")
        for proc in worker_processes:
            proc.terminate()

        # Wait for graceful shutdown
        time.sleep(1)

        # Force kill any remaining
        for proc in worker_processes:
            try:
                proc.kill()
            except:
                pass

        click.echo("✓ Workers stopped")


@cli.command()
@click.option(
    "--port", "-p", default=5000, help="Port to run server on (default: 5000)"
)
@click.option(
    "--host", "-h", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)"
)
def serve(port, host):
    """Start the web viewer server."""
    from server import app

    click.echo(f"Starting web server at http://{host}:{port}")
    click.echo("Press Ctrl+C to stop")
    app.run(host=host, port=port)


@cli.command()
@click.argument("manual_name")
@click.argument("source_url")
def add_url(manual_name, source_url):
    """Add source URL to a manual's metadata.

    Example:
      toku add-url "CSM-Fang-Memory" "https://toy.bandai.co.jp/manuals/pdf.php?id=123"
    """
    import json
    from docai_activities import _generate_main_index

    output_dir = Path("output")
    json_path = output_dir / manual_name / "translations.json"

    if not json_path.exists():
        click.secho(f"✗ Error: {json_path} not found", fg="red")
        click.echo("\nAvailable manuals:")
        for folder in sorted(output_dir.iterdir()):
            if folder.is_dir() and (folder / "translations.json").exists():
                click.echo(f"  - {folder.name}")
        sys.exit(1)

    # Read existing data
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Update metadata
    data["meta"]["source_url"] = source_url

    # Write back
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    click.secho(f"✓ Added source URL to {manual_name}", fg="green")
    click.echo(f"  URL: {source_url}")

    # Regenerate main index
    click.echo("  Regenerating main index...")
    _generate_main_index(output_dir)
    click.secho("✓ Done!", fg="green")


@cli.command()
def reindex():
    """Regenerate the main index from all translated manuals."""
    from docai_activities import _generate_main_index

    output_dir = Path("output")

    if not output_dir.exists():
        click.secho("✗ Error: output/ directory not found", fg="red")
        sys.exit(1)

    click.echo("Regenerating main index...")
    _generate_main_index(output_dir)
    click.secho("✓ Main index regenerated!", fg="green")
    click.echo(f"  Location: {output_dir / 'index.html'}")


@cli.command()
def list():
    """List all translated manuals."""
    import json

    output_dir = Path("output")

    if not output_dir.exists():
        click.secho("✗ Error: output/ directory not found", fg="red")
        sys.exit(1)

    manuals = []
    for folder in sorted(output_dir.iterdir()):
        if not folder.is_dir():
            continue

        json_path = folder / "translations.json"
        if not json_path.exists():
            continue

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("meta", {})
        pages = data.get("pages", [])

        manuals.append(
            {
                "name": folder.name,
                "source": meta.get("source", ""),
                "pages": len(pages),
                "blocks": sum(len(p.get("blocks", [])) for p in pages),
                "has_url": bool(meta.get("source_url")),
            }
        )

    if not manuals:
        click.echo("No translated manuals found.")
        return

    click.echo(f"\nFound {len(manuals)} translated manual(s):\n")

    for m in manuals:
        click.echo(f"  {click.style(m['name'], fg='cyan', bold=True)}")
        click.echo(f"    Source: {m['source']}")
        click.echo(f"    Pages: {m['pages']} | Blocks: {m['blocks']}")
        if m["has_url"]:
            click.echo(f"    {click.style('✓', fg='green')} Source URL configured")
        else:
            click.echo(f"    {click.style('○', fg='yellow')} No source URL")
        click.echo("")


if __name__ == "__main__":
    cli()
