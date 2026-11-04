"""Exercise automatic research at the production manual translation boundary."""

import tempfile
import unittest
from pathlib import Path

from src.quality.pipeline import ManualBlock, translate_manual
from src.quality.research import LookupResult


class PendingLookup:
    """Represent a legitimate name with no verified public English spelling."""

    def lookup(self, japanese: str, franchise: str | None) -> LookupResult:
        """Keep the result pending rather than inventing a spelling."""
        return LookupResult()


class DraftTranslator:
    """Return a draft while retaining all original coordinates."""

    def translate(self, texts: list[str]) -> list[str]:
        """Translate isolated names for the test fixture."""
        return ["Zein Card" for text in texts]


class PipelineTests(unittest.TestCase):
    """Unknown terminology is retained for the final edit without losing the draft."""

    def test_verified_names_are_constrained_and_model_deviations_are_flagged(
        self,
    ) -> None:
        """Do not trust a translation model to obey a glossary without checking."""
        from datetime import date

        from src.quality.glossary import Source

        class VerifiedLookup:
            def lookup(self, japanese: str, franchise: str | None) -> LookupResult:
                return LookupResult(
                    english="Kiva",
                    sources=[
                        Source(
                            url="https://example.com/kiva",
                            title="Fixture",
                            evidence="キバ / Kiva",
                            accessed=date(2026, 11, 2),
                        )
                    ],
                )

        class IgnoringTranslator(DraftTranslator):
            terms: dict[str, str]

            def translate_with_glossary(
                self, texts: list[str], terms: dict[str, str]
            ) -> list[str]:
                self.terms = terms
                return ["Fang"]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "glossary.json").write_text('{"version":1,"entries":[]}')
            block = ManualBlock(text="キバ", page=0, x=0, y=0, width=1, height=1)
            translator = IgnoringTranslator()
            candidate = translate_manual(
                root,
                [block],
                "仮面ライダーゼイン",
                "test-run",
                translator,
                VerifiedLookup(),
            )
            self.assertEqual(translator.terms, {"キバ": "Kiva"})
            self.assertEqual(candidate.flags[0].kind, "glossary-mismatch")

    def test_uncertainties_create_advisory_artifact_without_blocking_draft(
        self,
    ) -> None:
        """Unknown terminology remains advisory while the machine draft is retained."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "glossary.json").write_text('{"version":1,"entries":[]}')
            block = ManualBlock(
                text="ゼインカード", page=0, x=0.1, y=0.2, width=0.5, height=0.1
            )
            candidate = translate_manual(
                root,
                [block],
                "仮面ライダーゼイン",
                "test-run",
                DraftTranslator(),
                PendingLookup(),
            )
            self.assertEqual(candidate.blocks[0].text, "ゼインカード")
            self.assertEqual(candidate.blocks[0].translation, "Zein Card")
            self.assertEqual(candidate.flags[0].kind, "glossary-review")
            self.assertTrue(Path(candidate.review_path).is_file())
            self.assertFalse((root / "manuals").exists())
            with self.assertRaisesRegex(ValueError, "input changed"):
                translate_manual(
                    root,
                    [block.model_copy(update={"text": "キバ"})],
                    "仮面ライダーゼイン",
                    "test-run",
                    DraftTranslator(),
                    PendingLookup(),
                )
