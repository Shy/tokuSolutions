"""Test dedicated-model request and coordinate contracts without credentials."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.quality.models import RunConfig
from src.quality.providers import (
    GoogleTranslator,
    OllamaTranslator,
    normalized_box,
    parse_yomitoku,
)


class ProviderTests(unittest.TestCase):
    """Verify boundaries where provider formats differ from viewer coordinates."""

    def test_yomitoku_keeps_short_labels_and_confidence(self) -> None:
        """Use all polygon corners and retain single-letter diagram labels."""
        raw = '{"words":[{"content":"A","points":[[30,20],[10,30],[20,50],[40,40]],"rec_score":0.7,"det_score":0.9,"direction":"vertical"}]}'
        blocks = parse_yomitoku(raw, 100, 100)
        self.assertEqual(blocks[0].text, "A")
        self.assertEqual(blocks[0].bbox, (0.1, 0.2, 0.3, 0.3))
        self.assertEqual(blocks[0].confidence, 0.7)

    def test_ollama_uses_translation_prompt_and_rejects_truncation(self) -> None:
        """Local inference must request Japanese-to-English and fail if cut short."""
        config = RunConfig(
            provider="ollama",
            model="translategemma:12b",
            endpoint="http://127.0.0.1:11434",
        )
        translator = OllamaTranslator(config)
        response = SimpleNamespace(
            text='{"response":"2 batteries","done":true,"done_reason":"stop"}',
            raise_for_status=lambda: None,
        )
        with patch("requests.post", return_value=response) as post:
            self.assertEqual(translator.translate(["電池2本"]), ["2 batteries"])
            request = post.call_args.kwargs["json"]
            self.assertIn("Japanese (ja)", request["prompt"])
            self.assertIn("English (en)", request["prompt"])
            self.assertTrue(request["prompt"].endswith("\n\n\n電池2本"))
            self.assertEqual(request["options"]["temperature"], 0)
            self.assertFalse(request["stream"])
            translator.translate_with_glossary(
                ["ゼインドライバー本体"], {"ゼインドライバー": "Zein Driver"}
            )
            self.assertTrue(
                post.call_args.kwargs["json"]["prompt"].endswith(
                    "\n\n\nZein Driver本体"
                )
            )
        response.text = '{"response":"cut short","done":true,"done_reason":"length"}'
        with (
            patch("requests.post", return_value=response),
            self.assertRaisesRegex(ValueError, "truncated"),
        ):
            translator.translate(["電池2本"])

    def test_box_clips_only_to_actual_image_bounds(self) -> None:
        """Keep edge text while rejecting degenerate detections."""
        self.assertEqual(
            normalized_box([(-2, 0), (50, 20)], 100, 100), (0, 0, 0.5, 0.2)
        )
        with self.assertRaises(ValueError):
            normalized_box([(10, 10), (10, 10)], 100, 100)

    def test_google_explicitly_requests_translation_llm(self) -> None:
        """The benchmark must not accidentally use the default NMT model."""
        requests: list[dict[str, object]] = []

        class Client:
            def translate_text(self, *, request: dict[str, object]) -> SimpleNamespace:
                requests.append(request)
                return SimpleNamespace(
                    translations=[SimpleNamespace(translated_text="2 batteries")]
                )

        with patch(
            "src.quality.providers.google_translation_client", return_value=Client()
        ):
            provider = GoogleTranslator(
                Path("."), "translation-llm", "test-project", "us-central1"
            )
            self.assertEqual(provider.translate(["電池2本"]), ["2 batteries"])
        self.assertEqual(
            requests[0]["model"],
            "projects/test-project/locations/us-central1/models/general/translation-llm",
        )
        self.assertEqual(requests[0]["source_language_code"], "ja")
        self.assertEqual(requests[0]["contents"], ["電池2本"])

    def test_google_glossary_uses_html_and_returns_plain_text(self) -> None:
        """Protect accepted names while preserving literal source markup in output."""
        requests: list[dict[str, object]] = []

        class Client:
            def translate_text(self, *, request: dict[str, object]) -> SimpleNamespace:
                requests.append(request)
                return SimpleNamespace(
                    translations=[
                        SimpleNamespace(
                            translated_text='Insert <span translate="no">Zein Driver</span>.<br>&lt;Right&gt; &amp; left'
                        )
                    ]
                )

        with patch(
            "src.quality.providers.google_translation_client", return_value=Client()
        ):
            provider = GoogleTranslator(
                Path("."), "translation-llm", "test", "us-central1"
            )
            result = provider.translate_with_glossary(
                ["ゼインドライバー\n<右> & 左"], {"ゼインドライバー": "Zein Driver"}
            )
        self.assertEqual(requests[0]["mime_type"], "text/html")
        self.assertIn(
            '<span translate="no">Zein Driver</span>', str(requests[0]["contents"])
        )
        self.assertEqual(result, ["Insert Zein Driver.\n<Right> & left"])

    def test_protected_name_does_not_introduce_punctuation_gaps(self) -> None:
        """Remove transport spacing after a span while preserving ordinary text."""
        from src.quality.providers import plain_translation_html

        self.assertEqual(
            plain_translation_html(
                '<span translate="no">Zein Driver</span> \'s lever. <span translate="no">Zein Driver</span> .'
            ),
            "Zein Driver's lever. Zein Driver.",
        )


if __name__ == "__main__":
    unittest.main()
