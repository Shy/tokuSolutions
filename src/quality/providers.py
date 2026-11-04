"""Dedicated OCR and translation adapters with lazy optional model runtimes."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import requests
from pydantic import BaseModel, Field

from src.quality.glossary import render_glossary_input
from src.quality.models import Block, RunConfig

if TYPE_CHECKING:
    from google.auth.credentials import Credentials
    from google.cloud import documentai_v1 as documentai


def normalized_box(
    points: list[tuple[float, float]], width: int, height: int
) -> tuple[float, float, float, float]:
    """Bound all polygon corners to the scan, then return normalized geometry."""
    if not points or min(width, height) <= 0:
        raise ValueError("box requires points and positive image dimensions")
    left = max(0.0, min(point[0] for point in points))
    top = max(0.0, min(point[1] for point in points))
    right = min(float(width), max(point[0] for point in points))
    bottom = min(float(height), max(point[1] for point in points))
    if right <= left or bottom <= top:
        raise ValueError("empty OCR detection box")
    return (left / width, top / height, (right - left) / width, (bottom - top) / height)


class YomiWord(BaseModel):
    """Subset of the upstream OCRSchema; retain recognition confidence."""

    content: str
    points: list[tuple[float, float]] = Field(min_length=4, max_length=4)
    rec_score: float = Field(ge=0, le=1)


class YomiResult(BaseModel):
    """YomiToku's documented OCR-only result format."""

    words: list[YomiWord]


def parse_yomitoku(raw_json: str, width: int, height: int) -> list[Block]:
    """Normalize all recognized text without deleting furigana or diagram labels."""
    result = YomiResult.model_validate_json(raw_json)
    return [
        Block(
            id=f"b{index}",
            text=word.content,
            bbox=normalized_box(word.points, width, height),
            confidence=word.rec_score,
        )
        for index, word in enumerate(result.words)
        if word.content.strip()
    ]


def google_credentials(root: Path) -> Credentials:
    """Load the existing service account without persisting or printing secrets."""
    import os

    from google.oauth2 import service_account

    path = os.environ.get("CREDENTIALS_PATH")
    if not path:
        raise ValueError("CREDENTIALS_PATH is not configured")
    credentials = service_account.Credentials.from_service_account_file(
        str(root / path)
    )  # type: ignore[no-untyped-call]
    return cast("Credentials", credentials)


class DocumentAIOCR:
    """Use the dedicated Google OCR processor with Japanese/English hints."""

    def __init__(
        self, root: Path, processor: str, location: str, *, timeout: float = 180
    ) -> None:
        """Store configuration and defer credentials until an uncached page runs."""
        self.root = root
        self.timeout = timeout
        self.processor = processor
        self.location = location

    def recognize(self, image: Path) -> list[Block]:
        """Keep all OCR blocks, including symbols, letters, and numbers."""
        from google.cloud import documentai_v1 as documentai

        client = documentai.DocumentProcessorServiceClient(
            credentials=google_credentials(self.root),
            client_options={
                "api_endpoint": f"{self.location}-documentai.googleapis.com"
            },
        )
        response = client.process_document(
            request=documentai.ProcessRequest(
                name=self.processor,
                raw_document=documentai.RawDocument(
                    content=image.read_bytes(), mime_type="image/png"
                ),
                process_options=documentai.ProcessOptions(
                    ocr_config=documentai.OcrConfig(
                        hints=documentai.OcrConfig.Hints(language_hints=["ja", "en"])
                    )
                ),
            ),
            timeout=self.timeout,
        )
        return parse_documentai(response.document)


