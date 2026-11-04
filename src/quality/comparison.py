"""Conservative paired assessment and read-only translation comparison reports."""

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from src.quality.comparison_models import (
    Assessment,
    ComparisonIssue,
    Finding,
    PageComparison,
    Pricing,
)
from src.quality.core import write_bytes
from src.quality.instruction_check import units_for_blocks
from src.quality.models import Block


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_assessment(assessment: Assessment, blocks: list[Block]) -> Assessment:
    """Require complete source coverage and downgrade unsupported quotes to uncertainty."""
    sources = {block.id: block for block in blocks}
    if Counter(assessment.covered_ids) != Counter(sources.keys()):
        raise ValueError("assessment coverage differs from requested blocks")
    units = {index: unit for unit in units_for_blocks(blocks) for index in unit.indices}
    checked = assessment.model_copy(deep=True)
    for finding in checked.findings:
        if finding.block_id not in sources:
            raise ValueError("assessment finding references an unknown block")
        source_text = sources[finding.block_id].text
        if finding.related_ids:
            unit = units[finding.block_id]
            if len(finding.related_ids) != len(set(finding.related_ids)) or not set(
                finding.related_ids
            ).issubset(unit.indices):
                # Reject this evidence span, not otherwise complete page coverage.
                finding.related_ids = []
                finding.uncertainty = (
                    finding.uncertainty
                    + " Related IDs cross instruction boundaries; the claimed span remains unverified."
                ).strip()
            else:
                finding.related_ids = unit.indices
                source_text = unit.japanese
        quote = _normalize(finding.source_quote)
        if not quote or quote not in _normalize(source_text):
            finding.uncertainty = (
                finding.uncertainty
                + " Source quote is not present in the OCR region; scan-only/OCR hypothesis remains unverified."
            ).strip()
    return checked


def reconcile(
    baseline: list[Block],
    candidate: list[Block],
    first: Assessment,
    second: Assessment,
    *,
    first_swapped: bool,
) -> list[ComparisonIssue]:
    """Count a fix/regression only when reversed-order assessments agree on its direction."""
    first = validate_assessment(first, baseline)
    second = validate_assessment(second, baseline)
    original = {block.id: block for block in baseline}
    updated = {block.id: block for block in candidate}
    if set(original) != set(updated) or any(
        original[key].text != updated[key].text
        or original[key].bbox != updated[key].bbox
        for key in original
    ):
        raise ValueError(
            "comparison must preserve source block identities and geometry"
        )
    observations: dict[tuple[str, str], dict[int, list[tuple[bool, bool, str]]]] = {}
    representative: dict[tuple[str, str], Finding] = {}
    for pass_index, assessment in enumerate((first, second)):
        swapped = first_swapped if pass_index == 0 else not first_swapped
        for finding in assessment.findings:
            family = "style" if finding.category in {"style", "fluency"} else "content"
            key = (
                finding.related_ids[0] if finding.related_ids else finding.block_id,
                family,
            )
            before, after = (
                (finding.b_wrong, finding.a_wrong)
                if swapped
                else (finding.a_wrong, finding.b_wrong)
            )
            if not before and not after and not finding.uncertainty:
                continue
            representative.setdefault(key, finding)
            observations.setdefault(key, {}).setdefault(pass_index, []).append(
                (before, after, finding.uncertainty)
            )
    issues: list[ComparisonIssue] = []
    for key, passes in observations.items():
        finding = representative[key]
        uncertainty = " ".join(
            sorted({item[2] for items in passes.values() for item in items if item[2]})
        )
        directions = {item[:2] for items in passes.values() for item in items}
        if len(passes) != 2 or len(directions) != 1:
            uncertainty = (
                uncertainty
                + " Assessment passes disagree or only one identified this issue."
            ).strip()
        direction = next(iter(directions))
        status: Literal["fixed", "introduced", "unresolved"] = "unresolved"
        if not uncertainty:
            if direction == (True, False):
                status = "fixed"
            elif direction == (False, True):
                status = "introduced"
        affected = finding.related_ids or [key[0]]
        if status != "unresolved" and all(
            _normalize(original[index].translation)
            == _normalize(updated[index].translation)
            for index in affected
        ):
            status = "unresolved"
            uncertainty = (
                "English is unchanged; this cannot be a fixed or introduced error."
            )
        issues.append(
            ComparisonIssue(
                block_id=finding.block_id,
                related_ids=finding.related_ids,
                category=finding.category,
                severity=finding.severity,
                status=status,
                source_quote=finding.source_quote,
                expected_meaning=finding.expected_meaning,
                explanation=finding.explanation,
                uncertainty=uncertainty,
            )
        )
    return issues


