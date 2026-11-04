"""Test paired AI assessment, cost accounting and reproducible comparisons."""

from pathlib import Path

import pytest

from src.quality.comparison import (
    reconcile,
    summarize,
    validate_assessment,
    write_comparison_report,
)
from src.quality.comparison_models import (
    Assessment,
    Finding,
    MeteredCall,
    PageComparison,
    Pricing,
)
from src.quality.models import Block


def source() -> list[Block]:
    """Return an instruction with an intentionally wrong English action."""
    return [
        Block(
            id="b0",
            text="レバーを引く",
            translation="Pull the liver",
            bbox=(0, 0, 1, 1),
        )
    ]


def finding(*, a_wrong: bool = True, b_wrong: bool = False) -> Finding:
    """Describe a substantive mistranslation against a verbatim source quote."""
    return Finding(
        block_id="b0",
        category="meaning",
        severity="major",
        a_wrong=a_wrong,
        b_wrong=b_wrong,
        source_quote="レバー",
        expected_meaning="lever",
        explanation="The noun is a mechanical lever.",
        uncertainty="",
    )


def test_only_agreeing_paired_assessments_count_as_fixed() -> None:
    """A single judge claim or unchanged text is never counted as a confirmed fix."""
    original = source()
    candidate = [original[0].model_copy(update={"translation": "Pull the lever"})]
    first = Assessment(covered_ids=["b0"], findings=[finding()])
    reversed_second = Assessment(
        covered_ids=["b0"], findings=[finding(a_wrong=False, b_wrong=True)]
    )
    issues = reconcile(original, candidate, first, reversed_second, first_swapped=False)
    assert issues[0].status == "fixed"
    disagreements = reconcile(
        original,
        candidate,
        first,
        Assessment(covered_ids=["b0"], findings=[]),
        first_swapped=False,
    )
    assert disagreements[0].status == "unresolved"
    unchanged = reconcile(
        original, original, first, reversed_second, first_swapped=False
    )
    assert unchanged[0].status == "unresolved"


def test_unknown_ids_missing_coverage_and_invented_quotes_are_rejected() -> None:
    """Coverage cannot silently omit blocks and model evidence must exist in source."""
    with pytest.raises(ValueError, match="coverage"):
        validate_assessment(Assessment(covered_ids=[], findings=[]), source())
    with pytest.raises(ValueError, match="block"):
        validate_assessment(
            Assessment(
                covered_ids=["b0"],
                findings=[finding().model_copy(update={"block_id": "invented"})],
            ),
            source(),
        )
    assessment = Assessment(
        covered_ids=["b0"],
        findings=[finding().model_copy(update={"source_quote": "電池"})],
    )
    checked = validate_assessment(assessment, source())
    assert checked.findings[0].uncertainty


def test_cost_uses_returned_tokens_and_cache_discount_and_unknown_is_not_zero() -> None:
    """Reasoning is already included in output tokens and must not be billed twice."""
    call = MeteredCall(
        stage="cleanup",
        model="gpt-5-mini-2025-08-07",
        response_id="response",
        input_tokens=1000,
        cached_tokens=400,
        output_tokens=100,
        reasoning_tokens=80,
        elapsed_seconds=2,
    )
    assert call.estimated_usd(Pricing()) == pytest.approx(
        (600 * 0.25 + 400 * 0.025 + 100 * 2) / 1_000_000
    )
    assert call.model_copy(update={"model": "unknown"}).estimated_usd(Pricing()) is None
    assert (
        call.model_copy(update={"input_tokens": None}).estimated_usd(Pricing()) is None
    )


def test_report_separates_ai_claims_diagnostics_and_coverage(tmp_path: Path) -> None:
    """Missing pages and style edits cannot masquerade as substantive improvement."""
    baseline = source()
    candidate = [baseline[0].model_copy(update={"translation": "Pull the lever"})]
    paired = reconcile(
        baseline,
        candidate,
        Assessment(covered_ids=["b0"], findings=[finding()]),
        Assessment(covered_ids=["b0"], findings=[finding(a_wrong=False, b_wrong=True)]),
        first_swapped=False,
    )
    page = PageComparison(
        case_id="test-p1",
        baseline=baseline,
        candidate=candidate,
        cleanup_seconds=1,
        assessment_seconds=2,
        issues=paired,
        diagnostics=[],
    )
    summary = summarize([page], expected_pages=2, pricing=Pricing())
    assert summary["fixed"] == 1
    assert summary["unassessed_pages"] == 1
    assert not summary["complete"]
    report = write_comparison_report(
        tmp_path,
        [page],
        expected_pages=2,
        pricing=Pricing(),
        baseline_label="<unsafe>",
        baseline_seconds=5,
        metadata={"scope": "cleanup only"},
    )
    document = report.read_text()
    assert "&lt;unsafe&gt;" in document
    assert "AI-assessed" in document
    assert "Incomplete" in document
    assert "invoice" in document


