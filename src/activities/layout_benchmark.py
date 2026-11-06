"""Independent, cached extraction Activities for an isolated layout comparison."""

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import cast

import pymupdf
from pydantic import JsonValue, TypeAdapter
from temporalio import activity

from src.layout_models import LayoutBenchmarkInput
from src.quality.core import write_json as _write
from src.quality.layout_engines import (
    normalize_docling,
    normalize_google,
    normalize_pymupdf,
)

JSON = TypeAdapter(dict[str, JsonValue])
ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> dict[str, JsonValue]:
    return JSON.validate_json(path.read_bytes())


def _freeze_configuration(
    output: Path,
    manifest: bytes,
    docling_python: str,
    google_processor: str = "",
    google_version: str = "",
) -> None:
    configuration = {
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "docling_python": docling_python,
    }
    if google_processor:
        configuration.update(
            google_processor=google_processor, google_version=google_version
        )
    target = output / "configuration.json"
    if target.exists() and _read(target) != configuration:
        raise ValueError(
            "Benchmark configuration changed; choose a new output directory"
        )
    _write(target, configuration)


def _records(request: LayoutBenchmarkInput) -> list[dict[str, JsonValue]]:
    return TypeAdapter(list[dict[str, JsonValue]]).validate_python(
        _read(Path(request.manifest_path))["pages"]
    )


@activity.defn
async def inspect_layout_input_activity(request: LayoutBenchmarkInput) -> str:
    """Validate frozen inputs before any provider work or API charge."""

    def inspect() -> str:
        _freeze_configuration(
            Path(request.output_dir),
            Path(request.manifest_path).read_bytes(),
            request.docling_python,
            request.google_processor,
            request.google_version if request.google_processor else "",
        )
        manifest = _read(Path(request.manifest_path))
        source = Path(cast(str, manifest["source"]))
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["source_sha256"]:
            raise ValueError("Source PDF changed after freezing")
        for record in _records(request):
            if (
                hashlib.sha256(Path(cast(str, record["pdf"])).read_bytes()).hexdigest()
                != record["sha256"]
            ):
                raise ValueError("Frozen page PDF changed")
        target = Path(request.output_dir) / "input-inspection.json"
        _write(
            target,
            {
                "pages": len(_records(request)),
                "image_only_pages": sum(
                    record["native_characters"] == 0 and record["vector_paths"] == 0
                    for record in _records(request)
                ),
                "source_sha256": manifest["source_sha256"],
            },
        )
        return str(target)

    return await asyncio.to_thread(inspect)


