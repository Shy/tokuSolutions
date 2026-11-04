"""Translation activities using Google Cloud Translation API."""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from google.cloud import translate_v3 as translate
from temporalio import activity

from src.quality.providers import google_credentials

load_dotenv()

# Config from environment
PROJECT_ID = os.getenv("ProjectID")
TRANSLATE_LOCATION = "us-central1"


@dataclass
class TranslatedBlock:
    """A text block with translation."""

    original: str
    translated: str
    page: int
    x: float
    y: float
    width: float
    height: float


@dataclass
class TranslationResult:
    """Result from translating OCR blocks."""

    blocks: list[TranslatedBlock]
    success: bool
    error: str | None = None
    notes_path: str = ""
    glossary_path: str = ""


class OCRBlock(TypedDict):
    """The existing, serializable OCR Activity input."""

    text: str
    page: int
    x: float
    y: float
    width: float
    height: float


class TranslatedPayload(TypedDict):
    """The existing translated block payload sent to site generation."""

    original: str
    translated: str
    page: int
    x: float
    y: float
    width: float
    height: float


@activity.defn
async def translate_blocks_activity(
    blocks: list[OCRBlock],
    source_lang: str = "ja",
    target_lang: str = "en",
    product: str | None = None,
    use_web_research: bool = True,
) -> TranslationResult:
    """
    Translate a list of text blocks.
    """
    activity.logger.info(f"Translating {len(blocks)} blocks")

    try:
        if product is not None and source_lang == "ja" and target_lang == "en":
            from src.quality.pipeline import ManualBlock, translate_manual
            from src.quality.providers import GoogleTranslator
            from src.quality.research import WikidataLookup
            from src.quality.web_research import GlossaryResearcher

            run_id = activity.info().workflow_run_id
            if not run_id:
                raise ValueError("translation requires a Workflow run")
            root = Path(__file__).resolve().parents[2]
            # Blocking public lookups and Google requests must not stall the
            # Worker's event loop. The Activity result is recorded in history.
            candidate = await asyncio.to_thread(
                translate_manual,
                root,
                [ManualBlock.model_validate(block) for block in blocks],
                product,
                run_id,
                GoogleTranslator(
                    root, "translation-llm", PROJECT_ID or "", TRANSLATE_LOCATION
                ),
                GlossaryResearcher(
                    WikidataLookup(root / "benchmark-runs" / "glossary-research-cache"),
                    root / "benchmark-runs" / "glossary-web-cache",
                )
                if use_web_research
                else WikidataLookup(
                    root / "benchmark-runs" / "glossary-research-cache"
                ),
                model="google-tllm",
            )
            translated = [
                TranslatedBlock(
                    original=block.text,
                    translated=block.translation,
                    page=block.page,
                    x=block.x,
                    y=block.y,
                    width=block.width,
                    height=block.height,
                )
                for block in candidate.blocks
            ]
            if candidate.flags:
                activity.logger.info(
                    f"Machine translation complete with {len(candidate.flags)} advisory notes: {candidate.review_path}"
                )
            return TranslationResult(
                blocks=translated,
                success=True,
                notes_path=candidate.review_path if candidate.flags else "",
                glossary_path=str(
                    Path(candidate.review_path).with_name("glossary.json")
                ),
            )

        credentials = google_credentials(Path(__file__).resolve().parents[2])
        client = translate.TranslationServiceClient(credentials=credentials)
        parent = f"projects/{PROJECT_ID}/locations/{TRANSLATE_LOCATION}"

        translated_blocks = []

        # Batch translate for efficiency (max 1024 segments per request)
        batch_size = 100
        for offset in range(0, len(blocks), batch_size):
            batch = blocks[offset : offset + batch_size]
            texts = [b["text"] for b in batch]

            response = client.translate_text(
                request={
                    "parent": parent,
                    "contents": texts,
                    "source_language_code": source_lang,
                    "target_language_code": target_lang,
                    "mime_type": "text/plain",
                }
            )

            for block, translation in zip(batch, response.translations, strict=True):
                translated_blocks.append(
                    TranslatedBlock(
                        original=block["text"],
                        translated=translation.translated_text,
                        page=block["page"],
                        x=block["x"],
                        y=block["y"],
                        width=block["width"],
                        height=block["height"],
                    )
                )

        activity.logger.info(f"Translation complete: {len(translated_blocks)} blocks")

        return TranslationResult(
            blocks=translated_blocks,
            success=True,
        )

    except Exception as error:  # noqa: BLE001 — preserve the Activity's legacy failure-result contract.
        activity.logger.error(f"Translation failed: {error}")
        return TranslationResult(
            blocks=[],
            success=False,
            error=str(error),
        )
