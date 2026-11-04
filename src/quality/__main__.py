"""Run ``python -m src.quality`` without Temporal or heavyweight model packages."""

import os
from pathlib import Path

import click
from dotenv import load_dotenv

from src.quality.core import (
    add_review_flag,
    load_suite,
    prepare_suite,
    run_ocr,
    run_translation,
)
from src.quality.models import RunConfig
from src.quality.providers import (
    DocumentAIOCR,
    GoogleTranslator,
    OllamaTranslator,
    YomitokuOCR,
    ollama_digest,
)
from src.quality.report import write_report

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUITE = ROOT / "benchmark-runs" / "pilot-v1"


@click.group()
def cli() -> None:
    """Prepare and compare dedicated OCR/translation models in isolated runs."""


@cli.command("prepare")
@click.option(
    "--manifest",
    type=click.Path(path_type=Path, exists=True),
    default=ROOT / "benchmarks/pilot.toml",
    show_default=True,
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=DEFAULT_SUITE,
    show_default=True,
)
def prepare(manifest: Path, output: Path) -> None:
    """Freeze original PDF scans and byte-for-byte baseline snapshots offline."""
    suite = prepare_suite(ROOT, manifest, output)
    click.echo(f"Frozen {len(suite.cases)} pages: {output / 'suite.json'}")


@cli.command("inspect")
@click.option(
    "--suite", type=click.Path(path_type=Path, exists=True), default=DEFAULT_SUITE
)
def inspect_suite(suite: Path) -> None:
    """Verify the frozen suite and list source-page coverage."""
    frozen = load_suite(suite)
    for case in frozen.cases:
        click.echo(
            f"{case.id}: {case.pdf} p{case.page} · {case.width}×{case.height} · {', '.join(case.features)}"
        )


@cli.command("ocr")
@click.option(
    "--suite", type=click.Path(path_type=Path, exists=True), default=DEFAULT_SUITE
)
@click.option(
    "--provider", type=click.Choice(["documentai", "yomitoku"]), required=True
)
@click.option("--run-id", required=True)
@click.option(
    "--case",
    "case_ids",
    multiple=True,
    help="Run only these cases; omit to run every page.",
)
@click.option("--device", type=click.Choice(["cpu", "cuda"]), default="cpu")
def ocr(
    suite: Path, provider: str, run_id: str, case_ids: tuple[str, ...], device: str
) -> None:
    """Run a dedicated OCR engine, resuming completed pages automatically."""
    from src.quality.core import OCRProvider

    load_dotenv(ROOT / ".env")
    engine: OCRProvider
    if provider == "documentai":
        project = required_env("ProjectID")
        processor_id = required_env("ProcessorID")
        location = os.getenv("Location", "us")
        processor = f"projects/{project}/locations/{location}/processors/{processor_id}"
        version = os.getenv("DOCAI_PROCESSOR_VERSION", "")
        if version:
            processor += f"/processorVersions/{version}"
        config = RunConfig(provider=provider, model=processor, revision=version)
        engine = DocumentAIOCR(ROOT, processor, location)
    else:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as package_version_for

        try:
            package_version = package_version_for("yomitoku")
        except PackageNotFoundError:
            raise click.ClickException(
                "Install YomiToku first; see benchmarks/README.md"
            ) from None
        config = RunConfig(
            provider=provider,
            model="yomitoku-full-ocr",
            device=device,
            revision=package_version,
        )
        engine = YomitokuOCR(device)
    results = run_ocr(suite, run_id, config, engine, case_ids)
    click.echo(
        f"OCR ready: {len(results)} pages / {sum(len(result.blocks) for result in results)} blocks · {run_id}"
    )


@cli.command("translate")
@click.option(
    "--suite", type=click.Path(path_type=Path, exists=True), default=DEFAULT_SUITE
)
@click.option(
    "--provider",
    type=click.Choice(["google-tllm", "google-nmt", "ollama"]),
    required=True,
)
@click.option(
    "--source-run",
    required=True,
    help="Frozen OCR run shared by translation candidates.",
)
@click.option("--run-id", required=True)
@click.option("--case", "case_ids", multiple=True)
@click.option(
    "--model",
    default="",
    help="Ollama model tag or compatible hf.co GGUF model; defaults to translategemma:12b.",
)
@click.option("--ollama-url", default="http://127.0.0.1:11434", show_default=True)
@click.option(
    "--glossary",
    "glossary_path",
    type=click.Path(path_type=Path, exists=True),
    default=ROOT / "glossary.json",
    show_default=True,
)
@click.option(
    "--no-glossary",
    is_flag=True,
    help="Reproduce earlier comparisons without terminology constraints.",
)
@click.option(
    "--research/--no-research",
    default=True,
    show_default=True,
    help="Automatically research new names before freezing a new run's glossary.",
)
@click.option(
    "--research-limit", default=20, type=click.IntRange(0, 200), show_default=True
)
def translate(
    suite: Path,
    provider: str,
    source_run: str,
    run_id: str,
    case_ids: tuple[str, ...],
    model: str,
    ollama_url: str,
    glossary_path: Path,
    no_glossary: bool,
    research: bool,
    research_limit: int,
) -> None:
    """Translate identical Japanese inputs with a dedicated translation model."""
    from src.quality.core import Translator
    from src.quality.research import WikidataLookup

    load_dotenv(ROOT / ".env")
    engine: Translator
    if provider.startswith("google-"):
        project = required_env("ProjectID")
        location = os.getenv("TRANSLATE_LOCATION", "us-central1")
        family = "translation-llm" if provider == "google-tllm" else "nmt"
        config = RunConfig(
            provider=provider,
            model=f"projects/{project}/locations/{location}/models/general/{family}",
        )
        engine = GoogleTranslator(ROOT, family, project, location)
    else:
        model = model or "translategemma:12b"
        config = RunConfig(
            provider=provider,
            model=model,
            revision=ollama_digest(ollama_url, model),
            device="ollama",
            precision="gguf",
            endpoint=ollama_url,
        )
        engine = OllamaTranslator(config)
    results = run_translation(
        suite,
        run_id,
        source_run,
        config,
        engine,
        case_ids,
        glossary_path=None if no_glossary else glossary_path,
        research_lookup=WikidataLookup(
            ROOT / "benchmark-runs" / "glossary-research-cache"
        )
        if research and not no_glossary
        else None,
        research_limit=research_limit,
    )
    click.echo(
        f"Translation ready: {len(results)} pages / {sum(len(result.flags) for result in results)} optional notes · {run_id}"
    )


