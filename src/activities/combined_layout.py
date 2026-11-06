"""Validate and fuse independently checkpointed layout sources without replacing OCR."""

import asyncio
import hashlib
from pathlib import Path

from pydantic import BaseModel
from temporalio import activity

from src.combined_models import CombinedLayoutInput
from src.layout_models import LayoutPage
from src.quality.core import write_json as _write


class _PageInput(BaseModel):
    page: int
    pdf: str
    sha256: str


class _Manifest(BaseModel):
    source_sha256: str
    pages: list[_PageInput]


def _build_index(request: CombinedLayoutInput) -> str:
    source_hash = hashlib.sha256(
        Path(request.semantic.pdf_path).read_bytes()
    ).hexdigest()
    pages: list[list[dict[str, str]]] = [[] for _ in range(request.semantic.page_count)]
    coverage: dict[str, set[int]] = {"docling": set(), "google": set()}
    for job in request.parsers:
        if len(job.providers) != 1 or job.providers[0] not in coverage:
            raise ValueError(
                "Combined layout requires explicit Docling/Google parser jobs"
            )
        provider = job.providers[0]
        manifest = _Manifest.model_validate_json(Path(job.manifest_path).read_bytes())
        if manifest.source_sha256 != source_hash:
            raise ValueError("Parser input does not match the semantic source PDF")
        seen: set[int] = set()
        for record in manifest.pages:
            if record.page in seen or not 1 <= record.page <= len(pages):
                raise ValueError("Invalid or duplicate parser source page")
            seen.add(record.page)
            if (
                hashlib.sha256(Path(record.pdf).read_bytes()).hexdigest()
                != record.sha256
            ):
                raise ValueError("Frozen parser page changed during extraction")
            folder = "docling" if provider == "docling" else "google-layout"
            path = (
                Path(job.output_dir) / folder / "pages" / f"page-{record.page:04d}.json"
            )
            if not path.is_file():
                raise ValueError(f"Missing {provider} output on page {record.page}")
            payload = path.read_bytes()
            layout = LayoutPage.model_validate_json(payload)
            prefix = "docling" if provider == "docling" else "google-layout"
            if layout.page != record.page or not layout.provider.startswith(prefix):
                raise ValueError("Parser output identity mismatch")
            pages[record.page - 1].append(
                {
                    "path": str(path.absolute()),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            coverage[provider].add(record.page)
    expected = set(range(1, len(pages) + 1))
    if any(found != expected for found in coverage.values()):
        raise ValueError(
            "Combined layout requires complete Docling and Google coverage"
        )
    target = Path(request.semantic.run_dir) / "combined-layout-index.json"
    _write(target, {"source_sha256": source_hash, "pages": pages})
    return str(target)


@activity.defn
async def build_combined_index_activity(request: CombinedLayoutInput) -> str:
    """Require every declared result and bind the fusion index to immutable inputs."""
    return await asyncio.to_thread(_build_index, request)
