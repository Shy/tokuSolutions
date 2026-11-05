"""Cached file preparation and source-checked application for semantic panels."""

import asyncio
import base64
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Literal, cast

from PIL import Image
from pydantic import JsonValue, TypeAdapter
from temporalio import activity

from src.agent_models import AgentReply
from src.quality.cleanup_context import ContextManual, ContextPage, ManualData
from src.quality.core import write_bytes
from src.quality.glossary import load_glossary, resolve_glossary
from src.quality.page_grouping import (
    OCRLine,
    PagePlan,
    Panel,
    PanelText,
    PanelTranslations,
    validate_translations,
)
from src.quality.pipeline import infer_franchise
from src.quality.semantic_layout import (
    PLAN_INSTRUCTIONS,
    TRANSLATE_INSTRUCTIONS,
    assemble_page,
    panel_geometry,
    panel_preserves_illustrations,
    panel_references,
    safe_plan,
    source_candidates,
    split_illustrated_panel,
)
from src.semantic_models import GroupingRequest

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, value: object) -> None:
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def _picture(
    source: Path, bounds: tuple[float, float, float, float] | None = None
) -> list[dict[str, JsonValue]]:
    with Image.open(source) as image:
        view = image.convert("RGB")
        if bounds:
            left, top, width, height = bounds
            view = view.crop(
                (
                    round(left * image.width),
                    round(top * image.height),
                    round((left + width) * image.width),
                    round((top + height) * image.height),
                )
            )
        view.thumbnail((2000, 2400))
        buffer = io.BytesIO()
        view.save(buffer, "JPEG", quality=90)
    return [
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,"
            + base64.b64encode(buffer.getvalue()).decode(),
            "detail": "high",
        }
    ]


def _request(
    path: Path,
    stage: str,
    instructions: str,
    payload: object,
    image: Path,
    bounds: tuple[float, float, float, float] | None = None,
) -> str:

    request = GroupingRequest(
        stage=cast(Literal["plan", "translate"], stage),
        instructions=instructions,
        input_items=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    },
                    *_picture(image, bounds),
                ],
            }
        ],
        model=(os.getenv("LAYOUT_MODEL") or "gpt-5.5")
        if stage == "plan"
        else (os.getenv("OPENAI_MODEL") or "gpt-5-mini"),
    )
    if path.exists() and path.read_text() != request.model_dump_json():
        raise ValueError("Semantic model inputs changed; choose a new run")
    write_bytes(path, request.model_dump_json().encode())
    return str(path)


