"""Keep unfiltered Document AI responses outside the published manual schema."""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from google.cloud import documentai_v1 as documentai
from pydantic import JsonValue, TypeAdapter

from src.quality.core import write_artifact
from src.quality.models import Artifact

if TYPE_CHECKING:
    from src.quality.cleanup_context import ContextPage
    from src.quality.page_grouping import OCRLine


class OCRSource(Artifact):
    """Raw OCR, processor identity and rendered-image provenance."""

    processor: str
    image_sha256: str
    page: int
    document: JsonValue


def save_ocr_source(
    document: documentai.Document,
    processor: str,
    image_bytes: bytes,
    page: int,
    directory: Path | None = None,
) -> Path:
    """Atomically retain every OCR region, confidence and layout before filtering."""
    image_digest = hashlib.sha256(image_bytes).hexdigest()
    artifact = OCRSource(
        processor=processor,
        image_sha256=image_digest,
        page=page,
        document=TypeAdapter(JsonValue).validate_json(
            documentai.Document.to_json(document)
        ),
    )
    directory = (
        directory
        or Path(__file__).resolve().parents[2] / "benchmark-runs" / "ocr-source"
    )
    response_digest = hashlib.sha256(artifact.model_dump_json().encode()).hexdigest()
    identifier = hashlib.sha256(
        f"{processor}:{image_digest}:{page}:{response_digest}".encode()
    ).hexdigest()
    path = directory / f"{identifier}.json"
    write_artifact(path, artifact)
    if directory == Path(__file__).resolve().parents[2] / "benchmark-runs/ocr-source":
        update_source_index(path, document)
    return path


def source_signature(
    blocks: list[tuple[str, tuple[float, float, float, float]]],
) -> str:
    """Match persisted rounded regions to their unfiltered OCR response."""
    import json

    return hashlib.sha256(
        json.dumps(
            [
                (text.strip(), [round(value, 4) for value in bounds])
                for text, bounds in blocks
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def source_lines(document: documentai.Document) -> list["OCRLine"]:
    """Read line text and geometry without flattening the rich OCR response."""
    from src.quality.page_grouping import OCRLine
    from src.quality.providers import normalized_box

    lines: list[OCRLine] = []
    for page in document.pages:
        for line in page.lines:
            points = [
                (float(vertex.x), float(vertex.y))
                for vertex in line.layout.bounding_poly.normalized_vertices
            ]
            if not points:
                continue
            text = "".join(
                document.text[int(segment.start_index) : int(segment.end_index)]
                for segment in line.layout.text_anchor.text_segments
            ).strip()
            if text:
                lines.append(
                    OCRLine(id=len(lines), text=text, bbox=normalized_box(points, 1, 1))
                )
    return lines


def cached_page_lines(
    page: "ContextPage", image: Path, output: Path, page_index: int
) -> list["OCRLine"]:
    """Reuse exact-match raw OCR or cache one fresh request for a legacy page."""
    import json
    import os

    from src.quality.page_grouping import OCRLine
    from src.quality.providers import google_credentials

    root = Path(__file__).resolve().parents[2]
    if output.exists():
        return TypeAdapter(list[OCRLine]).validate_json(output.read_bytes())
    shared = (
        root
        / "benchmark-runs/semantic-lines"
        / (hashlib.sha256(image.read_bytes()).hexdigest() + ".json")
    )
    if shared.exists():
        from src.quality.core import write_bytes

        write_bytes(output, shared.read_bytes())
        return TypeAdapter(list[OCRLine]).validate_json(output.read_bytes())
    index_path = root / "benchmark-runs/semantic-ocr-index.json"
    index = json.loads(index_path.read_bytes()) if index_path.exists() else {}
    signature = source_signature(
        [(block.text, block.bbox) for block in page.blocks if block.bbox]
    )
    matched = index.get(signature)
    if matched and Path(matched).is_file():
        raw = json.loads(Path(matched).read_bytes())
        document = documentai.Document.from_json(json.dumps(raw["document"]))
    else:
        location = os.getenv("Location", "us")
        processor = f"projects/{os.environ['ProjectID']}/locations/{location}/processors/{os.environ['ProcessorID']}"
        client = documentai.DocumentProcessorServiceClient(
            credentials=google_credentials(root),
            client_options={"api_endpoint": f"{location}-documentai.googleapis.com"},
        )
        document = client.process_document(
            request=documentai.ProcessRequest(
                name=processor,
                raw_document=documentai.RawDocument(
                    content=image.read_bytes(), mime_type="image/jpeg"
                ),
                process_options=documentai.ProcessOptions(
                    ocr_config=documentai.OcrConfig(
                        hints=documentai.OcrConfig.Hints(language_hints=["ja", "en"])
                    )
                ),
            ),
            timeout=120,
        ).document
        save_ocr_source(document, processor, image.read_bytes(), page_index)
    lines = source_lines(document)
    from src.quality.core import write_bytes

    write_bytes(output, TypeAdapter(list[OCRLine]).dump_json(lines))
    write_bytes(shared, output.read_bytes())
    return lines


def update_source_index(path: Path, document: documentai.Document) -> None:
    """Index source text and geometry while serializing local cache updates."""
    import fcntl
    import json

    from src.quality.core import write_bytes
    from src.quality.providers import parse_documentai

    blocks = parse_documentai(document)
    signature = source_signature([(block.text, block.bbox) for block in blocks])
    index_path = path.parent.parent / "semantic-ocr-index.json"
    with index_path.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = json.loads(index_path.read_bytes()) if index_path.exists() else {}
        index[signature] = str(path.resolve())
        write_bytes(index_path, json.dumps(index).encode())