def summarize(
    pages: list[PageComparison], *, expected_pages: int, pricing: Pricing
) -> dict[str, JsonValue]:
    """Separate substantive findings, diagnostic alerts, coverage, metered cost and runtime."""
    counts = Counter(
        issue.status for page in pages for issue in page.issues if issue.substantive
    )
    calls = [call for page in pages for call in page.calls]
    costs = [call.estimated_usd(pricing) for call in calls]
    complete_pages = sum(page.complete for page in pages)
    return {
        "complete": complete_pages == expected_pages,
        "assessed_pages": complete_pages,
        "unassessed_pages": max(0, expected_pages - complete_pages),
        "fixed": counts["fixed"],
        "introduced": counts["introduced"],
        "unresolved": counts["unresolved"],
        "persistent_substantive": sum(
            issue.substantive and issue.status == "unresolved" and not issue.uncertainty
            for page in pages
            for issue in page.issues
        ),
        "uncertain_substantive": sum(
            issue.substantive
            and issue.status == "unresolved"
            and bool(issue.uncertainty)
            for page in pages
            for issue in page.issues
        ),
        "style_or_fluency_findings": sum(
            not issue.substantive for page in pages for issue in page.issues
        ),
        "changed_blocks": sum(
            before.translation != after.translation
            for page in pages
            for before, after in zip(page.baseline, page.candidate, strict=True)
        ),
        "diagnostic_alerts": sum(len(page.diagnostics) for page in pages),
        "cleanup_seconds": sum(page.cleanup_seconds for page in pages),
        "assessment_seconds": sum(page.assessment_seconds for page in pages),
        "cleanup_cost_estimate_usd": _stage_cost(pages, "cleanup", pricing),
        "assessment_cost_estimate_usd": _stage_cost(pages, "assessment", pricing),
        "recorded_cost_estimate_usd": sum(cost for cost in costs if cost is not None),
        "unknown_cost_calls": sum(cost is None for cost in costs),
        "input_tokens": sum(call.input_tokens or 0 for call in calls),
        "cached_input_tokens": sum(call.cached_tokens for call in calls),
        "output_tokens": sum(call.output_tokens or 0 for call in calls),
        "recorded_calls": len(calls),
        "returned_usage_calls": sum(
            call.input_tokens is not None and call.output_tokens is not None
            for call in calls
        ),
    }


def _stage_cost(
    pages: list[PageComparison], stage: str, pricing: Pricing
) -> float | None:
    calls = [call for page in pages for call in page.calls if call.stage == stage]
    estimates = [call.estimated_usd(pricing) for call in calls]
    return (
        None
        if any(cost is None for cost in estimates)
        else sum(cost for cost in estimates if cost is not None)
    )


