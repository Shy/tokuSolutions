"""Run the optional Docling dependency in an isolated, cached worker process."""

import importlib.metadata
import json
import sys
import time
from pathlib import Path


def main() -> None:
    """Convert frozen single-page inputs, preserving each complete raw result."""
    from docling.datamodel.accelerator_options import (  # type: ignore[import-not-found]
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.base_models import InputFormat  # type: ignore[import-not-found]
    from docling.datamodel.pipeline_options import (  # type: ignore[import-not-found]
        OcrMode,
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore[import-not-found]

    config = json.loads(Path(sys.argv[1]).read_text())
    manifest = json.loads(Path(config["manifest_path"]).read_text())
    output = Path(config["output_dir"])
    raw = output / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    options = PdfPipelineOptions(
        artifacts_path=Path(config["models"]),
        do_ocr=True,
        do_table_structure=True,
        ocr_options=TesseractCliOcrOptions(
            lang=["jpn", "eng"],
            mode=OcrMode.FULL_PAGE,
            path=config["tessdata"],
            psm=manifest.get("docling_psm"),
        ),
        accelerator_options=AcceleratorOptions(
            device=AcceleratorDevice.CPU, num_threads=4
        ),
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    for record in manifest["pages"]:
        path = raw / f"page-{record['page']:04d}.json"
        if path.exists():
            continue
        started = time.monotonic()
        result = converter.convert(Path(record["pdf"]))
        data = {
            "document": result.document.export_to_dict(),
            "elapsed_seconds": time.monotonic() - started,
            "status": str(result.status),
            "docling_version": importlib.metadata.version("docling"),
            "ocr_options": options.ocr_options.model_dump(mode="json"),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.replace(path)
        print(
            f"COMPLETE page {record['page']}: {data['elapsed_seconds']:.2f}s {data['status']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
