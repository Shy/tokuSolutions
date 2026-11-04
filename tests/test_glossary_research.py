"""Test deterministic discovery, evidence matching, and safe cache enrichment."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.quality.glossary import Glossary, load_glossary
from src.quality.models import Block
from src.quality.research import (
    LookupResult,
    ResearchPage,
    discover_terms,
    enrich_glossary,
    freeze_glossary,
    match_wikidata,
)


class ResearchTests(unittest.TestCase):
    """Ensure public lookup results cannot become unsupported glossary claims."""

    def test_pending_queue_is_readable_without_network(self) -> None:
        """Expose unresolved names and their IDs for JSON review."""
        from click.testing import CliRunner

        from src.quality.__main__ import cli

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "glossary.json"
            path.write_text(
                '{"version":1,"entries":[{"id":"unknown-name",'
                '"japanese":"ゼインカード","english":"ゼインカード",'
                '"notes":"No exact bilingual match."}]}'
            )
            result = CliRunner().invoke(
                cli, ["glossary", "--path", str(path), "--pending"]
            )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("unknown-name", result.output)
            self.assertIn("No exact bilingual match", result.output)

    def test_concurrent_curated_conflict_is_preserved_and_flagged(self) -> None:
        """A lookup completing after a curated edit must not overwrite or hide it."""
        from datetime import date

        from src.quality.glossary import Entry, Source, resolve_glossary

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "glossary.json"
            path.write_text('{"version":1,"entries":[]}')
            page = ResearchPage(
                case_id="page1",
                product="Test",
                franchise="kamen-rider",
                blocks=[Block(id="b0", text="キバ", bbox=(0, 0, 1, 1))],
            )
            source = Source(
                url="https://example.com/source",
                title="Evidence",
                evidence="Fixture bilingual evidence",
                accessed=date(2026, 11, 2),
            )

            class ConcurrentLookup:
                def lookup(self, japanese: str, franchise: str | None) -> LookupResult:
                    path.write_text(
                        Glossary(
                            entries=[
                                Entry(
                                    id="curated",
                                    japanese="キバ",
                                    english="Owner spelling",
                                    franchise="kamen-rider",
                                    product="Test",
                                    status="accepted",
                                    sources=[source],
                                )
                            ]
                        ).model_dump_json()
                    )
                    return LookupResult(english="Kiva", sources=[source])

            report = enrich_glossary(path, [page], ConcurrentLookup())
            glossary = load_glossary(path)
            self.assertEqual(glossary.entries[0].english, "Owner spelling")
            self.assertEqual(len(report.pending_ids), 1)
            self.assertIn(
                "キバ",
                resolve_glossary(
                    glossary, franchise="kamen-rider", product="Test"
                ).review,
            )

    def test_failed_lookups_are_flagged_in_snapshot_and_retryable(self) -> None:
        """A temporary outage must neither approve a name nor poison the cache."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "glossary.json"
            path.write_text('{"version":1,"entries":[]}')
            page = ResearchPage(
                case_id="page1",
                product="Test",
                franchise="kamen-rider",
                blocks=[Block(id="b0", text="キバ", bbox=(0, 0, 1, 1))],
            )
            with patch(
                "src.quality.research.WikidataLookup.lookup",
                return_value=LookupResult(failed=True, reason="Network unavailable"),
            ) as mocked:
                from src.quality.research import WikidataLookup

                lookup = WikidataLookup(root / "cache")
                snapshot = root / "run" / "glossary.json"
                frozen = freeze_glossary(path, [page], lookup, snapshot)
                self.assertEqual(
                    Glossary.model_validate_json(frozen).entries[0].status,
                    "needs_review",
                )
                self.assertEqual(load_glossary(path).entries, [])
                self.assertEqual(
                    freeze_glossary(path, [page], lookup, snapshot), frozen
                )
                self.assertEqual(mocked.call_count, 1)
                freeze_glossary(
                    path, [page], lookup, root / "new-run" / "glossary.json"
                )
                self.assertEqual(mocked.call_count, 2)

    def test_unverified_and_ignored_terms_are_never_applied(self) -> None:
        """Persist pending terms for review and allow owners to dismiss non-names."""
        from src.quality.glossary import Entry, resolve_glossary

        pending = Entry(
            id="pending", japanese="キバ", english="キバ", status="needs_review"
        )
        ignored = Entry(
            id="ignored",
            japanese="ゼインカード",
            english="ゼインカード",
            status="ignored",
        )
        glossary = Glossary(entries=[pending, ignored])
        resolved = resolve_glossary(glossary)
        self.assertEqual(resolved.terms, {})
        self.assertEqual(set(resolved.review), {"キバ"})
        page = ResearchPage(
            case_id="page1",
            product="Test",
            blocks=[Block(id="b0", text="キバ ゼインカード", bbox=(0, 0, 1, 1))],
        )
        self.assertEqual(discover_terms([page], glossary), [])

    def test_conflicting_exact_matches_and_sentai_adaptations_are_pending(self) -> None:
        """Do not choose one conflicting name or import Power Rangers terminology."""
        raw = json.dumps(
            {
                "entities": {
                    "Q1": {
                        "id": "Q1",
                        "lastrevid": 123,
                        "labels": {
                            "ja": {"value": "仮面ライダーキバ"},
                            "en": {"value": "Kamen Rider Kiva"},
                        },
                    },
                    "Q2": {
                        "id": "Q2",
                        "lastrevid": 456,
                        "labels": {
                            "ja": {"value": "仮面ライダーキバ"},
                            "en": {"value": "Kamen Rider Fang"},
                        },
                    },
                }
            }
        )
        self.assertIsNone(match_wikidata("キバ", "kamen-rider", raw).english)
        raw = json.dumps(
            {
                "entities": {
                    "Q3": {
                        "id": "Q3",
                        "lastrevid": 789,
                        "labels": {
                            "ja": {"value": "ゴーカイジャー"},
                            "en": {"value": "Power Rangers Super Megaforce"},
                        },
                        "descriptions": {"en": {"value": "adaptation of Super Sentai"}},
                    }
                }
            }
        )
        self.assertIsNone(match_wikidata("ゴーカイジャー", "sentai", raw).english)

    def test_generic_and_fuzzy_wikidata_matches_are_rejected(self) -> None:
        """A dictionary sense of キバ and a railcar alias are not Rider evidence."""
        raw = json.dumps(
            {
                "entities": {
                    "Q1": {
                        "id": "Q1",
                        "lastrevid": 1,
                        "labels": {"ja": {"value": "牙"}, "en": {"value": "tusk"}},
                        "descriptions": {"en": {"value": "animal tooth"}},
                    },
                    "Q2": {
                        "id": "Q2",
                        "lastrevid": 2,
                        "labels": {
                            "ja": {"value": "キハ"},
                            "en": {"value": "diesel railcar"},
                        },
                        "descriptions": {"en": {"value": "railcar"}},
                    },
                }
            }
        )
        result = match_wikidata("キバ", "kamen-rider", raw)
        self.assertIsNone(result.english)

    def test_exact_bilingual_franchise_name_can_be_accepted(self) -> None:
        """Strip the same explicit Rider prefix in both languages, with evidence."""
        raw = json.dumps(
            {
                "entities": {
                    "Q1": {
                        "id": "Q1",
                        "lastrevid": 123,
                        "labels": {
                            "ja": {"value": "仮面ライダーキバ"},
                            "en": {"value": "Kamen Rider Kiva"},
                        },
                        "descriptions": {"en": {"value": "Japanese television series"}},
                    }
                }
            }
        )
        result = match_wikidata("キバ", "kamen-rider", raw)
        self.assertEqual(result.english, "Kiva")
        self.assertEqual(result.sources[0].kind, "structured_data")
        self.assertIn("123", result.sources[0].url)

    def test_discovery_is_bounded_and_tracks_original_regions(self) -> None:
        """Do not depend on random model suggestions to find names needing lookup."""
        page = ResearchPage(
            case_id="page1",
            product="Test",
            franchise="kamen-rider",
            blocks=[
                Block(id="b0", text="キバ", bbox=(0, 0, 1, 1)),
                Block(
                    id="b1",
                    text="ゼインカードをセット。ゼインカード",
                    bbox=(0, 0, 1, 1),
                ),
            ],
        )
        first = discover_terms([page], Glossary(entries=[]), limit=2)
        self.assertEqual(first, discover_terms([page], Glossary(entries=[]), limit=2))
        self.assertEqual({term.japanese for term in first}, {"キバ", "ゼインカード"})
        self.assertEqual(first[0].occurrences[0].case_id, "page1")

    def test_enrichment_reuses_cache_and_keeps_failed_lookups_pending(self) -> None:
        """Only verified pairs enter the shared cache; lookup failures remain reviewable."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "glossary.json"
            path.write_text('{"version":1,"entries":[]}')
            page = ResearchPage(
                case_id="page1",
                product="Test",
                franchise="kamen-rider",
                blocks=[Block(id="b0", text="キバ", bbox=(0, 0, 1, 1))],
            )

            class Lookup:
                calls = 0

                def lookup(self, japanese: str, franchise: str | None) -> LookupResult:
                    self.calls += 1
                    return match_wikidata(
                        japanese,
                        franchise,
                        json.dumps(
                            {
                                "entities": {
                                    "Q1": {
                                        "id": "Q1",
                                        "lastrevid": 123,
                                        "labels": {
                                            "ja": {"value": "仮面ライダーキバ"},
                                            "en": {"value": "Kamen Rider Kiva"},
                                        },
                                    }
                                }
                            }
                        ),
                    )

            lookup = Lookup()
            result = enrich_glossary(path, [page], lookup, limit=5)
            self.assertEqual(len(result.added_ids), 1)
            enrich_glossary(path, [page], lookup, limit=5)
            self.assertEqual(lookup.calls, 1)
            self.assertEqual(load_glossary(path).entries[0].english, "Kiva")