@activity.defn
async def cached_semantic_reply_activity(request_path: str) -> AgentReply | None:
    """Reuse paid replies only for identical frozen requests."""
    path = Path(request_path)
    cache = path.with_suffix(".reply.json")
    if not cache.exists():
        cache = (
            ROOT
            / "benchmark-runs/semantic-replies"
            / (hashlib.sha256(path.read_bytes()).hexdigest() + ".json")
        )
        if not cache.exists():
            return None
    saved = json.loads(cache.read_bytes())
    if saved["request_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError("Semantic request changed after response")
    if cache != path.with_suffix(".reply.json"):
        _write(path.with_suffix(".reply.json"), saved)
    return AgentReply.model_validate(saved["reply"])


@activity.defn
async def load_semantic_request_activity(request_path: str) -> GroupingRequest:
    """Load source images outside deterministic Workflow code."""

    return GroupingRequest.model_validate_json(Path(request_path).read_bytes())


@activity.defn
async def save_semantic_reply_activity(request_path: str, reply: AgentReply) -> None:
    """Save completed model usage/output before any semantic validation."""
    path = Path(request_path)
    _write(
        path.with_suffix(".reply.json"),
        {
            "request_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "reply": reply.model_dump(mode="json"),
        },
    )
    shared = (
        ROOT
        / "benchmark-runs/semantic-replies"
        / (hashlib.sha256(path.read_bytes()).hexdigest() + ".json")
    )
    write_bytes(shared, path.with_suffix(".reply.json").read_bytes())


def _prepare_page(
    json_path: str, page_index: int, directory: str, pdf_path: str, glossary_path: str
) -> str:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    source_path = output / "source-page.json"
    page = ContextManual.model_validate_json(Path(json_path).read_bytes()).pages[
        page_index
    ]
    if source_path.exists():
        if ContextPage.model_validate_json(source_path.read_bytes()) != page:
            raise ValueError("Source changed after semantic preparation")
    else:
        _write(source_path, page.model_dump(mode="json", exclude_none=True))
    image = output / "scan.jpg"
    if not image.exists():
        if pdf_path and Path(pdf_path).is_file():
            import pymupdf

            with pymupdf.open(pdf_path) as document:
                pdf_page = document[page_index]
                scale = min(
                    300 / 72, 7000 / max(pdf_page.rect.width, pdf_page.rect.height)
                )
                write_bytes(
                    image,
                    pdf_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes(
                        "jpeg", jpg_quality=95
                    ),
                )
        else:
            with Image.open(Path(json_path).parent / page.image) as scan:
                scan.convert("RGB").save(image, "JPEG", quality=95)
    product = Path(json_path).parent.name
    from src.quality.ocr_source import cached_page_lines

    cached_page_lines(page, image, output / "lines.json", page_index)
    glossary = load_glossary(
        Path(glossary_path) if glossary_path else ROOT / "glossary.json"
    )
    resolution = resolve_glossary(
        glossary,
        product=product,
        franchise=infer_franchise(
            product + "\n" + "\n".join(block.text for block in page.blocks)
        ),
    )
    _write(
        output / "glossary.json",
        {key: entry.english for key, entry in resolution.terms.items()},
    )
    return _request(
        output / "plan.request.json",
        "plan",
        PLAN_INSTRUCTIONS,
        {"page": page_index + 1, "source_regions": source_candidates(page)},
        image,
    )


@activity.defn
async def prepare_semantic_page_activity(
    json_path: str, page_index: int, directory: str, pdf_path: str, glossary_path: str
) -> str:
    """Freeze one page and prepare a source-ID-only planning request."""
    return await asyncio.to_thread(
        _prepare_page, json_path, page_index, directory, pdf_path, glossary_path
    )


def _plan_page(request_path: str, reply: AgentReply) -> list[str]:
    directory = Path(request_path).parent
    page = ContextPage.model_validate_json(
        (directory / "source-page.json").read_bytes()
    )
    plan = safe_plan(page, PagePlan.model_validate_json(reply.output_json))
    _write(directory / "plan.json", plan.model_dump(mode="json"))
    terms = json.loads((directory / "glossary.json").read_bytes())
    lines = TypeAdapter(list[OCRLine]).validate_json(
        (directory / "lines.json").read_bytes()
    )
    safe_panels = []
    for panel in plan.panels:
        if all(
            page.blocks[identity].text.strip() in {"✓", "✔", "✕", "✖", "×", "☓"}
            for identity in panel.source_ids
        ):
            continue
        if panel_preserves_illustrations(
            page, plan, panel, directory / "scan.jpg", lines
        ):
            safe_panels.append(panel)
        else:
            parts = split_illustrated_panel(
                page, plan, panel, directory / "scan.jpg", lines
            )
            safe_panels.extend(parts)
            included = {identity for part in parts for identity in part.source_ids}
            missing = set(panel.source_ids) - included
            if missing:
                plan.notes.append(
                    f"Retained {panel.id} source IDs {sorted(missing)} unchanged: illustration-mask check"
                )
    plan.panels = safe_panels
    references = panel_references(page, plan, lines)
    _write(directory / "plan.json", plan.model_dump(mode="json"))
    paths = []
    for panel in plan.panels:
        source_text = "\n".join(
            page.blocks[identity].text for identity in panel.source_ids
        )
        relevant_terms = {
            key: value
            for key, value in terms.items()
            if re.sub(r"\s+", "", key) in re.sub(r"\s+", "", source_text)
        }
        if panel.kind == "heading" and len(panel.source_ids) == 1:
            previous = page.blocks[panel.source_ids[0]].translation
            if (
                previous.strip()
                and not re.search(r"[ぁ-ゖァ-ヺ一-龯]", previous)
                and all(
                    term.casefold() in previous.casefold()
                    for term in relevant_terms.values()
                )
            ):
                reused = PanelTranslations(
                    panels=[
                        PanelText(
                            panel_id=panel.id,
                            japanese_items=[source_text],
                            english_items=[previous],
                        )
                    ],
                    uncertainties=[],
                )
                try:
                    validate_translations(
                        page,
                        PagePlan(panels=[panel], furigana=[], notes=[]),
                        reused,
                        references,
                    )
                except ValueError:
                    pass
                else:
                    _write(
                        directory / f"panel-{panel.id}.validated.json",
                        reused.model_dump(mode="json"),
                    )
                    continue
        paths.append(
            _request(
                directory / f"panel-{panel.id}.request.json",
                "translate",
                TRANSLATE_INSTRUCTIONS,
                {
                    "panel": panel.model_dump(mode="json"),
                    "source_text": source_text,
                    "fresh_panel_ocr": references[panel.id],
                    "accepted_glossary": relevant_terms,
                },
                directory / "scan.jpg",
                panel_geometry(page, plan, panel, lines),
            )
        )
    return paths


@activity.defn
async def plan_semantic_page_activity(
    request_path: str, reply: AgentReply
) -> list[str]:
    """Normalize a plan and prepare independent crop translations."""
    return await asyncio.to_thread(_plan_page, request_path, reply)


def _check_translation(request_path: str, reply: AgentReply, repaired: bool) -> str:
    directory = Path(request_path).parent
    page = ContextPage.model_validate_json(
        (directory / "source-page.json").read_bytes()
    )
    request = json.loads(Path(request_path).read_bytes())
    payload = json.loads(request["input_items"][0]["content"][0]["text"])
    panel = Panel.model_validate(payload["panel"])
    value_path = directory / f"panel-{panel.id}.validated.json"
    try:
        texts = PanelTranslations.model_validate_json(reply.output_json)
        validate_translations(
            page,
            PagePlan(panels=[panel], furigana=[], notes=[]),
            texts,
            {panel.id: payload.get("fresh_panel_ocr", "")},
        )
        # Accepted names must survive crop translation; uncertain names remain advisory.
        english = "\n".join(texts.panels[0].english_items).casefold()
        for term in payload.get("accepted_glossary", {}).values():
            if term.casefold() not in english:
                raise ValueError(f"Accepted term missing: {term}")
    except ValueError as error:
        _write(
            directory
            / f"panel-{panel.id}.rejected{'-repair' if repaired else ''}.json",
            {"error": str(error), "reply": reply.model_dump(mode="json")},
        )
        if repaired:
            return ""
        payload["validation_error"] = str(error)
        payload["rejected_translation"] = reply.output_json
        return _request(
            directory / f"panel-{panel.id}.repair.request.json",
            "translate",
            TRANSLATE_INSTRUCTIONS
            + " Fix the specified failure without omitting other instructions. Correct OCR from the crop where necessary.",
            payload,
            directory / "scan.jpg",
            panel_geometry(
                page,
                PagePlan.model_validate_json((directory / "plan.json").read_bytes()),
                panel,
                TypeAdapter(list[OCRLine]).validate_json(
                    (directory / "lines.json").read_bytes()
                ),
            ),
        )
    _write(value_path, texts.model_dump(mode="json"))
    return ""


@activity.defn
async def check_semantic_translation_activity(
    request_path: str, reply: AgentReply, repaired: bool
) -> str:
    """Validate a panel, allowing one bounded automatic repair before safe fallback."""
    return await asyncio.to_thread(_check_translation, request_path, reply, repaired)


def _finish_page(directory: str) -> str:
    output = Path(directory)
    page = ContextPage.model_validate_json((output / "source-page.json").read_bytes())
    plan = PagePlan.model_validate_json((output / "plan.json").read_bytes())
    panels = []
    translations = []
    uncertainties = []
    lines = TypeAdapter(list[OCRLine]).validate_json(
        (output / "lines.json").read_bytes()
    )
    references = panel_references(page, plan, lines)
    for panel in plan.panels:
        path = output / f"panel-{panel.id}.validated.json"
        if not path.exists():
            plan.notes.append(
                f"Panel {panel.id} retained original English after failed automatic checks"
            )
            continue
        value = PanelTranslations.model_validate_json(path.read_bytes())
        try:
            validate_translations(
                page,
                PagePlan(panels=[panel], furigana=plan.furigana, notes=[]),
                value,
                references,
            )
        except ValueError as error:
            plan.notes.append(
                f"Panel {panel.id} retained original English after final check: {error}"
            )
            continue
        panels.append(panel)
        translations.extend(value.panels)
        uncertainties.extend(value.uncertainties)
    plan.panels = panels
    grouped = assemble_page(
        page,
        plan,
        PanelTranslations(panels=translations, uncertainties=uncertainties),
        lines,
    )
    _write(output / "result.json", grouped)
    return str(output / "result.json")


@activity.defn
async def finish_semantic_page_activity(directory: str) -> str:
    """Assemble a checked page, retaining unsafe proposals as original overlays."""
    return await asyncio.to_thread(_finish_page, directory)


@activity.defn
async def apply_semantic_manual_activity(
    json_path: str, page_paths: list[str]
) -> list[str]:
    """Replace staged display regions only after every source ledger validates."""
    from src.quality.semantic_layout import validate_grouped_page

    path = Path(json_path)
    data = TypeAdapter(ManualData).validate_json(path.read_bytes())
    source = ContextManual.model_validate(data)
    if len(page_paths) != len(source.pages):
        raise ValueError("Missing grouped pages")
    warnings = []
    pages = []
    for page, result_path in zip(source.pages, page_paths, strict=True):
        value = json.loads(Path(result_path).read_bytes())
        grouped = ContextPage.model_validate(value)
        original_page = (
            page.model_copy(
                update={
                    "blocks": page.source_blocks,
                    "source_blocks": None,
                    "layout_provenance": None,
                }
            )
            if page.source_blocks is not None
            else page
        )
        validate_grouped_page(original_page, grouped)
        pages.append(value)
        notes = cast(dict[str, JsonValue], grouped.layout_provenance)
        plan = PagePlan.model_validate(notes["plan"])
        warnings.extend(plan.notes)
        warnings.extend(cast(list[str], notes["uncertainties"]))
    data["pages"] = pages
    data["meta"]["blocks"] = sum(len(page["blocks"]) for page in pages)
    data["meta"]["semantic_layout"] = 1
    _write(path, data)
    return warnings
