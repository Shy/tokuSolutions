"""Tests for OpenAI-backed translation cleanup."""

import os
import unittest
from unittest.mock import patch

from src.cleanup import OpenAICleanupResponse, OpenAICorrection, stage3_openai_cleanup


class FakeResponses:
    """Record Responses API calls and return a structured cleanup result."""

    def __init__(self) -> None:
        self.model = ""
        self.text_format: type[OpenAICleanupResponse] | None = None

    def parse(
        self,
        *,
        model: str,
        input: str,
        text_format: type[OpenAICleanupResponse],
    ) -> object:
        """Return a representative parsed cleanup response."""
        del input
        self.model = model
        self.text_format = text_format
        parsed = OpenAICleanupResponse(
            remove=["0-1"],
            corrections=[
                OpenAICorrection(index="0-0", translation="Instruction Manual")
            ],
            product_name="CSM Test Driver",
            tags=["csm", "kamen-rider"],
        )
        return type("FakeResponse", (), {"output_parsed": parsed})()


class FakeOpenAI:
    """Minimal OpenAI client used by the cleanup tests."""

    last_api_key = ""
    last_responses: FakeResponses | None = None

    def __init__(self, *, api_key: str) -> None:
        type(self).last_api_key = api_key
        self.responses = FakeResponses()
        type(self).last_responses = self.responses


class FakeBatchResponses:
    """Return one correction for each 100-block cleanup batch."""

    def __init__(self) -> None:
        self.call_count = 0

    def parse(
        self,
        *,
        model: str,
        input: str,
        text_format: type[OpenAICleanupResponse],
    ) -> object:
        """Return a correction targeting the first block in each batch."""
        del model, input, text_format
        block_index = self.call_count * 100
        self.call_count += 1
        parsed = OpenAICleanupResponse(
            remove=[],
            corrections=[
                OpenAICorrection(
                    index=f"0-{block_index}",
                    translation=f"Clean batch {self.call_count}",
                )
            ],
            product_name="",
            tags=[],
        )
        return type("FakeResponse", (), {"output_parsed": parsed})()


class FakeBatchOpenAI:
    """OpenAI client exposing a shared batch-aware response recorder."""

    last_responses: FakeBatchResponses | None = None

    def __init__(self, *, api_key: str) -> None:
        del api_key
        self.responses = FakeBatchResponses()
        type(self).last_responses = self.responses


