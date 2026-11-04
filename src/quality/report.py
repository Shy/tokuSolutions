"""Generate read-only machine-translation previews with optional editing notes."""

import html
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from src.quality.core import (
    digest,
    load_candidate,
    load_run_glossary,
    quality_flags,
    run_path,
    safe_path,
)
from src.quality.glossary import matching_terms, resolve_case_glossary
from src.quality.models import Manual, Run


def escape(value: str) -> str:
    """Escape all source and model strings before inserting HTML."""
    return html.escape(value, quote=True)


def write_report(directory: Path, run_id: str) -> Path:
    """Preview finished machine output; advisory notes never require decisions."""
    suite, results, _fingerprint = load_candidate(directory, run_id)
    destination = run_path(directory, run_id)
    run = Run.model_validate_json((destination / "run.json").read_bytes())
    glossary, _raw_glossary = load_run_glossary(directory, run_id, run)
    sections: list[str] = []
    for case, result in zip(suite.cases, results, strict=True):
        manual = Manual.model_validate_json(
            safe_path(directory, case.snapshot).read_bytes()
        )
        baseline = manual.pages[case.page - 1]
        previous = "".join(
            f"<p>{escape(block.translation)}</p>" for block in baseline.blocks
        )
        glossary_details = ""
        if glossary is not None:
            resolution = resolve_case_glossary(glossary, case)
            source_text = "\n".join(block.text for block in result.blocks)
            matched = set(
                matching_terms(
                    source_text, set(resolution.terms) | set(resolution.review)
                )
            )
            identifiers = {
                resolution.terms[term].id
                for term in matched
                if term in resolution.terms
            }
            identifiers.update(
                identifier
                for term in matched
                if term in resolution.review
                for identifier in resolution.review[term]
            )
            items: list[str] = []
            for entry in glossary.entries:
                if entry.id not in identifiers:
                    continue
                sources = (
                    "; ".join(
                        f'<a href="{escape(source.url)}" target="_blank" rel="noopener noreferrer">{escape(source.title)}</a>'
                        f" ({source.accessed.isoformat()}): {escape(source.evidence)}"
                        for source in entry.sources
                    )
                    or "Spelling not yet verified; machine translation is retained."
                )
                items.append(
                    f"<li>{escape(entry.japanese)} → {escape(entry.english)} ({escape(entry.status)}). {sources}</li>"
                )
            if items:
                glossary_details = f"<details><summary>Glossary evidence</summary><ul>{''.join(items)}</ul></details>"
        flags = {
            flag.id: flag
            for flag in result.flags + quality_flags(result.blocks, translated=True)
        }
        notes = "".join(
            f'<li data-note="{escape(flag.id)}"><a href="#{case.id}-{escape(flag.block_id)}">Show text</a>: {escape(flag.reason)}</li>'
            for flag in flags.values()
        )
        candidates: list[str] = []
        with Image.open(safe_path(directory, case.image)) as source_image:
            for block in result.blocks:
                left, top, width, height = block.bbox
                crop_id = digest(str(block.bbox).encode())[:24]
                crop_relative = f"crops/{case.image_sha256}/{crop_id}.webp"
                crop_path = safe_path(directory, crop_relative)
                if not crop_path.exists():
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    source_image.crop(
                        (
                            int(left * case.width),
                            int(top * case.height),
                            max(
                                int((left + width) * case.width),
                                int(left * case.width) + 1,
                            ),
                            max(
                                int((top + height) * case.height),
                                int(top * case.height) + 1,
                            ),
                        )
                    ).save(crop_path, format="WEBP", lossless=True)
                candidates.append(
                    f'<article id="{case.id}-{escape(block.id)}"><p class="translation">{escape(block.translation)}</p>'
                    "<details><summary>Original Japanese</summary>"
                    f'<img class="crop" loading="lazy" src="../../{crop_relative}" alt="Source crop for {escape(block.id)}">'
                    f'<p lang="ja">{escape(block.text)}</p></details></article>'
                )
        sections.append(
            f'<section id="{case.id}"><h2>{escape(Path(case.pdf).stem)} — page {case.page}</h2>'
            f'<details><summary>Source scan</summary><img class="scan" loading="lazy" src="../../{quote(case.image)}" alt="Source scan"></details>'
            f"{glossary_details}"
            f"<details><summary>Optional notes for final edit ({len(flags)})</summary>"
            f"<p>These notes do not block generation or require approval.</p><ul>{notes}</ul></details>"
            f"<details><summary>Previous translation</summary>{previous}</details>"
            f"<h3>Proposed English</h3>{''.join(candidates)}</section>"
        )
    navigation = " · ".join(
        f'<a href="#{case.id}">{escape(Path(case.pdf).stem)} p.{case.page}</a>'
        for case in suite.cases
    )
    glossary_link = (
        '<a href="glossary.json">Frozen glossary</a>'
        if glossary is not None
        else "Glossary off"
    )
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Machine translation preview</title>"
        "<style>body{font:16px/1.5 system-ui;margin:24px auto;max-width:1000px;padding:0 20px;color:#202731;background:#eef1f5}"
        "section{background:white;border:1px solid #d7dde6;border-radius:10px;margin:24px 0;padding:24px}h1{font-size:1.7rem}h2{font-size:1.3rem}"
        "article{padding:10px 0;border-top:1px solid #e3e7ed;scroll-margin-top:24px}p{white-space:pre-wrap;overflow-wrap:anywhere}"
        "details{margin:8px 0;color:#485667}summary{cursor:pointer}a{color:#174f8d}.crop{max-width:100%;max-height:300px;object-fit:contain}"
        ".scan{max-width:100%}nav{display:flex;flex-wrap:wrap;gap:8px}.meta{font-size:.9rem;color:#485667}li{margin:8px 0}"
        "@media(max-width:700px){body{padding:0 12px}section{padding:16px}}</style>"
        "<h1>Machine translation preview</h1><p>Use the proposed English as the machine draft. Human corrections happen in the final editor; no page-by-page approval is required here.</p>"
        f'<p class="meta">{escape(run_id)} · {escape(run.config.provider)} · {glossary_link} · <a href="../../index.html">All previews</a></p>'
        f'<nav aria-label="Preview pages">{navigation}</nav>'
        + "".join(sections)
        + "</html>"
    )
    path = destination / "report.html"
    path.write_text(document, encoding="utf-8")
    write_index(directory)
    return path