def parse_documentai(document: documentai.Document) -> list[Block]:
    """Extract every nonempty region using all corners and either coordinate form."""
    blocks: list[Block] = []
    for page in document.pages:
        for region in page.blocks:
            layout = region.layout
            text = "".join(
                document.text[int(segment.start_index) : int(segment.end_index)]
                for segment in layout.text_anchor.text_segments
            )
            if not text.strip():
                continue
            points = [
                (float(vertex.x), float(vertex.y))
                for vertex in layout.bounding_poly.normalized_vertices
            ]
            if not points:
                if page.dimension.width <= 0 or page.dimension.height <= 0:
                    raise ValueError("pixel OCR geometry requires page dimensions")
                points = [
                    (
                        float(vertex.x) / page.dimension.width,
                        float(vertex.y) / page.dimension.height,
                    )
                    for vertex in layout.bounding_poly.vertices
                ]
            blocks.append(
                Block(
                    id=f"b{len(blocks)}",
                    text=text,
                    bbox=normalized_box(points, 1, 1),
                    confidence=float(layout.confidence),
                )
            )
    return blocks


class YomitokuOCR:
    """Run the full YomiToku recognition model on CPU or CUDA, loaded once."""

    def __init__(self, device: str) -> None:
        """Store device selection without downloading models during CLI help."""
        self.device = device
        self._engine: object | None = None

    def recognize(self, image: Path) -> list[Block]:
        """Read source pixels through the dedicated Japanese OCR engine."""
        try:
            import cv2  # type: ignore[import-not-found]
            from yomitoku import OCR  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "Install the optional YomiToku runtime; see benchmarks/README.md"
            ) from error
        if self._engine is None:
            self._engine = OCR(device=self.device)
        pixels = cv2.imread(str(image))
        if pixels is None:
            raise ValueError(f"cannot read scan: {image.name}")
        # Optional packages are intentionally not mandatory for the Mac harness.
        engine = self._engine
        if not callable(engine):
            raise TypeError("YomiToku OCR engine is not callable")
        result, _visualization = engine(pixels)
        height, width = pixels.shape[:2]
        return parse_yomitoku(str(result.model_dump_json()), int(width), int(height))


class GoogleTranslationItem(Protocol):
    """Minimal response item needed from the Google client."""

    translated_text: str


class GoogleTranslationResponse(Protocol):
    """Minimal response contract for ordered translation output."""

    @property
    def translations(self) -> list[GoogleTranslationItem]:
        """Return ordered translated segments."""
        ...


class GoogleTranslationClient(Protocol):
    """Narrow client interface for credential-free contract tests."""

    def translate_text(
        self, *, request: dict[str, object]
    ) -> GoogleTranslationResponse:
        """Translate one bounded request."""
        ...


def google_translation_client(root: Path) -> GoogleTranslationClient:
    """Create the existing Google v3 client for an uncached translation page."""
    from google.cloud import translate_v3 as translate

    return cast(
        GoogleTranslationClient,
        translate.TranslationServiceClient(credentials=google_credentials(root)),
    )


class GoogleTranslator:
    """Explicitly compare dedicated Translation LLM with the NMT baseline."""

    def __init__(self, root: Path, model: str, project: str, location: str) -> None:
        """Store the exact Google model family and regional resource prefix."""
        self.root = root
        self.model = model
        self.parent = f"projects/{project}/locations/{location}"

    def translate(self, texts: list[str]) -> list[str]:
        """Translate all segments in bounded batches without adding English drafts."""
        return self._translate(texts, mime_type="text/plain")

    def translate_with_glossary(
        self, texts: list[str], terms: dict[str, str]
    ) -> list[str]:
        """Protect accepted English names using Google's do-not-translate HTML."""
        contents = [
            render_glossary_input(text, terms, html_mode=True) for text in texts
        ]
        translated = self._translate(contents, mime_type="text/html")
        return [plain_translation_html(text) for text in translated]

    def _translate(self, texts: list[str], *, mime_type: str) -> list[str]:
        client = google_translation_client(self.root)
        translations: list[str] = []
        for offset in range(0, len(texts), 50):
            batch = texts[offset : offset + 50]
            response = client.translate_text(
                request={
                    "parent": self.parent,
                    "model": f"{self.parent}/models/general/{self.model}",
                    "contents": batch,
                    "source_language_code": "ja",
                    "target_language_code": "en",
                    "mime_type": mime_type,
                }
            )
            if len(response.translations) != len(batch):
                raise ValueError(
                    "Google translation result count differs from input count"
                )
            translations.extend(item.translated_text for item in response.translations)
        return translations