@cli.command("report")
@click.option(
    "--suite", type=click.Path(path_type=Path, exists=True), default=DEFAULT_SUITE
)
@click.option("--run-id", required=True)
def report(suite: Path, run_id: str) -> None:
    """Build a machine-translation preview with optional notes for the final edit."""
    click.echo(str(write_report(suite, run_id)))


@cli.command("compare")
@click.option(
    "--suite", type=click.Path(path_type=Path, exists=True), default=DEFAULT_SUITE
)
@click.option(
    "--baseline-run", default="docai-tllm-glossary-auto-v2", show_default=True
)
@click.option("--comparison-id", required=True)
@click.option("--cleanup-model", default="gpt-5-mini", show_default=True)
@click.option("--judge-model", default="gpt-5-mini", show_default=True)
@click.option("--workers", default=3, type=click.IntRange(1, 4), show_default=True)
@click.option(
    "--render-only",
    is_flag=True,
    help="Rebuild from saved evidence without API requests.",
)
def compare(
    suite: Path,
    baseline_run: str,
    comparison_id: str,
    cleanup_model: str,
    judge_model: str,
    workers: int,
    render_only: bool,
) -> None:
    """Generate an automated, metered cleanup comparison without changing manuals."""
    from src.quality.comparison_run import render_comparison, run_comparison

    if render_only:
        click.echo(str(render_comparison(suite, comparison_id)))
        return
    load_dotenv(ROOT / ".env", override=True)
    required_env("OPENAI_API_KEY")
    output = run_comparison(
        suite,
        baseline_run,
        comparison_id,
        cleanup_model=cleanup_model,
        judge_model=judge_model,
        workers=workers,
        progress=click.echo,
    )
    click.echo(f"Comparison report: {output}")


@cli.command("flag")
@click.option(
    "--suite", type=click.Path(path_type=Path, exists=True), default=DEFAULT_SUITE
)
@click.option("--run-id", required=True)
@click.option("--case", "case_id", required=True)
@click.option("--block-id", required=True)
@click.option(
    "--kind",
    type=click.Choice(["ambiguity", "terminology", "omission", "meaning", "layout"]),
    default="ambiguity",
)
@click.option("--reason", required=True)
def flag(
    suite: Path, run_id: str, case_id: str, block_id: str, kind: str, reason: str
) -> None:
    """Record an optional editing note linked to the original source crop."""
    add_review_flag(suite, run_id, case_id, block_id, kind, reason)
    click.echo("Note recorded. Regenerate the optional report to include it.")


@cli.command("glossary")
@click.option(
    "--path",
    type=click.Path(path_type=Path, exists=True),
    default=ROOT / "glossary.json",
    show_default=True,
)
@click.option(
    "--franchise", help="Exact scope identifier, for example kamen-rider or sentai."
)
@click.option(
    "--product", help="Product scope identifier, normally the manual directory name."
)
@click.option(
    "--pending",
    is_flag=True,
    help="List uncertain entries, evidence, and review IDs offline.",
)
def glossary(
    path: Path, franchise: str | None, product: str | None, pending: bool
) -> None:
    """Validate the shared cache and inspect applicable accepted terminology."""
    from src.quality.glossary import load_glossary, resolve_glossary

    cache = load_glossary(path)
    accepted = sum(entry.status == "accepted" for entry in cache.entries)
    click.echo(
        f"Valid glossary: {accepted} accepted, {sum(entry.status == 'needs_review' for entry in cache.entries)} need review, "
        f"{sum(entry.status == 'ignored' for entry in cache.entries)} ignored."
    )
    if pending:
        for entry in cache.entries:
            if entry.status != "needs_review":
                continue
            if franchise is not None and entry.franchise not in (None, franchise):
                continue
            if product is not None and entry.product not in (None, product):
                continue
            click.echo(
                f"{entry.id} · {entry.japanese} · {entry.product or entry.franchise or 'shared'}"
            )
            click.echo(f"  {entry.notes or 'Owner review required.'}")
            for source in entry.sources:
                click.echo(f"  {source.url} · {source.evidence}")
    if franchise is not None or product is not None:
        resolved = resolve_glossary(cache, franchise=franchise, product=product)
        for japanese, entry in sorted(resolved.terms.items()):
            click.echo(f"{japanese} → {entry.english}")
        for japanese, identifiers in sorted(resolved.review.items()):
            click.echo(f"Needs review: {japanese} ({', '.join(identifiers)})")


def required_env(name: str) -> str:
    """Require a configured value without exposing credentials in error output."""
    value = os.getenv(name)
    if not value:
        raise click.ClickException(f"Missing environment setting: {name}")
    return value


def main() -> None:
    """Convert provider/file errors into a concise nonzero CLI result."""
    try:
        cli()
    except (ValueError, OSError, RuntimeError) as error:
        raise SystemExit(f"Benchmark failed: {error}") from error


if __name__ == "__main__":
    main()