def write_index(directory: Path) -> Path:
    """List finished machine previews without an approval queue."""
    rows: list[str] = []
    for report in sorted((directory / "runs").glob("*/report.html")):
        run_id = report.parent.name
        run = Run.model_validate_json((report.parent / "run.json").read_bytes())
        _suite, results, _fingerprint = load_candidate(directory, run_id)
        rows.append(
            f'<tr><td><a href="runs/{quote(run_id)}/report.html">{escape(run_id)}</a></td>'
            f"<td>{escape(run.source_run or '')}</td><td>{escape(run.config.provider)}</td>"
            f"<td>{'yes' if run.glossary_sha256 else 'off'}</td><td>{len(results)}</td>"
            f"<td>{sum(result.elapsed_seconds for result in results):.1f}s</td></tr>"
        )
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Machine translation previews</title><style>body{font:16px system-ui;margin:24px;background:#eef1f5;color:#202731}table{border-collapse:collapse;background:white}"
        "td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd}a{color:#174f8d}.table{overflow-x:auto}</style>"
        "<h1>Machine translation previews</h1><p>Machine output proceeds to the final human edit. Glossary lookup stays enabled; diagnostic notes are optional and never require approval.</p>"
        '<div class="table"><table><thead><tr><th>Preview</th><th>OCR input</th><th>Translator</th><th>Glossary</th><th>Pages</th><th>Translation time</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div><p>Preview generation does not replace existing published manuals.</p></html>"
    )
    path = directory / "index.html"
    path.write_text(document, encoding="utf-8")
    return path