def test_cleanup_reports_actual_usage_without_changing_result_contract() -> None:
    """Benchmark instrumentation receives server usage while production return values stay stable."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from openai.types.responses import ResponseUsage

    from src.cleanup import OpenAICleanupResponse, stage3_openai_cleanup
    from src.quality.cleanup_context import ManualData

    usage = ResponseUsage.model_validate(
        {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 0},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 50},
            "total_tokens": 1100,
        }
    )
    manual: ManualData = {
        "meta": {},
        "pages": [{"blocks": [{"text": "レバー", "translation": "Lever"}]}],
    }
    recorded: list[tuple[str, ResponseUsage | None, str]] = []

    def record(model: str, metrics: ResponseUsage | None, response_id: str) -> None:
        recorded.append((model, metrics, response_id))

    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        patch("openai.OpenAI") as client,
    ):
        client.return_value.responses.parse.return_value = SimpleNamespace(
            model="gpt-5-mini-2025-08-07",
            id="resp_test",
            usage=usage,
            output_parsed=OpenAICleanupResponse(
                remove=[], corrections=[], product_name="", tags=[]
            ),
        )
        result = stage3_openai_cleanup(manual, on_response=record)
    assert result == (0, None, None, None)
    assert recorded == [("gpt-5-mini-2025-08-07", usage, "resp_test")]


def test_comparison_resumes_without_recleaning_and_rejects_changed_settings(
    tmp_path: Path,
) -> None:
    """A generated report is reproducible and never writes back into a manual."""
    from unittest.mock import patch

    import pymupdf as fitz

    from src.quality.comparison_run import run_comparison
    from src.quality.core import prepare_suite, run_ocr, run_translation
    from src.quality.models import RunConfig

    root = tmp_path / "project"
    root.mkdir()
    with fitz.open() as pdf:
        pdf.new_page(width=100, height=100)
        pdf.save(root / "scan.pdf")
    manual_dir = root / "manuals" / "Test"
    manual_dir.mkdir(parents=True)
    original = '{"meta":{},"pages":[{"image":"page.webp","blocks":[{"text":"レバーを引く","translation":"Published edit","bbox":[0,0,1,1]}]}]}'
    (manual_dir / "translations.json").write_text(original)
    manifest = root / "pilot.toml"
    manifest.write_text(
        'name="Test"\n[[cases]]\nid="test-p1"\npdf="scan.pdf"\nmanual="manuals/Test"\npage=1\nfeatures=[]\n'
    )
    suite = root / "benchmark-runs" / "test"
    prepare_suite(root, manifest, suite)

    class OCR:
        def recognize(self, image: Path) -> list[Block]:
            return source()

    class Translator:
        def translate(self, texts: list[str]) -> list[str]:
            return ["Pull the liver"]

    run_ocr(suite, "ocr", RunConfig(provider="fixture"), OCR())
    run_translation(
        suite, "baseline", "ocr", RunConfig(provider="fixture"), Translator()
    )
    with (
        patch(
            "src.quality.comparison_run.stage3_openai_cleanup",
            return_value=(0, None, None, None),
        ) as clean,
        patch(
            "src.quality.comparison_run.assess_batch",
            side_effect=lambda *args, **kwargs: Assessment(
                covered_ids=["b0"], findings=[]
            ),
        ) as assess,
    ):
        output = run_comparison(suite, "baseline", "comparison", workers=1)
        assert output.exists()
        assert clean.call_count == 1 and assess.call_count == 2
        run_comparison(suite, "baseline", "comparison", workers=1)
        assert clean.call_count == 1 and assess.call_count == 2
        with pytest.raises(ValueError, match="configuration"):
            run_comparison(
                suite, "baseline", "comparison", judge_model="different", workers=1
            )
    from src.quality.comparison_run import render_comparison

    with patch("src.quality.comparison_run.OpenAI") as api:
        render_comparison(suite, "comparison")
    api.assert_not_called()
    assert (manual_dir / "translations.json").read_text() == original


def test_introduced_persistent_and_style_findings_are_separate() -> None:
    """Count direction and substantive meaning separately from stylistic preference."""
    before = source()
    after = [before[0].model_copy(update={"translation": "Release the lever"})]
    introduced = reconcile(
        before,
        after,
        Assessment(covered_ids=["b0"], findings=[finding(a_wrong=False, b_wrong=True)]),
        Assessment(covered_ids=["b0"], findings=[finding(a_wrong=True, b_wrong=False)]),
        first_swapped=False,
    )
    assert introduced[0].status == "introduced"
    persistent = reconcile(
        before,
        after,
        Assessment(covered_ids=["b0"], findings=[finding(a_wrong=True, b_wrong=True)]),
        Assessment(covered_ids=["b0"], findings=[finding(a_wrong=True, b_wrong=True)]),
        first_swapped=False,
    )
    assert persistent[0].status == "unresolved"
    page = PageComparison(
        case_id="test-p1",
        baseline=before,
        candidate=after,
        cleanup_seconds=0,
        assessment_seconds=0,
        diagnostics=[],
        issues=introduced
        + persistent
        + [introduced[0].model_copy(update={"category": "style"})],
    )
    summary = summarize([page], expected_pages=1, pricing=Pricing())
    assert summary["introduced"] == 1 and summary["unresolved"] == 1
    assert summary["style_or_fluency_findings"] == 1


def test_duplicate_coverage_and_changed_source_geometry_are_rejected() -> None:
    """Duplicated IDs and misaligned regions cannot pass provenance validation."""
    with pytest.raises(ValueError, match="coverage"):
        validate_assessment(Assessment(covered_ids=["b0", "b0"], findings=[]), source())
    assessment = Assessment(covered_ids=["b0"], findings=[])
    with pytest.raises(ValueError, match="source"):
        reconcile(
            source(),
            [source()[0].model_copy(update={"bbox": (0, 0, 0.5, 0.5)})],
            assessment,
            assessment,
            first_swapped=False,
        )


def test_report_evidence_has_unique_ids_for_multiple_categories(tmp_path: Path) -> None:
    """Several findings on one OCR region still need distinct accessible anchors."""
    import re

    before = source()
    after = [before[0].model_copy(update={"translation": "Pull the lever"})]
    issues = reconcile(
        before,
        after,
        Assessment(covered_ids=["b0"], findings=[finding()]),
        Assessment(covered_ids=["b0"], findings=[finding(a_wrong=False, b_wrong=True)]),
        first_swapped=False,
    )
    page = PageComparison(
        case_id="test-p1",
        baseline=before,
        candidate=after,
        cleanup_seconds=1,
        assessment_seconds=1,
        diagnostics=[],
        issues=issues + [issues[0].model_copy(update={"category": "terminology"})],
    )
    report = write_comparison_report(
        tmp_path,
        [page],
        expected_pages=1,
        pricing=Pricing(),
        baseline_label="baseline",
        baseline_seconds=1,
        metadata={},
    )
    anchors = re.findall(r'\bid="([^"]+)"', report.read_text())
    assert len(anchors) == len(set(anchors))


def test_substantive_category_labels_do_not_create_false_disagreement() -> None:
    """Meaning and terminology can identify the same supported correction."""
    before = source()
    after = [before[0].model_copy(update={"translation": "Pull the lever"})]
    issues = reconcile(
        before,
        after,
        Assessment(covered_ids=["b0"], findings=[finding()]),
        Assessment(
            covered_ids=["b0"],
            findings=[
                finding(a_wrong=False, b_wrong=True).model_copy(
                    update={"category": "terminology"}
                )
            ],
        ),
        first_swapped=False,
    )
    assert len(issues) == 1
    assert issues[0].status == "fixed"


def test_assessment_records_usage_even_when_output_is_incomplete(
    tmp_path: Path,
) -> None:
    """A bounded-output failure must not lose the billable response's usage record."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from openai.types.responses import ResponseUsage

    from src.quality.cleanup_context import EditBatch
    from src.quality.comparison_run import assess_batch
    from src.quality.glossary import Glossary
    from src.quality.models import FrozenCase

    case = FrozenCase(
        id="test-p1",
        pdf="scan.pdf",
        manual="manuals/Test",
        page=1,
        features=[],
        image="source.png",
        image_sha256="image",
        snapshot="snapshot.json",
        snapshot_sha256="snapshot",
        pdf_sha256="pdf",
        width=100,
        height=100,
    )
    batch = EditBatch(page=0, image="", blocks=[], neighbors=[], headings=[])
    usage = ResponseUsage.model_validate(
        {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 12,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": 112,
        }
    )
    with patch("src.quality.comparison_run.OpenAI") as client:
        client.return_value.responses.create.side_effect = [
            SimpleNamespace(
                model="gpt-5-mini",
                id="response_first",
                status="incomplete",
                usage=usage,
            ),
            SimpleNamespace(
                model="gpt-5-mini",
                id="response_second",
                status="incomplete",
                usage=usage,
            ),
        ]
        with pytest.raises(ValueError, match="complete"):
            assess_batch(
                tmp_path,
                tmp_path,
                case,
                source(),
                source(),
                batch,
                Glossary(entries=[]),
                "gpt-5-mini",
                swapped=False,
                pass_index=0,
                batch_index=0,
            )
        assert client.return_value.responses.create.call_count == 2
        schema = client.return_value.responses.create.call_args.kwargs["text"][
            "format"
        ]["schema"]
        finding_schema = schema["$defs"]["Finding"]
        assert set(finding_schema["required"]) == set(finding_schema["properties"])
    records = [
        MeteredCall.model_validate_json(path.read_bytes())
        for path in (tmp_path / "calls").glob("*.json")
    ]
    assert len(records) == 2
    assert all(
        record.input_tokens == 100 and record.output_tokens == 12 for record in records
    )


