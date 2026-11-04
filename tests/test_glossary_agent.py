"""Test source-grounded, unattended glossary research without network calls."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.quality.glossary import Glossary, load_glossary
from src.quality.models import Block
from src.quality.research import LookupResult, ResearchPage, enrich_glossary
from src.quality.web_research import (
    GlossaryResearcher,
    ResearchFinding,
    ResearchFindings,
    validate_finding,
)


class NoMatch:
    """Represent a name absent from Wikidata."""

    def lookup(self, japanese: str, franchise: str | None) -> LookupResult:
        """Return an unresolved name without interrupting translation."""
        return LookupResult()


def page() -> ResearchPage:
    """Supply mixed-script names missed by katakana-only discovery."""
    return ResearchPage(
        case_id="p0",
        product="Test Driver",
        franchise="kamen-rider",
        blocks=[
            Block(
                id="b0",
                text="仮面ライダー ゼインカードを使う。ゼインカードで必殺技を発動。",
                bbox=(0, 0, 1, 1),
            )
        ],
    )


def finding() -> ResearchFinding:
    """Return a source-backed proposed product term."""
    return ResearchFinding(
        japanese="ゼインカード",
        english="Zein Card",
        franchise="kamen-rider",
        product="Test Driver",
        url="https://www.tv-asahi.co.jp/zein/",
        title="Zein",
        evidence="ゼインカード / Zein Card",
        reason="Explicit bilingual product label",
    )


def test_only_observed_bilingual_evidence_can_be_accepted() -> None:
    """A source URL alone or an invented quote is insufficient."""
    proposed = finding()
    assert (
        validate_finding(
            proposed, [page()], {proposed.url}, "ゼインカード / Zein Card"
        ).english
        == "Zein Card"
    )
    assert (
        validate_finding(proposed, [page()], set(), proposed.evidence).english is None
    )
    assert (
        validate_finding(
            proposed, [page()], {proposed.url}, "different page text"
        ).english
        is None
    )
    assert (
        validate_finding(
            proposed.model_copy(update={"japanese": "架空カード"}),
            [page()],
            {proposed.url},
            "架空カード / Zein Card",
        ).english
        is None
    )
    assert (
        validate_finding(
            proposed.model_copy(update={"url": "https://evil.example/"}),
            [page()],
            {"https://evil.example/"},
            proposed.evidence,
        ).english
        is None
    )


def test_web_agent_promotes_pending_placeholder_and_reuses_evidence(
    tmp_path: Path,
) -> None:
    """Research happens in the pipeline and upgrades only untouched automatic placeholders."""
    glossary_path = tmp_path / "glossary.json"
    glossary_path.write_text(Glossary(entries=[]).model_dump_json())
    enrich_glossary(glossary_path, [page()], NoMatch())
    assert load_glossary(glossary_path).entries[0].status == "needs_review"
    researcher = GlossaryResearcher(NoMatch(), tmp_path / "cache")
    with (
        patch.object(
            researcher,
            "_search",
            return_value=(ResearchFindings(findings=[finding()]), {finding().url}),
        ),
        patch.object(researcher, "_page_text", return_value=finding().evidence),
    ):
        report = enrich_glossary(glossary_path, [page()], researcher)
    assert report.policy == "source-grounded-web-v1"
    entry = load_glossary(glossary_path).entries[0]
    assert entry.status == "accepted"
    assert entry.english == "Zein Card"
    assert entry.sources[0].accessed == date.today()


def test_agent_failure_keeps_translation_research_nonfatal(tmp_path: Path) -> None:
    """Network and model errors keep source-backed results and leave names unresolved."""
    researcher = GlossaryResearcher(NoMatch(), tmp_path)
    with patch.object(
        researcher, "_search", side_effect=RuntimeError("quota unavailable")
    ):
        report = researcher.research([page()], [], Glossary(entries=[]), limit=20)
    assert report.outcomes
    assert all(outcome.english is None for outcome in report.outcomes)
    assert all(outcome.failed for outcome in report.outcomes)


def test_conflicting_web_spellings_cannot_be_resolved_by_last_result(
    tmp_path: Path,
) -> None:
    """Conflicting sources remain provisional even when another result repeats one."""
    researcher = GlossaryResearcher(NoMatch(), tmp_path)
    alternate = finding().model_copy(
        update={"english": "Zein Cards", "evidence": "ゼインカード / Zein Cards"}
    )
    with (
        patch.object(
            researcher,
            "_search",
            return_value=(
                ResearchFindings(findings=[finding(), alternate, finding()]),
                {finding().url},
            ),
        ),
        patch.object(
            researcher,
            "_page_text",
            return_value="ゼインカード / Zein Card ゼインカード / Zein Cards",
        ),
    ):
        report = researcher.research([page()], [], Glossary(entries=[]), limit=20)
    assert report.outcomes[0].english is None
    assert "Conflicting" in report.outcomes[0].reason


def test_search_caches_validated_findings_with_tool_provenance(tmp_path: Path) -> None:
    """Generated URLs are not substituted for the API's consulted-source list."""
    from types import SimpleNamespace

    researcher = GlossaryResearcher(NoMatch(), tmp_path)
    response = SimpleNamespace(
        status="completed",
        output_parsed=ResearchFindings(findings=[finding()]),
        output=[
            SimpleNamespace(
                type="web_search_call",
                model_dump=lambda: {
                    "type": "web_search_call",
                    "action": {"sources": [{"url": finding().url}]},
                },
            ),
            SimpleNamespace(type="message"),
        ],
    )
    with (
        patch("src.quality.web_research.OpenAI") as client,
        patch.dict("os.environ", {"GLOSSARY_MODEL": "gpt-5-mini"}),
    ):
        client.return_value.responses.parse.return_value = response
        first = researcher._search("Japanese source context")
        second = researcher._search("Japanese source context")
    assert (
        client.return_value.responses.parse.call_args.kwargs["max_output_tokens"]
        == 16000
    )
    assert client.return_value.responses.parse.call_args.kwargs["reasoning"] == {
        "effort": "low"
    }
    assert first == second
    assert first[1] == {finding().url}
    assert client.return_value.responses.parse.call_count == 1
    assert client.return_value.responses.parse.call_args.kwargs["include"] == [
        "web_search_call.action.sources"
    ]


def test_search_accepts_nullable_sources_and_records_opened_pages(
    tmp_path: Path,
) -> None:
    """Real web-search actions can omit source lists while opening a source page."""
    from types import SimpleNamespace

    response = SimpleNamespace(
        status="completed",
        output_parsed=ResearchFindings(findings=[]),
        output=[
            SimpleNamespace(
                type="web_search_call",
                model_dump=lambda: {
                    "type": "web_search_call",
                    "action": {"type": "search", "sources": None},
                },
            ),
            SimpleNamespace(
                type="web_search_call",
                model_dump=lambda: {
                    "type": "web_search_call",
                    "action": {"type": "open_page", "url": finding().url},
                },
            ),
        ],
        model_dump=lambda: {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {"type": "search", "sources": None},
                }
            ]
        },
    )
    with patch("src.quality.web_research.OpenAI") as client:
        client.return_value.responses.parse.return_value = response
        _, urls = GlossaryResearcher(NoMatch(), tmp_path)._search("Source context")
    assert urls == {finding().url}
