"""Translation activities using Google Cloud Translation API."""

import os
from dataclasses import dataclass

from temporalio import activity
from google.cloud import translate_v3 as translate
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

# Config from environment
PROJECT_ID = os.getenv("ProjectID")
TRANSLATE_LOCATION = "us-central1"
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH")


def get_credentials():
    """Load Google Cloud credentials."""
    if not CREDENTIALS_PATH:
        raise ValueError(
            "CREDENTIALS_PATH environment variable not set. "
            "Please add it to your .env file."
        )
    return service_account.Credentials.from_service_account_file(CREDENTIALS_PATH)


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


@activity.defn
async def translate_blocks_activity(
    blocks: list[dict], source_lang: str = "ja", target_lang: str = "en"
) -> TranslationResult:
    """
    Translate a list of text blocks.
    """
    activity.logger.info(f"Translating {len(blocks)} blocks")

    try:
        credentials = get_credentials()
        client = translate.TranslationServiceClient(credentials=credentials)
        parent = f"projects/{PROJECT_ID}/locations/{TRANSLATE_LOCATION}"

        translated_blocks = []

        # Batch translate for efficiency (max 1024 segments per request)
        batch_size = 100
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i : i + batch_size]
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

            for j, translation in enumerate(response.translations):
                block = batch[j]
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

    except Exception as e:
        activity.logger.error(f"Translation failed: {e}")
        return TranslationResult(
            blocks=[],
            success=False,
            error=str(e),
        )
