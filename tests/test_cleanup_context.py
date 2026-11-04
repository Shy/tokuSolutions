"""Protect context boundaries, terminology and source data during AI cleanup."""

import base64
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from pydantic import JsonValue, TypeAdapter

from src.cleanup import OpenAICleanupResponse, OpenAICorrection, stage3_openai_cleanup
from src.quality.cleanup_context import ContextManual, ManualData, build_batches
from src.quality.glossary import Entry, Glossary, Source


def test_page_batches_include_neighbors_without_crossing_pages() -> None:
    """Chunk oversized pages without losing neighboring source or stable IDs."""
    manual: ManualData = {
        "meta": {},
        "pages": [
            {
                "blocks": [
                    {
                        "text": f"文 {index}",
                        "translation": f"Sentence {index}",
                        "bbox": [0, 0, 1, 0.1],
                    }
                    for index in range(105)
                ]
            },
            {"blocks": [{"text": "次", "translation": "Next"}]},
        ],
    }
    batches = build_batches(manual)
    assert [len(batch.blocks) for batch in batches] == [100, 5, 1]
    assert batches[1].blocks[0].index == "0-100"
    assert batches[1].neighbors[0].index == "0-98"
    assert batches[2].blocks[0].index == "1-0"
    assert not batches[2].neighbors


def test_cleanup_uses_image_and_frozen_glossary_and_preserves_blocks(
    tmp_path: Path,
) -> None:
    """An AI correction cannot delete source or replace a protected product spelling."""
    image_path = tmp_path / "pages" / "page-0.webp"
    image_path.parent.mkdir()
    Image.new("RGB", (100, 100), "white").save(image_path)
    glossary_path = tmp_path / "glossary.json"
    glossary_path.write_text(
        Glossary(
            entries=[
                Entry(
                    id="zein",
                    japanese="ゼインドライバー",
                    english="Zein Driver",
                    status="accepted",
                    sources=[
                        Source(
                            url="https://www.tv-asahi.co.jp/zein/",
                            title="Zein",
                            evidence="ゼインドライバー / Zein Driver",
                            accessed=date.today(),
                        )
                    ],
                )
            ]
        ).model_dump_json()
    )
    manual: ManualData = {
        "meta": {"manual_name": "Zein"},
        "pages": [
            {
                "image": "pages/page-0.webp",
                "blocks": [
                    {
                        "text": "ゼインドライバー",
                        "translation": "Zein Driver",
                        "bbox": [0, 0, 1, 0.2],
                    },
                    {
                        "text": "レバーを引く",
                        "translation": "Pull the liver",
                        "bbox": [0, 0.2, 1, 0.2],
                    },
                    {"text": "ON", "translation": "ON", "bbox": [0, 0.4, 0.1, 0.1]},
                ],
            }
        ],
    }
    before = ContextManual.model_validate(deepcopy(manual))
    responses = SimpleNamespace(
        parse=lambda **kwargs: SimpleNamespace(
            output_parsed=OpenAICleanupResponse(
                remove=["0-2"],
                corrections=[
                    OpenAICorrection(index="0-0", translation="Zein Screwdriver"),
                    OpenAICorrection(index="0-1", translation="Pull the lever"),
                    OpenAICorrection(index="0-2", translation="Enabled"),
                ],
                product_name="",
                tags=[],
            )
        )
    )
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        patch("openai.OpenAI", return_value=SimpleNamespace(responses=responses)),
        patch.object(responses, "parse", wraps=responses.parse) as parse,
    ):
        result = stage3_openai_cleanup(
            manual, glossary_path=str(glossary_path), manual_dir=tmp_path
        )
    assert result[3] is None
    assert result[0] == 1
    after = ContextManual.model_validate(manual)
    assert len(after.pages[0].blocks) == 3
    assert after.pages[0].blocks[0] == before.pages[0].blocks[0]
    assert after.pages[0].blocks[1].translation == "Pull the lever"
    assert after.pages[0].blocks[2] == before.pages[0].blocks[2]
    payload = parse.call_args.kwargs["input"]
    contents = payload[0]["content"]
    prompt = contents[0]["text"]
    assert "Zein Driver" in prompt and "bbox" in prompt and "neighbors" in prompt
    assert contents[1]["type"] == "input_image"
    assert base64.b64decode(contents[1]["image_url"].split(",")[1])
    assert [block.text for block in after.pages[0].blocks] == [
        block.text for block in before.pages[0].blocks
    ]


def test_small_section_gets_scan_crop_without_changing_geometry(tmp_path: Path) -> None:
    """Dense pages provide a detailed section image alongside the page overview."""
    from src.quality.cleanup_context import image_input

    Image.new("RGB", (4000, 4000), "white").save(tmp_path / "page.webp")
    batches = build_batches(
        {
            "meta": {},
            "pages": [
                {
                    "image": "page.webp",
                    "blocks": [
                        {
                            "text": "レバー",
                            "translation": "Liver",
                            "bbox": [0.2, 0.2, 0.1, 0.1],
                        }
                    ],
                }
            ],
        }
    )
    payload = image_input("Page instructions", batches[0], tmp_path)
    assert not isinstance(payload, str)
    content = TypeAdapter(list[dict[str, JsonValue]]).validate_python(payload)[0][
        "content"
    ]
    assert isinstance(content, list) and len(content) == 4


def test_possible_ruby_requires_spatial_support() -> None:
    """Reading order alone cannot classify a hiragana instruction as ruby."""
    batch = build_batches(
        {
            "meta": {},
            "pages": [
                {
                    "blocks": [
                        {
                            "text": "でんち",
                            "translation": "Denchi",
                            "bbox": [0.1, 0.1, 0.2, 0.01],
                        },
                        {
                            "text": "電池",
                            "translation": "Battery",
                            "bbox": [0.1, 0.12, 0.2, 0.04],
                        },
                        {
                            "text": "あける",
                            "translation": "Open",
                            "bbox": [0.7, 0.7, 0.2, 0.01],
                        },
                    ]
                }
            ],
        }
    )[0]
    assert batch.blocks[0].possible_ruby
    assert not batch.blocks[2].possible_ruby
