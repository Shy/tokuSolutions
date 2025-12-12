#!/usr/bin/env python
"""
Toku - Unified CLI for toy manual translation system.

Commands:
  translate Translate a PDF manual (auto-manages workers)
  add-url   Add source URL to a manual
  reindex   Regenerate main index
  list      List all translated manuals
"""

import asyncio
import sys
import time
from pathlib import Path

import click
from temporalio.client import Client

from src.workflows import PDFTranslationWorkflow, PDFTranslationInput, WorkflowProgress

TASK_QUEUE = "pdf-translation"


@click.group()
def cli():
    """Toku - Toy manual translation system."""
    pass


@cli.command()
@click.argument("pdf_path_or_url")
@click.option("--source-lang", default="ja", help="Source language (default: ja)")
@click.option("--target-lang", default="en", help="Target language (default: en)")
@click.option(
    "--workers", "-w", default=3, help="Number of workers to start (default: 3)"
)
@click.option(
    "--skip-cleanup",
    is_flag=True,
    help="Skip translation cleanup (ftfy + rule-based + Gemini)",
)
def translate(pdf_path_or_url, source_lang, target_lang, workers, skip_cleanup):
    """Translate a PDF manual using Document AI.

    Accepts either a local PDF path or a URL to download from.
    Automatically starts workers, runs translation, and stops workers when done.

    Examples:
      toku translate manual.pdf
      toku translate https://example.com/manual.pdf
    """
    import subprocess
    import requests
    import tempfile

    # Check if input is a URL
    is_url = pdf_path_or_url.startswith(("http://", "https://"))
    pdf_path = pdf_path_or_url
    temp_file = None

    # Download PDF if URL provided
    if is_url:
        click.echo(f"Downloading PDF from: {pdf_path_or_url}")
        try:
            response = requests.get(pdf_path_or_url, timeout=60)
            response.raise_for_status()

            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(
                suffix=".pdf", delete=False, mode="wb"
            )
            temp_file.write(response.content)
            temp_file.close()
            pdf_path = temp_file.name

            click.secho(f"✓ Downloaded to: {pdf_path}", fg="green")
        except requests.RequestException as e:
            click.secho(f"✗ Failed to download PDF: {e}", fg="red", bold=True)
            sys.exit(1)
    else:
        # Verify local file exists
        if not Path(pdf_path).exists():
            click.secho(f"✗ File not found: {pdf_path}", fg="red", bold=True)
            sys.exit(1)

    worker_processes = []

    try:
        # Start workers
        click.echo(f"Starting {workers} worker(s)...")
        for _ in range(workers):
            proc = subprocess.Popen(
                [sys.executable, "-m", "src.worker"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            worker_processes.append(proc)

        click.echo(f"✓ Workers started\n")

        # Give workers a moment to initialize
        time.sleep(2)

        async def run_translation():
            try:
                client = await Client.connect("localhost:7233")
            except Exception as e:
                click.secho(
                    "✗ Failed to connect to Temporal server", fg="red", bold=True
                )
                click.echo(f"  Error: {e}")
                click.echo("\nMake sure Temporal is running:")
                click.echo("  temporal server start-dev")
                sys.exit(1)

            # Compute manual name and output directory (outside workflow for determinism)
            pdf_file = Path(pdf_path)
            manual_name = pdf_file.stem
            output_dir = f"manuals/{manual_name}"

            input_data = PDFTranslationInput(
                pdf_path=pdf_path,
                manual_name=manual_name,
                output_dir=output_dir,
                source_language=source_lang,
                target_language=target_lang,
                skip_cleanup=skip_cleanup,
            )

            # Create unique workflow ID with readable format
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            workflow_id = f"translate-{manual_name.replace(' ', '-')}-{timestamp}"

            click.echo(f"Translating: {pdf_path}")
            click.echo(f"  Manual: {manual_name}")
            click.echo(f"  Output: {output_dir}")
            click.echo(f"  Source: {source_lang} → Target: {target_lang}\n")

            # Start workflow (non-blocking)
            handle = await client.start_workflow(
                PDFTranslationWorkflow.run,
                input_data,
                id=workflow_id,
                task_queue=TASK_QUEUE,
            )

            # Poll workflow progress using Queries
            last_phase = None
            last_status = {}
            phase_icons = {
                "initializing": "⏳",
                "ocr": "📄",
                "translation": "🌐",
                "site_generation": "🌍",
                "cleanup": "✨",
                "complete": "✓",
            }

            # Calculate total steps (4 phases, or 3 if skip_cleanup)
            total_steps = 3 if skip_cleanup else 4

            # Progress bar
            with click.progressbar(
                length=total_steps,
                label="Overall Progress",
                show_eta=False,
                show_percent=True,
                width=40,
            ) as bar:
                completed_phases = 0
                url_prompted = False  # Track if we've already prompted for URL

                while True:
                    try:
                        # Query workflow progress
                        progress = await handle.query(PDFTranslationWorkflow.get_progress)

                        # Handle URL prompt if workflow is waiting
                        if progress.waiting_for_url and not url_prompted:
                            url_prompted = True
                            click.echo("")
                            click.secho(
                                "⏸  Product URL not found - workflow paused",
                                fg="yellow",
                                bold=True
                            )

                            from src.tokullectibles import search_tokullectibles

                            # Try searching with manual name
                            click.echo(f"Searching Tokullectibles for: {progress.manual_name}")
                            search_result = search_tokullectibles(progress.manual_name)

                            source_url = None
                            if search_result:
                                click.secho(f"✓ Found: {search_result.name}", fg="green")
                                click.echo(f"  URL: {search_result.url}")
                                if click.confirm("Use this URL?"):
                                    source_url = search_result.url

                            # If search failed or user declined, prompt for manual entry
                            if not source_url:
                                source_url = click.prompt(
                                    "Enter product URL (or press Enter to skip)",
                                    default="",
                                    show_default=False
                                )

                            # Send URL to workflow via signal (can be empty string if skipped)
                            await handle.signal(PDFTranslationWorkflow.provide_url, source_url or "")

                            if source_url:
                                click.secho(f"✓ URL provided - workflow resuming", fg="green")
                            else:
                                click.echo("  Skipped - continuing without URL")
                            click.echo("")

                        # Update display when phase changes
                        if progress.phase != last_phase:
                            # Update progress bar based on completed phases
                            # Calculate how many steps to advance
                            steps_to_advance = 0
                            if progress.phase == "translation":
                                steps_to_advance = 1 - completed_phases  # OCR done
                            elif progress.phase == "site_generation":
                                steps_to_advance = 2 - completed_phases  # OCR + Translation done
                            elif progress.phase == "cleanup":
                                steps_to_advance = 3 - completed_phases  # OCR + Translation + Site done
                            elif progress.phase == "complete":
                                steps_to_advance = total_steps - completed_phases  # All done

                            if steps_to_advance > 0:
                                bar.update(steps_to_advance)
                                completed_phases += steps_to_advance

                            last_phase = progress.phase
                            icon = phase_icons.get(progress.phase, "⏳")

                            if progress.phase == "ocr":
                                # Show page count if available
                                page_info = ""
                                if progress.pages_total > 0:
                                    page_info = f" ({progress.pages_total} pages)"
                                click.echo(
                                    f"\n{icon} {click.style('[1/4] OCR', bold=True, fg='cyan')} - Extracting text{page_info}..."
                                )
                            elif progress.phase == "translation":
                                # Show block count if available
                                block_info = ""
                                if progress.blocks_total > 0:
                                    block_info = f" ({progress.blocks_total} blocks)"
                                click.echo(
                                    f"\n{icon} {click.style('[2/4] Translation', bold=True, fg='cyan')} - Translating text{block_info}..."
                                )
                            elif progress.phase == "site_generation":
                                # Show page count if available
                                page_info = ""
                                if progress.pages_total > 0:
                                    page_info = f" ({progress.pages_total} pages)"
                                click.echo(
                                    f"\n{icon} {click.style('[3/4] Site Generation', bold=True, fg='cyan')} - Creating viewer{page_info}..."
                                )
                            elif progress.phase == "cleanup":
                                if skip_cleanup:
                                    click.echo(
                                        f"\n⊘ {click.style('[4/4] Cleanup', bold=True, fg='yellow')} - Skipped"
                                    )
                                else:
                                    # Show block count if available
                                    block_info = ""
                                    if progress.blocks_total > 0:
                                        block_info = f" ({progress.blocks_total} blocks)"
                                    click.echo(
                                        f"\n{icon} {click.style('[4/4] Cleanup', bold=True, fg='cyan')} - Improving quality{block_info}..."
                                    )
                            elif progress.phase == "complete":
                                break

                        # Show detailed sub-progress for current phase
                        current_status = {
                            "ocr": progress.ocr_progress,
                            "translation": progress.translation_progress,
                            "site": progress.site_progress,
                            "cleanup": progress.cleanup_progress,
                        }

                        # Print status updates (only when changed)
                        for key, status in current_status.items():
                            if status and status != last_status.get(key):
                                if "Complete:" in status or "✓" in status:
                                    click.echo(f"  {click.style('✓', fg='green')} {status}")
                                else:
                                    click.echo(f"  → {status}")
                                last_status[key] = status

                        # Check if workflow completed
                        if progress.phase == "complete":
                            break

                        # Poll every 500ms
                        await asyncio.sleep(0.5)

                    except Exception as e:
                        # Workflow might not be ready for queries yet
                        await asyncio.sleep(0.5)

            # Wait for final result
            result = await handle.result()
            return result

        result = asyncio.run(run_translation())

        click.echo("")
        if result.success:
            click.secho("✓ Translation successful!", fg="green", bold=True)
            click.echo(f"  OCR Blocks: {result.ocr_blocks}")
            click.echo(f"  Translated Blocks: {result.translated_blocks}")
            click.echo(f"  Output: {result.output_dir}")
            click.echo(f"  Viewer: {result.html_path}")

            # Display product URL status
            if result.product_url:
                click.echo("")
                click.secho(f"✓ Product URL configured:", fg="green")
                click.echo(f"  Name: {result.product_name}")
                click.echo(f"  URL: {result.product_url}")
            else:
                click.echo("")
                click.secho("○ No product URL configured", fg="yellow")
                manual_name = Path(result.output_dir).name
                click.echo("  You can add it later using:")
                click.echo(f'  toku add-url "{manual_name}" "<URL>"')
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

        # Clean up temporary file if downloaded
        if temp_file:
            try:
                import os

                os.unlink(temp_file.name)
                click.echo(f"✓ Cleaned up temporary file: {temp_file.name}")
            except:
                pass


@cli.command()
@click.argument("manual_name")
@click.argument("source_url", required=False)
def add_url(manual_name, source_url):
    """Add source URL to a manual's metadata.

    If no URL is provided, will attempt to search Tokullectibles automatically.

    Examples:
      toku add-url "CSM-Fang-Memory" "https://tokullectibles.com/products/csm-fang-memory"
      toku add-url "CSM-Fang-Memory"  # Auto-search
    """
    import json
    from src.activities import _generate_main_index
    from src.tokullectibles import search_tokullectibles

    manuals_dir = Path("manuals")
    json_path = manuals_dir / manual_name / "translations.json"

    if not json_path.exists():
        click.secho(f"✗ Error: {json_path} not found", fg="red")
        click.echo("\nAvailable manuals:")
        for folder in sorted(manuals_dir.iterdir()):
            if folder.is_dir() and (folder / "translations.json").exists():
                click.echo(f"  - {folder.name}")
        sys.exit(1)

    # If no URL provided, try to search Tokullectibles
    if not source_url:
        click.echo(f"Searching Tokullectibles for: {manual_name}")
        result = search_tokullectibles(manual_name)

        if result:
            click.secho(f"✓ Found: {result.name}", fg="green")
            click.echo(f"  URL: {result.url}")

            if not click.confirm("\nUse this URL?"):
                click.echo("Cancelled.")
                sys.exit(0)

            source_url = result.url
        else:
            click.secho("✗ Product not found on Tokullectibles", fg="red")
            click.echo("\nPlease provide URL manually:")
            click.echo(f'  toku add-url "{manual_name}" "https://..."')
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
    _generate_main_index(manuals_dir)
    click.secho("✓ Done!", fg="green")


@cli.command()
def reindex():
    """Regenerate the main index from all translated manuals."""
    from src.activities import _generate_main_index

    manuals_dir = Path("manuals")

    if not manuals_dir.exists():
        click.secho("✗ Error: manuals/ directory not found", fg="red")
        sys.exit(1)

    click.echo("Regenerating main index...")
    _generate_main_index(manuals_dir)
    click.secho("✓ Main index regenerated!", fg="green")
    click.echo(f"  Location: web/index.html")


@cli.command()
def list():
    """List all translated manuals."""
    import json

    manuals_dir = Path("manuals")

    if not manuals_dir.exists():
        click.secho("✗ Error: manuals/ directory not found", fg="red")
        sys.exit(1)

    manuals = []
    for folder in sorted(manuals_dir.iterdir()):
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