class _TranslationHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.after_span = False

    def handle_data(self, data: str) -> None:
        if self.after_span:
            data = re.sub(r"^\s+(?=[.,!?;:]|['’]s\b)", "", data)
        self.parts.append(data)
        self.after_span = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self.parts.append("\n")
        self.after_span = False

    def handle_endtag(self, tag: str) -> None:
        self.after_span = tag.lower() == "span"
        if tag.lower() in {"p", "div"}:
            self.parts.append("\n")


def plain_translation_html(text: str) -> str:
    """Remove transport markup while decoding literal escaped source characters."""
    parser = _TranslationHTML()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


class OllamaModel(BaseModel):
    """Local model identity used to prevent mixing updated weights in one run."""

    name: str
    digest: str


class OllamaModels(BaseModel):
    """Ollama's installed-model inventory."""

    models: list[OllamaModel]


def ollama_digest(endpoint: str, model: str) -> str:
    """Resolve an already-installed tag; never silently pull or substitute weights."""
    response = requests.get(endpoint.rstrip("/") + "/api/tags", timeout=15)
    response.raise_for_status()
    installed = OllamaModels.model_validate_json(response.text)
    for entry in installed.models:
        if entry.name == model:
            return entry.digest
    raise ValueError(f"Ollama model is not installed: {model}. Run ollama pull {model}")


class OllamaResponse(BaseModel):
    """Require a completed generation instead of accepting partial text."""

    response: str
    done: bool
    done_reason: str


class OllamaTranslator:
    """Run TranslateGemma locally using Ollama's documented translation prompt."""

    def __init__(self, config: RunConfig) -> None:
        """Store the endpoint and pinned local model identity."""
        self.config = config

    def translate(self, texts: list[str]) -> list[str]:
        """Translate independent Japanese segments without retaining chat history."""
        translations: list[str] = []
        for text in texts:
            # TranslateGemma's Ollama model card requires this language preamble
            # and two blank lines before the source text.
            prompt = (
                "You are a professional Japanese (ja) to English (en) translator. "
                "Your goal is to accurately convey the meaning and nuances of the original "
                "Japanese text while adhering to English grammar, vocabulary, and cultural sensitivities.\n"
                "Produce only the English translation, without any additional explanations or commentary. "
                "Please translate the following Japanese text into English:\n\n\n"
                + text
            )
            # UTF-8 byte count is a conservative token upper bound. Reserve
            # generation space and special tokens instead of letting Ollama
            # silently truncate a long source prompt to its context window.
            if len(prompt.encode("utf-8")) + 1024 + 64 > 8192:
                raise ValueError(
                    "source exceeds safe Ollama context; split the region first"
                )
            response = requests.post(
                self.config.endpoint.rstrip("/") + "/api/generate",
                json={
                    "model": self.config.model,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {
                        "temperature": 0,
                        "seed": 0,
                        "num_predict": 1024,
                        "num_ctx": 8192,
                    },
                },
                timeout=(10, 300),
            )
            response.raise_for_status()
            result = OllamaResponse.model_validate_json(response.text)
            if not result.done or result.done_reason != "stop":
                raise ValueError(
                    "Ollama generation was incomplete or truncated; page was not saved"
                )
            translations.append(result.response.strip())
        return translations

    def translate_with_glossary(
        self, texts: list[str], terms: dict[str, str]
    ) -> list[str]:
        """Insert accepted names into source input; output conformity is reviewed."""
        return self.translate(
            [render_glossary_input(text, terms, html_mode=False) for text in texts]
        )
