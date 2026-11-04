"""Verify shared glossary scoping and conflict handling before model integration."""

import unittest
from datetime import date
from pathlib import Path

from click.testing import CliRunner

from src.quality.__main__ import cli
from src.quality.glossary import (
    Entry,
    Glossary,
    Source,
    glossary_flags,
    load_glossary,
    render_glossary_input,
    resolve_glossary,
)
from src.quality.models import Block


def entry(
    identifier: str,
    target: str,
    *,
    franchise: str | None = None,
    product: str | None = None,
    status: str = "accepted",
) -> Entry:
    """Make a source-backed fixture with the same Japanese term."""
    return Entry.model_validate(
        {
            "id": identifier,
            "japanese": "通常モード",
            "english": target,
            "franchise": franchise,
            "product": product,
            "status": status,
            "sources": [
                Source(
                    url="https://example.com/manual",
                    title="Test manual",
                    evidence="Fixture evidence",
                    accessed=date(2026, 11, 2),
                ).model_dump(mode="json")
            ],
        }
    )


class GlossaryTests(unittest.TestCase):
    """Keep reuse safe when one file contains several franchises and products."""

    def test_product_override_and_franchise_boundaries(self) -> None:
        """Specific product terminology wins without leaking into other franchises."""
        glossary = Glossary(
            entries=[
                entry("generic", "Normal Mode"),
                entry("sentai", "Standard Mode", franchise="sentai"),
                entry(
                    "product",
                    "Normal Transformation Mode",
                    franchise="sentai",
                    product="GoKaiKeys",
                ),
            ]
        )
        self.assertEqual(
            resolve_glossary(glossary, franchise="sentai", product="GoKaiKeys")
            .terms["通常モード"]
            .english,
            "Normal Transformation Mode",
        )
        self.assertEqual(
            resolve_glossary(glossary, franchise="sentai").terms["通常モード"].english,
            "Standard Mode",
        )
        self.assertEqual(
            resolve_glossary(glossary, franchise="kamen-rider")
            .terms["通常モード"]
            .english,
            "Normal Mode",
        )
        self.assertEqual(
            resolve_glossary(glossary).terms["通常モード"].english, "Normal Mode"
        )

    def test_conflicting_accepted_entries_require_review(self) -> None:
        """Do not silently select a spelling from equally applicable sources."""
        glossary = Glossary(
            entries=[entry("first", "Normal Mode"), entry("second", "Standard Mode")]
        )
        result = resolve_glossary(glossary)
        self.assertNotIn("通常モード", result.terms)
        self.assertEqual(set(result.review["通常モード"]), {"first", "second"})

    def test_pending_entries_are_not_applied(self) -> None:
        """Uncertain evidence stays visible without replacing an accepted term."""
        glossary = Glossary(
            entries=[
                entry("accepted", "Normal Mode"),
                entry("uncertain", "Standard Mode", status="needs_review"),
            ]
        )
        result = resolve_glossary(glossary)
        self.assertEqual(result.terms["通常モード"].english, "Normal Mode")
        self.assertEqual(result.review["通常モード"], ["uncertain"])

    def test_duplicate_ids_are_rejected_and_repo_seed_has_source(self) -> None:
        """Ensure edits remain identifiable and the committed seed is usable."""
        with self.assertRaisesRegex(ValueError, "duplicate"):
            Glossary(entries=[entry("same", "Normal"), entry("same", "Normal")])
        glossary = load_glossary(Path(__file__).resolve().parents[1] / "glossary.json")
        result = resolve_glossary(glossary, franchise="kamen-rider")
        self.assertEqual(result.terms["ゼインドライバー"].english, "Zein Driver")
        self.assertTrue(result.terms["ゼインドライバー"].sources)

    def test_cli_validates_and_shows_contextual_terms(self) -> None:
        """Expose the shared cache without requiring API credentials or models."""
        result = CliRunner().invoke(cli, ["glossary", "--franchise", "kamen-rider"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("ゼインドライバー → Zein Driver", result.output)

    def test_html_protection_preserves_literals_and_katakana_boundaries(self) -> None:
        """Protect complete terms without corrupting a longer unregistered name."""
        text = "キバット <右> & キバ\nゼインドライバー本体"
        rendered = render_glossary_input(
            text, {"キバ": "Kiva", "ゼインドライバー": "Zein Driver"}, html_mode=True
        )
        self.assertIn("キバット &lt;右&gt; &amp;", rendered)
        self.assertIn('<span translate="no">Kiva</span><br>', rendered)
        self.assertIn('<span translate="no">Zein Driver</span>本体', rendered)

    def test_longest_accepted_term_wins(self) -> None:
        """Do not replace a shorter term inside an accepted longer term."""
        self.assertEqual(
            render_glossary_input(
                "キバ エンペラーフォーム",
                {"キバ": "Kiva", "キバ エンペラーフォーム": "Kiva Emperor Form"},
                html_mode=False,
            ),
            "Kiva Emperor Form",
        )

    def test_output_mismatches_and_pending_terms_are_flagged(self) -> None:
        """Preserve source-linked review when a model ignores accepted terminology."""
        glossary = Glossary(
            entries=[
                entry("accepted", "Normal Mode"),
                entry("pending", "Standard Mode", status="needs_review"),
            ]
        )
        block = Block(
            id="b0",
            text="通常モード",
            translation="Alternative mode",
            bbox=(0, 0, 1, 1),
        )
        flags = glossary_flags([block], resolve_glossary(glossary), glossary)
        self.assertEqual(
            {flag.kind for flag in flags}, {"glossary-review", "glossary-mismatch"}
        )
        self.assertTrue(all(flag.block_id == "b0" for flag in flags))


if __name__ == "__main__":
    unittest.main()


def test_product_keys_can_use_their_natural_plural() -> None:
    """A plural product noun preserves the canonical name, unlike a longer compound."""
    glossary = Glossary(
        entries=[
            entry("key", "Ranger Key").model_copy(update={"japanese": "レンジャーキー"})
        ]
    )
    resolution = resolve_glossary(glossary)
    block = Block(
        id="key", text="レンジャーキー", translation="Ranger Keys", bbox=(0, 0, 1, 1)
    )
    assert not glossary_flags([block], resolution, glossary)
    assert glossary_flags(
        [block.model_copy(update={"translation": "Ranger Keyswitch"})],
        resolution,
        glossary,
    )
