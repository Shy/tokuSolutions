"""Keep unfiltered Document AI responses outside the published manual schema."""

import hashlib
from pathlib import Path

from google.cloud import documentai_v1 as documentai
from pydantic import JsonValue, TypeAdapter

from src.quality.core import write_artifact
from src.quality.models import Artifact


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
    return path