class OpenAICleanupTests(unittest.TestCase):
    """Verify OpenAI cleanup behavior without making network calls."""

    def test_response_schema_requires_every_property(self) -> None:
        """Keep the structured-output schema compatible with strict mode."""
        schema = OpenAICleanupResponse.model_json_schema()

        self.assertEqual(
            set(schema["required"]),
            set(schema["properties"]),
        )

    def test_response_schema_has_no_free_form_objects(self) -> None:
        """Avoid dictionary schemas unsupported by strict structured output."""
        schema = OpenAICleanupResponse.model_json_schema()

        def find_open_object(value: object) -> bool:
            if isinstance(value, dict):
                additional = value.get("additionalProperties")
                if isinstance(additional, dict):
                    return True
                return any(find_open_object(child) for child in value.values())
            if isinstance(value, list):
                return any(find_open_object(child) for child in value)
            return False

        self.assertFalse(find_open_object(schema))

    def test_structured_response_is_applied(self) -> None:
        """Apply corrections, removals, metadata, and the configured model."""
        translations_data = {
            "meta": {"manual_name": "Test Driver", "source_url": ""},
            "pages": [
                {
                    "blocks": [
                        {"text": "取扱説明書", "translation": "Operating instructions"},
                        {"text": "ノイズ", "translation": "Noise"},
                    ]
                }
            ],
        }

        with (
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_MODEL": "test-model",
                },
                clear=False,
            ),
            patch("openai.OpenAI", FakeOpenAI),
        ):
            corrections, product_name, tags, error = stage3_openai_cleanup(
                translations_data
            )

        self.assertEqual(corrections, 1)
        self.assertEqual(product_name, "CSM Test Driver")
        self.assertEqual(tags, ["csm", "kamen-rider"])
        self.assertIsNone(error)
        self.assertEqual(len(translations_data["pages"][0]["blocks"]), 2)
        self.assertEqual(
            translations_data["pages"][0]["blocks"][0]["translation"],
            "Instruction Manual",
        )
        self.assertEqual(FakeOpenAI.last_api_key, "test-key")
        self.assertIsNotNone(FakeOpenAI.last_responses)
        self.assertEqual(FakeOpenAI.last_responses.model, "test-model")
        self.assertIs(
            FakeOpenAI.last_responses.text_format,
            OpenAICleanupResponse,
        )

    def test_missing_api_key_returns_nonfatal_error(self) -> None:
        """Keep cleanup optional when no OpenAI credential is configured."""
        translations_data = {
            "meta": {"manual_name": "Test Driver", "source_url": ""},
            "pages": [],
        }

        with patch.dict(os.environ, {}, clear=True):
            result = stage3_openai_cleanup(translations_data)

        self.assertEqual(
            result,
            (0, None, None, "OPENAI_API_KEY not set in environment"),
        )

    def test_default_model_is_gpt_5_mini(self) -> None:
        """Use the cost-efficient model when OPENAI_MODEL is not configured."""
        translations_data = {
            "meta": {"manual_name": "Test Driver", "source_url": ""},
            "pages": [{"blocks": [{"text": "取扱説明書", "translation": "Manual"}]}],
        }

        with (
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "test-key"},
                clear=True,
            ),
            patch("openai.OpenAI", FakeOpenAI),
        ):
            stage3_openai_cleanup(translations_data)

        self.assertIsNotNone(FakeOpenAI.last_responses)
        self.assertEqual(FakeOpenAI.last_responses.model, "gpt-5-mini")

    def test_all_blocks_are_reviewed_in_batches(self) -> None:
        """Review manuals larger than one model context batch."""
        translations_data = {
            "meta": {"manual_name": "Large Manual", "source_url": ""},
            "pages": [
                {
                    "blocks": [
                        {
                            "text": f"原文 {block_index}",
                            "translation": f"Translation {block_index}",
                        }
                        for block_index in range(205)
                    ]
                }
            ],
        }

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True),
            patch("openai.OpenAI", FakeBatchOpenAI),
        ):
            corrections, _, _, error = stage3_openai_cleanup(translations_data)

        self.assertIsNone(error)
        self.assertEqual(corrections, 3)
        self.assertIsNotNone(FakeBatchOpenAI.last_responses)
        self.assertEqual(FakeBatchOpenAI.last_responses.call_count, 3)
        self.assertEqual(
            translations_data["pages"][0]["blocks"][200]["translation"],
            "Clean batch 3",
        )

    def test_corrections_use_original_indices_when_blocks_are_removed(self) -> None:
        """Apply corrections before removals can shift block positions."""
        translations_data = {
            "meta": {"manual_name": "Index Test", "source_url": ""},
            "pages": [
                {
                    "blocks": [
                        {"text": "削除", "translation": "Remove"},
                        {"text": "保持", "translation": "Keep"},
                        {"text": "修正", "translation": "Wrong"},
                    ]
                }
            ],
        }

        class IndexResponses:
            def parse(
                self,
                *,
                model: str,
                input: str,
                text_format: type[OpenAICleanupResponse],
            ) -> object:
                del model, input, text_format
                parsed = OpenAICleanupResponse(
                    remove=["0-0"],
                    corrections=[OpenAICorrection(index="0-2", translation="Correct")],
                    product_name="",
                    tags=[],
                )
                return type("FakeResponse", (), {"output_parsed": parsed})()

        class IndexOpenAI:
            def __init__(self, *, api_key: str) -> None:
                del api_key
                self.responses = IndexResponses()

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True),
            patch("openai.OpenAI", IndexOpenAI),
        ):
            corrections, _, _, error = stage3_openai_cleanup(translations_data)

        self.assertIsNone(error)
        self.assertEqual(corrections, 1)
        self.assertEqual(
            translations_data["pages"][0]["blocks"][2]["translation"],
            "Correct",
        )


if __name__ == "__main__":
    unittest.main()