def test_instruction_fix_is_counted_when_only_a_later_fragment_changes(
    tmp_path: Path,
) -> None:
    """A stable anchor must not hide a substantive fix elsewhere in its instruction."""
    original = [
        Block(
            id="a",
            text="装填/引き抜きを",
            translation="Insertion/removal",
            bbox=(0.1, 0.1, 0.2, 0.02),
        ),
        Block(
            id="b",
            text="繰り返すと、",
            translation="To reiterate,",
            bbox=(0.1, 0.125, 0.2, 0.02),
        ),
    ]
    candidate = [
        original[0],
        original[1].model_copy(update={"translation": "when repeated,"}),
    ]
    claim = finding().model_copy(
        update={
            "block_id": "a",
            "related_ids": ["a", "b"],
            "source_quote": "装填/引き抜きを繰り返すと、",
        }
    )
    first = Assessment(covered_ids=["a", "b"], findings=[claim])
    second = Assessment(
        covered_ids=["a", "b"],
        findings=[claim.model_copy(update={"a_wrong": False, "b_wrong": True})],
    )
    issues = reconcile(original, candidate, first, second, first_swapped=False)
    assert issues[0].status == "fixed"
    assert issues[0].related_ids == ["a", "b"]
    # Models sometimes list only the OTHER region under "related_ids".
    implicit_anchor = first.model_copy(deep=True)
    implicit_anchor.findings[0].related_ids = ["b"]
    assert validate_assessment(implicit_anchor, original).findings[0].related_ids == [
        "a",
        "b",
    ]
    report = write_comparison_report(
        tmp_path,
        [
            PageComparison(
                case_id="instruction",
                baseline=original,
                candidate=candidate,
                cleanup_seconds=0,
                assessment_seconds=0,
                issues=issues,
                diagnostics=[],
            )
        ],
        expected_pages=1,
        pricing=Pricing(),
        baseline_label="Previous",
        baseline_seconds=0,
        metadata={},
    ).read_text()
    assert "To reiterate," in report and "when repeated," in report