@activity.defn
async def docling_layout_activity(request: LayoutBenchmarkInput) -> str:
    """Run optional local models once, checkpointing each independently converted page."""
    output = Path(request.output_dir) / "docling"
    output.mkdir(parents=True, exist_ok=True)
    run = Path(
        str(
            _read(Path(request.manifest_path)).get(
                "docling_assets", Path(request.manifest_path).parent
            )
        )
    )
    config = output / "runner-config.json"
    _write(
        config,
        {
            "manifest_path": request.manifest_path,
            "output_dir": str(output),
            "models": str(run / "models"),
            "tessdata": str(run / "tessdata"),
        },
    )
    environment = {
        **os.environ,
        "HF_HOME": str(run / "hf-cache"),
        "HF_HUB_OFFLINE": "1",
        "TESSDATA_PREFIX": str(run / "tessdata"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    process = await asyncio.create_subprocess_exec(
        request.docling_python,
        str(ROOT / "scripts/docling-layout.py"),
        str(config),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=environment,
    )
    assert process.stdout is not None
    try:
        with (output / "worker.log").open("ab") as log:
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=15)
                except TimeoutError:
                    activity.heartbeat("Docling conversion in progress")
                    continue
                if not line:
                    break
                log.write(line)
                log.flush()
                activity.heartbeat(line.decode(errors="replace")[-500:])
        code = await process.wait()
        if code:
            raise RuntimeError(
                f"Docling worker failed ({code}); see {output / 'worker.log'}"
            )
    except BaseException:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        raise
    for record in _records(request):
        page = int(cast(int, record["page"]))
        raw = _read(output / "raw" / f"page-{page:04d}.json")
        normalized = normalize_docling(JSON.validate_python(raw["document"]), page)
        segmentation = _read(Path(request.manifest_path)).get("docling_psm")
        if segmentation is not None:
            normalized.provider = f"docling-psm-{segmentation}"
        normalized.elapsed_seconds = cast(float, raw["elapsed_seconds"])
        _write(
            output / "pages" / f"page-{page:04d}.json",
            normalized.model_dump(mode="json"),
        )
    target = output / "summary.json"
    _write(
        target,
        {"provider": "docling", "status": "complete", "pages": len(_records(request))},
    )
    return str(target)


def _pymupdf_page(path: Path, *, ocr: bool, tessdata: Path) -> dict[str, JsonValue]:
    with pymupdf.open(path) as original:
        if ocr:
            pixels = original[0].get_pixmap(dpi=216, alpha=False)
            payload = pixels.pdfocr_tobytes(language="jpn+eng", tessdata=str(tessdata))
            document = pymupdf.open(stream=payload, filetype="pdf")
        else:
            document = pymupdf.open(path)
    try:
        page = document[0]
        blocks = [
            {"bbox": list(block[:4]), "text": str(block[4])}
            for block in page.get_text("blocks", sort=True)
            if block[6] == 0
        ]
        tables = []
        for table in page.find_tables(
            strategy="text" if ocr else "lines", use_layout=ocr
        ).tables:
            content = table.extract()
            detected = [bounds for row in table.rows for bounds in row.cells if bounds]
            x_edges = sorted(
                {
                    round(bounds[position], 3)
                    for bounds in detected
                    for position in (0, 2)
                }
            )
            y_edges = sorted(
                {
                    round(bounds[position], 3)
                    for bounds in detected
                    for position in (1, 3)
                }
            )
            cells = []
            for row_index, row in enumerate(content):
                for column_index, text in enumerate(row):
                    bounds = table.rows[row_index].cells[column_index]
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "text": text or "",
                            "row_span": 1
                            + sum(
                                bounds[1] + 0.001 < edge < bounds[3] - 0.001
                                for edge in y_edges
                            )
                            if bounds
                            else 1,
                            "column_span": 1
                            + sum(
                                bounds[0] + 0.001 < edge < bounds[2] - 0.001
                                for edge in x_edges
                            )
                            if bounds
                            else 1,
                            "bbox": [
                                bounds[0] / page.rect.width,
                                bounds[1] / page.rect.height,
                                (bounds[2] - bounds[0]) / page.rect.width,
                                (bounds[3] - bounds[1]) / page.rect.height,
                            ]
                            if bounds
                            else None,
                        }
                    )
            tables.append(
                {
                    "bbox": list(table.bbox),
                    "rows": table.row_count,
                    "columns": table.col_count,
                    "cells": cells,
                }
            )
        return JSON.validate_python(
            {
                "width": page.rect.width,
                "height": page.rect.height,
                "blocks": blocks,
                "tables": tables,
                "image_count": len(page.get_images()),
                "vector_count": len(page.get_drawings()),
            }
        )
    finally:
        document.close()


