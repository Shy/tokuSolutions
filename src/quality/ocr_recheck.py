"""Bounded independent crop readings; original Japanese is never overwritten."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PIL import Image

from src.quality.core import digest, write_artifact, write_bytes
from src.quality.models import Artifact
from src.quality.providers import DocumentAIOCR, YomitokuOCR

if TYPE_CHECKING:
    from src.quality.cleanup_context import EditBatch


class OCRObservation(Artifact):
    """Two independent readings of an enlarged crop with exact image provenance."""

    index: str
    original: str
    google: str = ""
    yomitoku: str = ""
    status: Literal["agreed", "conflict", "unavailable"] = "unavailable"
    image_sha256: str = ""
    error: str = ""
    yomitoku_version: str = ""


def compare_readings(
    first: str, second: str
) -> Literal["agreed", "conflict", "unavailable"]:
    """Ignore whitespace/width variations while retaining meaningful sign changes."""

    def normalize(text: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).replace("−", "-")

    if not first.strip() or not second.strip():
        return "unavailable"
    return "agreed" if normalize(first) == normalize(second) else "conflict"


def google_ocr(root: Path) -> DocumentAIOCR:
    """Reuse the configured Japanese Document AI processor for uncached evidence."""
    project = os.environ.get("ProjectID", "")
    processor = os.environ.get("ProcessorID", "")
    location = os.environ.get("Location", "us")
    if not project or not processor:
        raise ValueError("Document AI is not configured")
    name = f"projects/{project}/locations/{location}/processors/{processor}"
    return DocumentAIOCR(root, name, location, timeout=30)


def recheck_crops(
    batch: EditBatch, manual_dir: Path, cache: Path, root: Path
) -> list[OCRObservation]:
    """Re-read at most four risky source crops with Google and local YomiToku."""
    selected = [
        block
        for block in batch.blocks
        if block.bbox
        and not block.possible_ruby
        and (
            (len(block.original) < 120 and re.search(r"電池|極性|端子", block.original))
            or re.fullmatch(r"[+＋−\-\d()①-⑳\s]{1,8}", block.original)
        )
    ]
    selected.sort(
        key=lambda block: (
            not bool(re.search(r"上側|下側|極性|[＋+−]", block.original)),
            not bool(re.search(r"電池|端子", block.original)),
        )
    )
    selected = selected[:4]
    if not selected or not batch.image:
        return []
    image_path = (manual_dir / batch.image).resolve()
    if not image_path.is_relative_to(manual_dir.resolve()) or not image_path.is_file():
        return []
    python = Path(os.getenv("YOMITOKU_PYTHON", str(root / ".venv-quality/bin/python")))
    observations = []
    with Image.open(image_path) as image:
        for block in selected:
            assert block.bbox is not None
            left, top, width, height = block.bbox
            margin = min(height * 0.2, 0.01)
            crop = image.crop(
                (
                    max(0, int((left - margin) * image.width)),
                    max(0, int((top - margin) * image.height)),
                    min(image.width, int((left + width + margin) * image.width)),
                    min(image.height, int((top + height + margin) * image.height)),
                )
            ).convert("RGB")
            crop.thumbnail((1800, 1800))
            scale = min(3, 1800 / max(crop.size))
            if scale > 1:
                crop = crop.resize((int(crop.width * scale), int(crop.height * scale)))
            # The detector expects document-scale text, not a crop filled by
            # oversized glyphs. White padding adds no source information.
            crop.thumbnail((640, 640))
            canvas = Image.new("RGB", (1600, 1600), "white")
            canvas.paste(crop, (80, 80))
            crop = canvas
            import io

            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            content = buffer.getvalue()
            fingerprint = digest(content)
            path = cache / (fingerprint + ".json")
            if path.exists():
                saved = OCRObservation.model_validate_json(path.read_bytes())
                if saved.status != "unavailable":
                    observations.append(
                        saved.model_copy(
                            update={"index": block.index, "original": block.original}
                        )
                    )
                    continue
            observation = OCRObservation(
                index=block.index, original=block.original, image_sha256=fingerprint
            )
            if not python.is_file():
                observation.error = (
                    "Local YomiToku runtime unavailable; original OCR retained."
                )
            else:
                crop_path = cache / (fingerprint + ".png")
                write_bytes(crop_path, content)
                try:
                    process = subprocess.run(
                        [str(python), "-m", "src.quality.ocr_recheck", str(crop_path)],
                        cwd=root,
                        env=dict(os.environ),
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=True,
                    )
                    payload = json.loads(process.stdout.strip().splitlines()[-1])
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("text"), str
                    ):
                        raise TypeError("Invalid local OCR response")
                    observation.yomitoku = payload["text"]
                    observation.yomitoku_version = str(payload.get("version", ""))
                    observation.google = "".join(
                        block.text for block in google_ocr(root).recognize(crop_path)
                    )
                    observation.status = compare_readings(
                        observation.google, observation.yomitoku
                    )
                except Exception as error:  # noqa: BLE001 — optional rechecks retain original OCR on provider/runtime failure.
                    observation.error = f"OCR recheck unavailable ({type(error).__name__}); original OCR retained."
            write_artifact(path, observation)
            observations.append(observation)
    return observations


def main() -> None:
    """Run the optional local model in its own environment without loading it in Workers."""
    blocks = YomitokuOCR(os.getenv("YOMITOKU_DEVICE", "cpu")).recognize(
        Path(sys.argv[1])
    )
    from importlib.metadata import version

    print(
        json.dumps(
            {
                "text": "".join(
                    block.text
                    for block in sorted(
                        blocks, key=lambda block: (block.bbox[1], block.bbox[0])
                    )
                ),
                "version": version("yomitoku"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
