"""Pending terminology remains advisory inside the production translation Activity."""

import unittest
from unittest.mock import patch

from src.activities.translation import translate_blocks_activity


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    """Source-backed glossary uncertainty never requests a human gate."""

    async def test_pending_terminology_does_not_block_machine_translation(self) -> None:
        """Keep advisory notes and finish without waiting for a reviewer."""
        from pathlib import Path
        from types import SimpleNamespace

        from src.quality.models import Flag
        from src.quality.pipeline import ManualCandidate, ManualTranslation

        candidate = ManualCandidate(
            product="Test",
            franchise=None,
            model="google-tllm",
            glossary_sha256="fixture",
            blocks=[
                ManualTranslation(
                    text="ゼインカード",
                    translation="Zein Card",
                    page=0,
                    x=0,
                    y=0,
                    width=1,
                    height=1,
                )
            ],
            flags=[
                Flag(
                    id="b0:review",
                    block_id="b0",
                    kind="glossary-review",
                    reason="Unverified name",
                )
            ],
            review_path="/tmp/candidate.json",
        )
        with (
            patch("src.activities.translation.activity.logger"),
            patch(
                "src.activities.translation.activity.info",
                return_value=SimpleNamespace(workflow_run_id="test-run"),
            ),
            patch(
                "src.quality.pipeline.translate_manual", return_value=candidate
            ) as translate,
        ):
            result = await translate_blocks_activity(
                [
                    {
                        "text": "ゼインカード",
                        "page": 0,
                        "x": 0,
                        "y": 0,
                        "width": 1,
                        "height": 1,
                    }
                ],
                product="Test",
            )
            self.assertTrue(result.success)
            self.assertIsNone(result.error)
            self.assertEqual(result.blocks[0].translated, "Zein Card")
            self.assertEqual(result.notes_path, str(Path(candidate.review_path)))
            self.assertEqual(translate.call_args.args[2:4], ("Test", "test-run"))
            self.assertEqual(translate.call_args.args[4].model, "translation-llm")
            self.assertEqual(translate.call_args.kwargs["model"], "google-tllm")