@activity.defn
async def pymupdf_layout_activity(request: LayoutBenchmarkInput) -> str:
    """Compare native extraction and an explicitly labeled Japanese OCR-assisted arm."""
    run = Path(request.manifest_path).parent
    for record in _records(request):
        page = int(cast(int, record["page"]))
        for ocr in (False, True):
            provider = "pymupdf-ocr" if ocr else "pymupdf-native"
            output = Path(request.output_dir) / provider
            raw_path = output / "raw" / f"page-{page:04d}.json"
            started = time.monotonic()
            if raw_path.exists():
                raw = _read(raw_path)
            else:
                raw = await asyncio.to_thread(
                    _pymupdf_page,
                    Path(cast(str, record["pdf"])),
                    ocr=ocr,
                    tessdata=run / "tessdata",
                )
                _write(raw_path, raw)
            normalized = normalize_pymupdf(raw, page, ocr=ocr)
            normalized.elapsed_seconds = time.monotonic() - started
            _write(
                output / "pages" / f"page-{page:04d}.json",
                normalized.model_dump(mode="json"),
            )
            activity.heartbeat(f"{provider}: page {page}")
    target = Path(request.output_dir) / "pymupdf-summary.json"
    _write(
        target,
        {
            "provider": "pymupdf",
            "status": "complete",
            "pages": len(_records(request)),
            "arms": ["native", "ocr-assisted"],
        },
    )
    return str(target)


@activity.defn
async def google_layout_activity(request: LayoutBenchmarkInput) -> str:
    """Cache Layout Parser responses separately from existing recognition and translation."""
    target = Path(request.output_dir) / "google-layout" / "summary.json"
    if request.google_processor:
        pending = asyncio.create_task(asyncio.to_thread(_google_layout, request))
        while not pending.done():
            await asyncio.wait({pending}, timeout=15)
            activity.heartbeat("Google Layout conversion/checkpoint in progress")
        return await pending
    _write(
        target,
        {
            "provider": "google-layout",
            "status": "deferred",
            "pages": 0,
            "reason": "Owner deferred Google until a Layout Parser resource is created in GCP.",
        },
    )
    return str(target)


def _google_layout(request: LayoutBenchmarkInput) -> str:
    from google.cloud import documentai_v1beta3 as documentai

    from src.quality.providers import google_credentials

    match = re.fullmatch(
        r"projects/[^/]+/locations/(us|eu)/processors/[^/]+", request.google_processor
    )
    if not match or not re.fullmatch(r"[A-Za-z0-9._-]+", request.google_version):
        raise ValueError(
            "A full Google processor resource and explicit version are required"
        )
    output = Path(request.output_dir) / "google-layout"
    client = documentai.DocumentProcessorServiceClient(
        credentials=google_credentials(ROOT),
        client_options={"api_endpoint": match.group(1) + "-documentai.googleapis.com"},
    )
    processor = (
        request.google_processor + "/processorVersions/" + request.google_version
    )
    for record in _records(request):
        page = int(cast(int, record["page"]))
        raw_path = output / "raw" / f"page-{page:04d}.json"
        if not raw_path.exists():
            started = time.monotonic()
            result = client.process_document(
                request=documentai.ProcessRequest(
                    name=processor,
                    raw_document=documentai.RawDocument(
                        content=Path(cast(str, record["pdf"])).read_bytes(),
                        mime_type="application/pdf",
                    ),
                    process_options=documentai.ProcessOptions(
                        layout_config=documentai.ProcessOptions.LayoutConfig(
                            return_bounding_boxes=True
                        )
                    ),
                ),
                retry=None,
                timeout=120,
            )
            _write(
                raw_path,
                {
                    "processor": processor,
                    "source_sha256": record["sha256"],
                    "elapsed_seconds": time.monotonic() - started,
                    "document": json.loads(
                        documentai.Document.to_json(result.document)
                    ),
                },
            )
        raw = _read(raw_path)
        if (
            raw.get("processor") != processor
            or raw.get("source_sha256") != record["sha256"]
        ):
            raise ValueError(
                "Cached Google response does not match processor/version/source"
            )
        normalized = normalize_google(JSON.validate_python(raw["document"]), page)
        normalized.provider = "google-layout-" + request.google_version
        normalized.elapsed_seconds = cast(float, raw["elapsed_seconds"])
        _write(
            output / "pages" / f"page-{page:04d}.json",
            normalized.model_dump(mode="json"),
        )
    target = output / "summary.json"
    _write(
        target,
        {
            "provider": "google-layout",
            "status": "complete",
            "processor": processor,
            "pages": len(_records(request)),
            "return_bounding_boxes": True,
        },
    )
    return str(target)