def write_comparison_report(
    output: Path,
    pages: list[PageComparison],
    *,
    expected_pages: int,
    pricing: Pricing,
    baseline_label: str,
    baseline_seconds: float,
    metadata: dict[str, JsonValue],
) -> Path:
    """Write portable HTML, Markdown and JSON without review controls or publication."""
    summary = summarize(pages, expected_pages=expected_pages, pricing=pricing)
    payload = {
        "summary": summary,
        "pricing": pricing.model_dump(mode="json"),
        "baseline": baseline_label,
        "baseline_translation_seconds": baseline_seconds,
        "metadata": metadata,
        "pages": [page.model_dump(mode="json") for page in pages],
    }
    write_bytes(
        output / "comparison.json",
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(),
    )
    escape = html.escape
    status = "Complete" if summary["complete"] else "Incomplete"
    table = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(str(summary[key]))}</td></tr>"
        for label, key in [
            ("Substantive errors fixed", "fixed"),
            ("Errors introduced", "introduced"),
            ("Unresolved findings", "unresolved"),
            ("AI-agreed persistent problems", "persistent_substantive"),
            ("Uncertain or disputed problems", "uncertain_substantive"),
            ("Assessed pages", "assessed_pages"),
            ("Unassessed pages", "unassessed_pages"),
            ("Changed English blocks", "changed_blocks"),
            ("Style/fluency findings (excluded above)", "style_or_fluency_findings"),
            (
                "Deterministic diagnostic alerts (not proven errors)",
                "diagnostic_alerts",
            ),
        ]
    )
    sections: list[str] = []
    for page in pages:
        original = {block.id: block for block in page.baseline}
        candidate = {block.id: block for block in page.candidate}
        issues = []
        for issue in sorted(
            page.issues,
            key=lambda item: (
                item.status != "introduced",
                item.status != "unresolved",
                not item.substantive,
                item.block_id,
            ),
        ):
            identifiers = issue.related_ids or [issue.block_id]
            source_text = "\n".join(original[index].text for index in identifiers)
            before_text = "\n".join(
                original[index].translation for index in identifiers
            )
            after_text = "\n".join(
                candidate[index].translation for index in identifiers
            )
            issues.append(
                f'<article id="{escape(page.case_id)}-{escape(issue.block_id)}-{escape(issue.category)}"><h3>{escape(issue.status.title())} · {escape(issue.category)} · {escape(issue.block_id)}</h3>'
                f'<p class="source">{escape(source_text)}</p>'
                f'<div class="pair"><div><b>Before</b><p>{escape(before_text)}</p></div><div><b>After</b><p>{escape(after_text)}</p></div></div>'
                f"<p>{escape(issue.explanation)}</p><p>Expected meaning: {escape(issue.expected_meaning)}</p>"
                + (
                    f'<p class="uncertain">Uncertainty: {escape(issue.uncertainty)}</p>'
                    if issue.uncertainty
                    else ""
                )
                + "</article>"
            )
        diagnostics = "".join(
            f"<li>{escape(flag.block_id)} · {escape(flag.kind)}: {escape(flag.reason)}</li>"
            for flag in page.diagnostics
        )
        image = metadata.get("images", {})
        image_url = image.get(page.case_id, "") if isinstance(image, dict) else ""
        source_link = (
            f'<a href="{escape(str(image_url))}" target="_blank" rel="noopener">Original scan</a>'
            if image_url
            else ""
        )
        sections.append(
            f'<section id="{escape(page.case_id)}"><h2>{escape(page.case_id)}</h2>{source_link}<p>{len(page.baseline)} source blocks; {page.cleanup_seconds:.1f}s cleanup; {page.assessment_seconds:.1f}s AI assessment.</p>'
            + "<p>"
            + escape(
                ", ".join(
                    f"{status}: {sum(issue.substantive and issue.status == status for issue in page.issues)}"
                    for status in ("fixed", "introduced", "unresolved")
                )
            )
            + "</p>"
            + (
                f'<p class="uncertain">Incomplete: {escape(page.error)}</p>'
                if not page.complete
                else ""
            )
            + (
                "<details open>"
                if any(
                    issue.substantive and issue.status == "introduced"
                    for issue in page.issues
                )
                else "<details>"
            )
            + f"<summary>AI evidence ({len(issues)} findings)</summary>"
            + "".join(issues)
            + "</details>"
            + f"<details><summary>Deterministic diagnostics ({len(page.diagnostics)})</summary><ul>{diagnostics}</ul></details></section>"
        )
    cleanup_cost = summary["cleanup_cost_estimate_usd"]
    evaluation_cost = summary["assessment_cost_estimate_usd"]
    scope = str(metadata.get("scope", ""))
    limits = "AI-assessed findings are hypotheses, not a verified Japanese reference. Two reversed-order assessment calls must agree before a substantive fix or regression is counted. One content-issue group per source block is counted; substantive category labels may differ between passes, and multiple errors in a region may be grouped. Conflicting directions within a group remain uncertain. Style/fluency preferences are separate. Same-model blind spots remain possible. Unreported errors may exist. No human approval or automatic replacement is required."

    def money(value: JsonValue) -> str:
        return f"${value:.4f}" if isinstance(value, (int, float)) else "unknown"

    known_assessment = sum(
        call.estimated_usd(pricing) or 0
        for page in pages
        for call in page.calls
        if call.stage == "assessment"
    )
    costs = (
        f"Additional cleanup: {money(cleanup_cost)} USD. "
        f"Evaluation only: {money(evaluation_cost) if evaluation_cost is not None else 'at least ' + money(known_assessment)} USD. "
        f"Recorded usage estimate: {money(summary['recorded_cost_estimate_usd'])} USD; unknown-cost calls: {summary['unknown_cost_calls']}. "
        "These are dated list-rate estimates, not an invoice. Unknown usage is excluded, so the recorded amount is a lower bound when any call is unmetered. "
        "Image input and reasoning output are included in returned usage; reasoning is not added twice. "
        "Cached OCR/translation/glossary requests incur no new charge in this experiment. Historical Google billing and glossary-research costs are unavailable and excluded; this is not a full fresh-run price."
    )
    timing = f"Historical baseline translation: {baseline_seconds:.2f}s. Additional cleanup: {sum(page.cleanup_seconds for page in pages):.2f}s summed page work. Evaluation: {sum(page.assessment_seconds for page in pages):.2f}s summed page work (excluded from production latency). Concurrent wall time is reported separately in run metadata."
    observed_wall = metadata.get("observed_work_wall_seconds")
    if isinstance(observed_wall, (int, float)):
        timing += f" Observed comparison elapsed time, including recovery: approximately {observed_wall / 60:.1f} minutes."
    if summary["unknown_cost_calls"]:
        timing += " Request work without returned metrics is excluded from the recorded totals."
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Automated translation comparison</title><style>body{font:16px/1.5 system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#202731;background:#f0f2f5}section,header{background:white;padding:24px;margin:20px 0;border-radius:12px}h1{font-size:1.8rem}h3{font-size:1rem}table{border-collapse:collapse;width:100%}td,th{text-align:left;border-bottom:1px solid #ddd;padding:8px}p{white-space:pre-wrap;overflow-wrap:anywhere}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{border-top:1px solid #ddd;padding:18px 0}.uncertain{color:#8a4800}.source{font-size:.9rem;color:#465365}li{margin:8px 0}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>"
        f"<header><h1>Automated translation comparison</h1><p>{status} · AI-assessed · {escape(baseline_label)}</p><p>{escape(scope)}</p><table>{table}</table>"
        f"<p>{escape(limits)}</p><h2>Runtime</h2><p>{escape(timing)}</p><h2>Cost</h2><p>{escape(costs)}</p>"
        f'<p>Rates verified {pricing.as_of}: input ${pricing.input_per_million}/M; cached input ${pricing.cached_per_million}/M; output ${pricing.output_per_million}/M. <a href="{escape(pricing.source)}">Official pricing source</a></p>'
        '<p><a href="comparison.json">Full machine-readable results and usage</a> · <a href="report.md">Markdown report</a></p></header>'
        + "".join(sections)
        + "</html>"
    )
    write_bytes(output / "report.html", document.encode())
    markdown = (
        f"# Automated translation comparison\n\n{status} · AI-assessed\n\n{scope}\n\n"
        + "\n".join(f"- {key}: {value}" for key, value in summary.items())
        + f"\n\n{limits}\n\n{timing}\n\n{costs}\n\nRates: [{pricing.model}, {pricing.as_of}]({pricing.source}).\n"
    )
    write_bytes(output / "report.md", markdown.encode())
    return output / "report.html"
