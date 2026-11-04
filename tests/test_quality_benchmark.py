"""Exercise benchmark isolation, provenance, resumability, and translation previews."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

from src.quality.core import (
    add_review_flag,
    load_suite,
    prepare_suite,
    run_ocr,
    run_translation,
)
from src.quality.models import Block, RunConfig
from src.quality.report import write_report


class FakeOCR:
    """Provide deterministic recognition without network or model downloads."""

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, image: Path) -> list[Block]:
        """Return both an instruction and a meaningful single-letter label."""
        self.calls += 1
        return [
            Block(id="b0", text="電池2本", bbox=(0.1, 0.1, 0.5, 0.1)),
            Block(id="b1", text="A", bbox=(0.1, 0.3, 0.1, 0.1)),
        ]


class FakeTranslator:
    """Return an intentionally changed number to exercise review flags."""

    def translate(self, texts: list[str]) -> list[str]:
        """Translate inputs in their original order."""
        return ["Insert 3 batteries", "A"]


class BenchmarkTests(unittest.TestCase):
    """Verify real file boundaries with tiny generated PDF fixtures."""

    def test_translation_automatically_researches_and_freezes_pending_terms(
        self,
    ) -> None:
        """Research before translation, expose flags, and reuse a frozen run offline."""
        from src.quality.core import load_candidate
        from src.quality.research import LookupResult

        class NamesOCR:
            def recognize(self, image: Path) -> list[Block]:
                return [Block(id="b0", text="ゼインカード", bbox=(0, 0, 1, 1))]

        class PendingLookup:
            calls = 0

            def lookup(self, japanese: str, franchise: str | None) -> LookupResult:
                self.calls += 1
                return LookupResult()

        class Translator:
            def translate(self, texts: list[str]) -> list[str]:
                return ["Zein Card"]

        self.prepare()
        run_ocr(self.suite_path, "names", RunConfig(provider="fake"), NamesOCR())
        path = self.root / "glossary.json"
        path.write_text('{"version":1,"entries":[]}')
        lookup = PendingLookup()
        results = run_translation(
            self.suite_path,
            "researched",
            "names",
            RunConfig(provider="fake"),
            Translator(),
            glossary_path=path,
            research_lookup=lookup,
        )
        self.assertEqual(lookup.calls, 1)
        self.assertTrue(
            any(flag.kind == "glossary-review" for flag in results[0].flags)
        )
        path.write_text('{"version":1,"entries":[]}')
        repeated = run_translation(
            self.suite_path,
            "researched",
            "names",
            RunConfig(provider="fake"),
            Translator(),
            glossary_path=path,
            research_lookup=lookup,
        )
        self.assertEqual(lookup.calls, 1)
        self.assertEqual(results, repeated)
        _, candidate_results, _ = load_candidate(self.suite_path, "researched")
        self.assertTrue(
            any(flag.kind == "glossary-review" for flag in candidate_results[0].flags)
        )

    def setUp(self) -> None:
        """Create a repository-shaped fixture without production credentials."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pdfs").mkdir()
        self.manual = self.root / "manuals" / "sample" / "translations.json"
        self.manual.parent.mkdir(parents=True)
        self.manual.write_text(
            json.dumps(
                {
                    "meta": {"manual_name": "Test", "source": "sample.pdf"},
                    "pages": [
                        {
                            "image": "pages/page-0.webp",
                            "blocks": [
                                {
                                    "text": "電池2本",
                                    "translation": "2 batteries",
                                    "bbox": [0.1, 0.1, 0.5, 0.1],
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with fitz.open() as document:
            page = document.new_page(width=200, height=300)
            page.insert_text((10, 20), "Source scan")
            document.save(self.root / "pdfs" / "sample.pdf")
        self.manifest = self.root / "pilot.toml"
        self.manifest.write_text(
            'name = "test"\ndpi = 72\n[[cases]]\nid = "sample-p1"\n'
            'pdf = "pdfs/sample.pdf"\nmanual = "manuals/sample"\n'
            'page = 1\nfeatures = ["batteries"]\n',
            encoding="utf-8",
        )
        self.suite_path = self.root / "benchmark-runs" / "pilot"
        self.before = hashlib.sha256(self.manual.read_bytes()).hexdigest()

    def prepare(self) -> None:
        """Freeze the tiny suite used by each integration test."""
        prepare_suite(self.root, self.manifest, self.suite_path)

    def translate(self) -> None:
        """Produce one OCR run and one translation run."""
        self.prepare()
        run_ocr(self.suite_path, "ocr-test", RunConfig(provider="fake"), FakeOCR())
        run_translation(
            self.suite_path,
            "translation-test",
            "ocr-test",
            RunConfig(provider="fake", model="test-v1"),
            FakeTranslator(),
        )

    def test_prepare_isolated_snapshot_and_source_image(self) -> None:
        """Freeze original bytes and render the PDF without modifying manuals."""
        self.prepare()
        suite = load_suite(self.suite_path)
        self.assertEqual(len(suite.cases), 1)
        case = suite.cases[0]
        self.assertEqual((case.width, case.height), (200, 300))
        self.assertEqual(
            (self.suite_path / case.snapshot).read_bytes(), self.manual.read_bytes()
        )
        self.assertEqual(
            hashlib.sha256(self.manual.read_bytes()).hexdigest(), self.before
        )
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_prepare_rejects_production_output_and_path_traversal(self) -> None:
        """Never allow generated output to overwrite manuals or escape the repo."""
        with self.assertRaises(ValueError):
            prepare_suite(self.root, self.manifest, self.manual.parent)
        self.manifest.write_text(
            self.manifest.read_text().replace("pdfs/sample.pdf", "../sample.pdf")
        )
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.suite_path.exists())

    def test_changed_frozen_assets_are_rejected(self) -> None:
        """A modified scan cannot silently reuse a previous result."""
        self.prepare()
        suite = load_suite(self.suite_path)
        (self.suite_path / suite.cases[0].image).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "hash"):
            load_suite(self.suite_path)

    def test_frozen_metadata_must_match_the_original_selection(self) -> None:
        """Do not label existing pixels as a different source page after freezing."""
        self.prepare()
        path = self.suite_path / "suite.json"
        data = json.loads(path.read_text())
        data["cases"][0]["page"] = 2
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "selection"):
            load_suite(self.suite_path)

    def test_glossary_snapshot_is_applied_and_changes_cannot_resume(self) -> None:
        """Bind source-backed terminology to a run and keep original OCR for review."""
        self.prepare()
        run_ocr(self.suite_path, "ocr-test", RunConfig(provider="fake"), FakeOCR())
        glossary_path = self.root / "glossary.json"
        glossary_data = {
            "version": 1,
            "entries": [
                {
                    "id": "battery",
                    "japanese": "電池",
                    "english": "battery",
                    "status": "accepted",
                    "sources": [
                        {
                            "url": "https://example.com/manual",
                            "title": "Manual",
                            "accessed": "2026-11-02",
                            "evidence": "Fixture",
                        }
                    ],
                }
            ],
        }
        glossary_path.write_text(json.dumps(glossary_data))

        class GlossaryTranslator:
            def translate(self, texts: list[str]) -> list[str]:
                raise AssertionError("Matching glossary must reach the model adapter")

            def translate_with_glossary(
                self, texts: list[str], terms: dict[str, str]
            ) -> list[str]:
                self_test.assertEqual(terms, {"電池": "battery"})
                return ["battery 2", "A"]

        self_test = self
        results = run_translation(
            self.suite_path,
            "with-glossary",
            "ocr-test",
            RunConfig(provider="fake"),
            GlossaryTranslator(),
            glossary_path=glossary_path,
        )
        self.assertEqual(results[0].blocks[0].text, "電池2本")
        self.assertFalse(
            any(flag.kind.startswith("glossary") for flag in results[0].flags)
        )
        self.assertEqual(
            (self.suite_path / "runs/with-glossary/glossary.json").read_bytes(),
            glossary_path.read_bytes(),
        )
        write_report(self.suite_path, "with-glossary")
        result_path = self.suite_path / "runs/with-glossary/sample-p1.json"
        edited = json.loads(result_path.read_text())
        edited["blocks"][0]["translation"] = "cell 2"
        edited["flags"] = []
        result_path.write_text(json.dumps(edited))
        # A removed mismatch flag is recovered from the frozen evidence.
        html = write_report(self.suite_path, "with-glossary").read_text()
        self.assertIn("glossary-mismatch-battery", html)
        self.assertIn("Glossary evidence", html)
        glossary_path.write_text(
            glossary_path.read_text().replace('"battery"', '"cell"')
        )
        # Old reports use their own snapshot even after the repository cache changes.
        self.assertTrue(write_report(self.suite_path, "with-glossary").exists())
        with self.assertRaisesRegex(ValueError, "configuration"):
            run_translation(
                self.suite_path,
                "with-glossary",
                "ocr-test",
                RunConfig(provider="fake"),
                GlossaryTranslator(),
                glossary_path=glossary_path,
            )
        (self.suite_path / "runs/with-glossary/glossary.json").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "glossary hash"):
            write_report(self.suite_path, "with-glossary")

    def test_ocr_resumes_without_calls_and_preserves_labels(self) -> None:
        """Completed pages are cached and single letters remain in candidates."""
        self.prepare()
        provider = FakeOCR()
        config = RunConfig(provider="fake")
        first = run_ocr(self.suite_path, "ocr-test", config, provider)
        second = run_ocr(self.suite_path, "ocr-test", config, provider)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0].blocks[1].text, "A")
        with self.assertRaisesRegex(ValueError, "configuration"):
            run_ocr(self.suite_path, "ocr-test", RunConfig(provider="other"), provider)

    def test_failed_page_leaves_no_success_result(self) -> None:
        """Provider failure remains retryable instead of being cached as success."""
        self.prepare()

        class FailingOCR:
            def recognize(self, image: Path) -> list[Block]:
                raise RuntimeError("offline")

        with self.assertRaisesRegex(RuntimeError, "offline"):
            run_ocr(self.suite_path, "failed", RunConfig(provider="fake"), FailingOCR())
        self.assertFalse((self.suite_path / "runs/failed/sample-p1.json").exists())

    def test_translation_flags_numeric_changes_and_binds_to_ocr(self) -> None:
        """Flag changed quantities and reject a changed upstream OCR result."""
        self.translate()
        output = self.suite_path / "runs/translation-test/sample-p1.json"
        result = json.loads(output.read_text())
        self.assertEqual(result["blocks"][0]["translation"], "Insert 3 batteries")
        self.assertTrue(any(flag["kind"] == "numbers" for flag in result["flags"]))
        ocr_path = self.suite_path / "runs/ocr-test/sample-p1.json"
        data = json.loads(ocr_path.read_text())
        data["blocks"][0]["text"] = "電池4本"
        ocr_path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            run_translation(
                self.suite_path,
                "translation-test",
                "ocr-test",
                RunConfig(provider="fake", model="test-v1"),
                FakeTranslator(),
            )

    def test_missing_translations_fail_without_partial_result(self) -> None:
        """Response cardinality must match input; zip must not drop blocks."""
        self.prepare()
        run_ocr(self.suite_path, "ocr-test", RunConfig(provider="fake"), FakeOCR())

        class ShortTranslator:
            def translate(self, texts: list[str]) -> list[str]:
                return ["Only one result"]

        with self.assertRaisesRegex(ValueError, "count"):
            run_translation(
                self.suite_path,
                "short",
                "ocr-test",
                RunConfig(provider="fake"),
                ShortTranslator(),
            )
        self.assertFalse((self.suite_path / "runs/short/sample-p1.json").exists())

    def test_preview_generates_with_advisory_flags_without_approval(self) -> None:
        """Complete machine output is available without decisions or review exports."""
        self.translate()
        report = write_report(self.suite_path, "translation-test")
        html = report.read_text()
        self.assertIn("Source scan", html)
        self.assertIn("Insert 3 batteries", html)
        self.assertIn("Machine translation preview", html)
        self.assertIn("Optional notes for final edit", html)
        self.assertNotIn("data-approved", html)
        self.assertNotIn("Looks right", html)
        self.assertNotIn("Download review", html)
        self.assertNotIn('type="module"', html)
        self.assertFalse(report.with_name("review-template.json").exists())
        add_review_flag(
            self.suite_path,
            "translation-test",
            "sample-p1",
            "b0",
            "meaning",
            "Check whether the source requires insertion or removal.",
        )
        refreshed = write_report(self.suite_path, "translation-test").read_text()
        self.assertIn(
            "Check whether the source requires insertion or removal.", refreshed
        )
        self.assertEqual(
            hashlib.sha256(self.manual.read_bytes()).hexdigest(), self.before
        )

    def test_invalid_boxes_and_duplicate_ids_are_rejected(self) -> None:
        """Reject coordinates that would lose the source crop relationship."""
        with self.assertRaises(ValueError):
            Block(id="b0", text="test", bbox=(0.9, 0.0, 0.2, 0.2))
        self.prepare()

        class DuplicateOCR:
            def recognize(self, image: Path) -> list[Block]:
                block = Block(id="same", text="A", bbox=(0.1, 0.1, 0.1, 0.1))
                return [block, block]

        with self.assertRaisesRegex(ValueError, "duplicate"):
            run_ocr(
                self.suite_path, "duplicate", RunConfig(provider="fake"), DuplicateOCR()
            )


if __name__ == "__main__":
    unittest.main()