def test_returned_assessment_is_retained_before_validation_failure(
    tmp_path: Path,
) -> None:
    """Offline validation fixes must not require another paid assessment request."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from src.quality.cleanup_context import EditBatch
    from src.quality.comparison_run import RawAssessment, assess_batch
    from src.quality.glossary import Glossary
    from src.quality.models import FrozenCase

    case = FrozenCase(
        id="test-p1",
        pdf="scan.pdf",
        manual="manuals/Test",
        page=1,
        features=[],
        image="source.png",
        image_sha256="image",
        snapshot="snapshot.json",
        snapshot_sha256="snapshot",
        pdf_sha256="pdf",
        width=100,
        height=100,
    )
    output = '{"covered_ids":[],"findings":[]}'
    response = SimpleNamespace(
        model="gpt-5-mini",
        id="returned-output",
        status="completed",
        usage=None,
        output_text=output,
    )
    with patch("src.quality.comparison_run.OpenAI") as client:
        client.return_value.responses.create.return_value = response
        for _attempt in range(2):
            with pytest.raises(ValueError, match="coverage"):
                assess_batch(
                    tmp_path,
                    tmp_path,
                    case,
                    source(),
                    source(),
                    EditBatch(page=0, image="", blocks=[], neighbors=[], headings=[]),
                    Glossary(entries=[]),
                    "gpt-5-mini",
                    swapped=False,
                    pass_index=0,
                    batch_index=0,
                )
        assert client.return_value.responses.create.call_count == 1
    saved = RawAssessment.model_validate_json(
        (tmp_path / "raw-assessment-0-0.json").read_bytes()
    )
    assert saved.output_text == output and saved.response_id == "returned-output"
    assert not list(tmp_path.glob("assessment-*.json")), (
        "Raw outputs must not match validated-assessment file scans"
    )
