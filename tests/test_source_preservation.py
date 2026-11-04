"""Protect manual labels and original OCR from destructive cleanup."""

from copy import deepcopy
from pathlib import Path

from src.cleanup import stage1_ftfy_cleanup, stage2_rule_based_cleanup
from src.quality.cleanup_context import ManualData


def test_short_instruction_labels_survive_ocr() -> None:
    """Diagram labels, switch positions and battery types are useful source text."""
    from google.cloud import documentai_v1 as documentai

    from src.quality.providers import parse_documentai

    labels = ["A", "3", "①", "ON", "OFF", "LR44", "+", "−", "→", "  "]
    document = documentai.Document(text="".join(labels))
    page = documentai.Document.Page()
    offset = 0
    for label in labels:
        page.blocks.append(
            documentai.Document.Page.Block(
                layout=documentai.Document.Page.Layout(
                    text_anchor=documentai.Document.TextAnchor(
                        text_segments=[
                            documentai.Document.TextAnchor.TextSegment(
                                start_index=offset, end_index=offset + len(label)
                            )
                        ]
                    ),
                    bounding_poly=documentai.BoundingPoly(
                        normalized_vertices=[
                            documentai.NormalizedVertex(x=0, y=0),
                            documentai.NormalizedVertex(x=0.1, y=0),
                            documentai.NormalizedVertex(x=0.1, y=0.1),
                            documentai.NormalizedVertex(x=0, y=0.1),
                        ]
                    ),
                )
            )
        )
        offset += len(label)
    document.pages.append(page)
    assert [block.text for block in parse_documentai(document)] == labels[:-1]


def test_cleanup_preserves_source_labels_and_possible_furigana() -> None:
    """Adjacency alone cannot prove a reading is redundant or a digit is a page."""
    texts = ["A", "3", "(1)", "ON", "LR44", "■", "でん ち", "電池", "十一", "土日"]
    manual: ManualData = {
        "meta": {},
        "pages": [
            {
                "blocks": [
                    {"text": text, "translation": text, "bbox": [0, 0, 0.1, 0.1]}
                    for text in texts
                ]
            }
        ],
    }
    before = deepcopy(manual)
    stage1_ftfy_cleanup(manual)
    stage2_rule_based_cleanup(manual)
    assert manual == before


def test_real_ocr_response_preserves_labels_and_language_hints(tmp_path: Path) -> None:
    """Exercise production extraction with a real PDF and a fake Google response."""
    from types import SimpleNamespace
    from unittest.mock import patch

    import pymupdf as fitz
    from google.cloud import documentai_v1 as documentai

    from src.activities.ocr import _ocr_page_sync
    from src.quality.ocr_source import OCRSource, save_ocr_source

    pdf = tmp_path / "scan.pdf"
    with fitz.open() as document:
        document.new_page(width=100, height=100)
        document.save(pdf)
    raw = documentai.Document(
        text="ON",
        pages=[
            documentai.Document.Page(
                blocks=[
                    documentai.Document.Page.Block(
                        layout=documentai.Document.Page.Layout(
                            text_anchor=documentai.Document.TextAnchor(
                                text_segments=[
                                    documentai.Document.TextAnchor.TextSegment(
                                        start_index=0, end_index=2
                                    )
                                ]
                            ),
                            confidence=0.7,
                            bounding_poly=documentai.BoundingPoly(
                                normalized_vertices=[
                                    documentai.NormalizedVertex(x=0.1, y=0.1),
                                    documentai.NormalizedVertex(x=0.3, y=0.1),
                                    documentai.NormalizedVertex(x=0.3, y=0.2),
                                    documentai.NormalizedVertex(x=0.1, y=0.2),
                                ]
                            ),
                        )
                    )
                ]
            )
        ],
    )

    def save(
        document: documentai.Document, processor: str, image: bytes, page: int
    ) -> Path:
        return save_ocr_source(document, processor, image, page, tmp_path / "raw")

    with (
        patch("src.activities.ocr.google_credentials"),
        patch("src.activities.ocr.activity.logger"),
        patch("src.quality.ocr_source.save_ocr_source", side_effect=save),
        patch("src.activities.ocr.documentai.DocumentProcessorServiceClient") as client,
    ):
        client.return_value.process_document.return_value = SimpleNamespace(
            document=raw
        )
        result = _ocr_page_sync(str(pdf), 0)
    assert result.success
    assert result.blocks[0].text == "ON"
    request = client.return_value.process_document.call_args.kwargs["request"]
    assert list(request.process_options.ocr_config.hints.language_hints) == ["ja", "en"]
    artifact = OCRSource.model_validate_json(
        next((tmp_path / "raw").glob("*.json")).read_bytes()
    )
    assert isinstance(artifact.document, dict)
    assert artifact.document["text"] == "ON"


def test_google_extraction_keeps_rotated_and_pixel_coordinate_regions() -> None:
    """A diagram label must not disappear because its polygon starts elsewhere."""
    from google.cloud import documentai_v1 as documentai

    from src.quality.providers import parse_documentai

    document = documentai.Document(
        text="ONOFF",
        pages=[
            documentai.Document.Page(
                dimension=documentai.Document.Page.Dimension(width=100, height=200),
                blocks=[
                    documentai.Document.Page.Block(
                        layout=documentai.Document.Page.Layout(
                            text_anchor=documentai.Document.TextAnchor(
                                text_segments=[
                                    documentai.Document.TextAnchor.TextSegment(
                                        start_index=0, end_index=2
                                    )
                                ]
                            ),
                            bounding_poly=documentai.BoundingPoly(
                                normalized_vertices=[
                                    documentai.NormalizedVertex(x=0.3, y=0.2),
                                    documentai.NormalizedVertex(x=0.1, y=0.3),
                                    documentai.NormalizedVertex(x=0.2, y=0.5),
                                    documentai.NormalizedVertex(x=0.4, y=0.4),
                                ]
                            ),
                        )
                    ),
                    documentai.Document.Page.Block(
                        layout=documentai.Document.Page.Layout(
                            text_anchor=documentai.Document.TextAnchor(
                                text_segments=[
                                    documentai.Document.TextAnchor.TextSegment(
                                        start_index=2, end_index=5
                                    )
                                ]
                            ),
                            bounding_poly=documentai.BoundingPoly(
                                vertices=[
                                    documentai.Vertex(x=10, y=20),
                                    documentai.Vertex(x=30, y=20),
                                    documentai.Vertex(x=30, y=40),
                                    documentai.Vertex(x=10, y=40),
                                ]
                            ),
                        )
                    ),
                ],
            )
        ],
    )
    blocks = parse_documentai(document)
    assert [block.text for block in blocks] == ["ON", "OFF"]
    import pytest

    assert blocks[0].bbox == pytest.approx((0.1, 0.2, 0.3, 0.3))
    assert blocks[1].bbox == pytest.approx((0.1, 0.1, 0.2, 0.1))
