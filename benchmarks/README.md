# Translation quality experiments

Use this harness to compare OCR and translation candidates without changing
published manuals. It runs independently of Temporal. Frozen source scans,
settings, glossary bytes, model identities and hashes make results resumable.
Machine output is accepted automatically; reports and editing notes are optional
research tools, not production approval steps.

The current production pipeline is Document AI → verified glossary → Google
Translation LLM → formatting/source-label guards → one page-aware AI cleanup pass
per batch. Extra instruction-check/confirmation and crop OCR are disabled for new
production runs. See [retained findings](#retained-findings) for the evidence.

## Freeze source inputs

From the repository root:

```bash
uv sync --locked --all-groups
uv run python -m src.quality prepare
uv run python -m src.quality inspect
```

[`pilot.toml`](pilot.toml) selects ten representative source pages: warnings,
polarity, diagrams, furigana, tables and franchise terms. Source PDF filenames and
baseline manual directories must exist locally. PDFs are ignored by Git; a fresh
checkout needs the matching inputs before `prepare` can run.

The default suite is `benchmark-runs/pilot-v1/`, ignored by Git. Preparation
refuses to overwrite a suite, renders at 300 DPI with a 7,000-pixel edge cap and
freezes byte-for-byte baseline snapshots. Existing manual JSON is a historical
translation, not a verified Japanese reference. Keep the frozen suite intact;
create another suite for different source pages. Once frozen, inference/reporting
can use its scans without the original PDFs.

## Compare OCR and translation

Google uses the same `.env` project, processor, location and credentials as
production. `DOCAI_PROCESSOR_VERSION` optionally pins the benchmark processor
version. These commands upload scans and incur Google usage:

```bash
uv run python -m src.quality ocr --provider documentai --run-id documentai-ja-en
uv run python -m src.quality translate \
  --provider google-tllm --source-run documentai-ja-en --run-id docai-tllm
uv run python -m src.quality report --run-id docai-tllm
```

The report is `benchmark-runs/pilot-v1/runs/docai-tllm/report.html`. It shows the
machine English, scans, source regions and collapsed advisory evidence. It never
publishes to `manuals/` or replaces the final overlay editor.

Use the same OCR run for competing translators. Supported providers are
`google-tllm`, `google-nmt` and `ollama`. To isolate translation-model differences,
hold glossary/research and source regions fixed. For an unconstrained comparison:

```bash
uv run python -m src.quality translate \
  --provider google-tllm --source-run documentai-ja-en --run-id tllm-raw --no-glossary
uv run python -m src.quality translate \
  --provider google-nmt --source-run documentai-ja-en --run-id nmt-raw --no-glossary
```

`--no-glossary` also disables research. Normal runs use the shared cache;
`--no-research` keeps existing entries but prevents new discovery. `--glossary
PATH` selects another cache; each run freezes the exact bytes. See the
[glossary guide](../docs/glossary.md) for automatic additions and scope.

All inference commands accept repeated `--case CASE_ID` options for a small
trial. Successful pages resume; failed/interrupted pages retry. Use a new run ID
when inputs, model settings, upstream OCR or glossary settings change.

## Optional local models

YomiToku is a separate OCR environment, not an Ollama model. The tested local
setup used YomiToku 0.15.1:

```bash
uv venv --python .venv/bin/python .venv-quality
uv pip install --python .venv-quality/bin/python \
  yomitoku==0.15.1 pymupdf click python-dotenv
HF_HOME="$PWD/benchmark-runs/model-cache" OMP_NUM_THREADS=4 \
  .venv-quality/bin/python -m src.quality ocr \
  --provider yomitoku --device cpu --run-id yomitoku-cpu
```

The first run downloads weights. Keep upstream code/model licenses and attribution;
see [YomiToku](https://github.com/kotaro-kinoshita/yomitoku). Local OCR may need
Hugging Face metadata access even when weights are cached. Compare OCR outputs
using the same translator; changed segmentation is part of an OCR candidate.

Ollama runs TranslateGemma locally:

```bash
ollama pull translategemma:12b
uv run python -m src.quality translate \
  --provider ollama --model translategemma:12b \
  --source-run documentai-ja-en --run-id gemma12b-raw --no-glossary
```

The adapter uses a TranslateGemma-specific prompt, bounded context, deterministic
sampling, completion checks and the installed model digest. Compatible Hugging
Face GGUF models can use an installed `hf.co/<owner>/<repository>:<quantization>`
name with `--model`. This does not support arbitrary Hugging Face architectures;
other translation families need a separately evaluated prompt.

For WSL/RTX 3090 trials, copy the frozen suite (not credentials), configure a
GPU-enabled runtime, verify actual GPU use and choose a new run ID. A 27B trial
has not been validated here. Use `--ollama-url` for a separate runtime host;
hardware/quantization differences do not establish equivalent quality.

## Automated cleanup comparison

Create a baseline translation first, then compare cleanup on its identical scans,
OCR regions, draft and frozen glossary:

```bash
uv run python -m src.quality compare \
  --baseline-run docai-tllm --comparison-id cleanup-trial-v1
```

This does not rerun Google OCR/translation or measure changes against the
human-edited published manual. The current cleanup candidate is generated in an
isolated directory. Two separate AI assessments reverse A/B order, check source
quotes and classify substantive fixes, introduced errors, unresolved findings
and style. Only agreed substantive direction counts as a fix/regression.
Related-box instructions retain ordered source identities. Unsupported quotes,
disagreement and incomplete coverage remain explicit uncertainty.

Outputs under `benchmark-runs/pilot-v1/comparisons/<id>/` include `report.html`,
`report.md`, `comparison.json`, per-page candidates/assessments and request usage.
`--workers 1..4` controls concurrent pages; `--cleanup-model` and `--judge-model`
select models. Defaults use the same model family, which limits independence.
Changed inputs/settings or generation code need a new comparison ID.

Rebuild a report from saved evidence without model calls:

```bash
uv run python -m src.quality compare \
  --baseline-run docai-tllm --comparison-id cleanup-trial-v1 --render-only
```

Reports separate generation, evaluation and concurrent wall time. Costs use
returned token usage and dated rates; unknown usage is not priced as zero.
Historical Google/research billing and requests without returned usage are
excluded. These are estimates, not invoices. AI agreement is not a reference
translation, an accuracy percentage or evidence of zero errors.

Serve only the chosen suite's directory for report viewing, rather than the
repository root:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory benchmark-runs/pilot-v1
```

## Retained findings

These are dated development results, not a model leaderboard. Machine artifacts
and full local reports are intentionally ignored by Git and will not exist in a
fresh clone. The [engineering journal](../blog.md) preserves measurements,
failures and recovery details.

| Experiment                                                | Evidence                                                                                                                       | Decision                                                                                            |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| Initial dedicated-model pilot                             | Exposed names such as キバ → Fang and operating terms such as レバー → Liver; candidates differed in glossary/cleanup settings | Do not infer a model winner from mismatched pipelines                                               |
| Contextual cleanup, ten pages                             | 15 paired AI-agreed substantive fixes, zero agreed regressions, 80 uncertain/disputed groups                                   | Retain source-backed terminology and page-aware cleanup; zero agreed regressions is not zero errors |
| Reconstructed-paragraph trial                             | Zero agreed substantive fixes; segmentation uncertainty remained                                                               | Do not promote the rewrite                                                                          |
| Full Fang instruction-check trial, 13 pages / 450 regions | Zero agreed fixes, zero agreed regressions, 37 uncertain/disputed groups; summed page work 529.37s → 999.91s                   | Restore the efficient single-pass cleanup path                                                      |

The full Fang report is saved locally at
`benchmark-runs/fang-full-comparison-v1/comparisons/whole-instruction-v1/report.html`.
It compares independently regenerated initial cleanup versions, so ordinary model
variation is a confound. The manual contains development examples. Assessment
added 1,405.47s summed work; known model estimates totaled $0.5992568, excluding
Google and nine unmetered rejected requests. These dated findings do not justify
replacing all edited translations.

Experimental instruction checking groups nearby fragments, requires source-linked
verdicts and allows at most one repair confirmation. Optional independent crop
OCR can veto conflicting edits. The offline experiment tools remain available;
old workflow-history compatibility is no longer maintained. Setting
`INSTRUCTION_CHECK_MODEL` does not enable this stage in the current pipeline.
Official PDF glossary evidence remains enabled independently.

For future quality work, freeze a new representative/held-out source selection,
hold unrelated stages constant and compare source-supported substantive changes.
Promote a candidate only when improvement is demonstrated; a lower advisory-flag
count or more English changes is not an accuracy measurement. Final optional
corrections belong directly in the overlay editor.

## Rendered-layout trial

The isolated Geartlinger experiment measures the actual viewer at desktop
1280×900, phone 390×844 and its A4 print representation. It sends rendered pages,
source scans and browser measurements to the configured OpenAI model, allows at
most two proposal rounds per page, and retains only bounded box/whitespace edits
that preserve words, source coverage, neighboring regions and near-white space.
A measured cross-view improvement and a separate visual check are required.
The AI-coordinate editor remains a development tool. The production pipeline
now reuses its read-only browser measurement helper after cleanup, with advisory
metrics and automatic publication. No model-coordinate edit or approval gate is
added; see the [publication lifecycle](../docs/development.md#staging-audit-and-publication).

With Google Chrome installed locally:

```bash
uv run python -m src.quality.layout_trial \
  --output benchmark-runs/geartlinger-layout-v2 --measure-only
# Omit --measure-only to run the billable model trial.
```

The tool is currently scoped to `manuals/Geartlinger/translations.json`. Use a new
output directory when changing inputs, renderer, model or experiment code. It
copies public viewer/manual assets and never writes into `manuals/`. Returned
model responses and usage are saved before validation so a parser failure does
not silently repeat a completed paid request. API failures without returned usage
remain unmetered. Reports include before/after screenshots and ordinary overlay
viewer copies; source scans/JSON and frozen code/evidence stay in the ignored run.

The 2026-11-04 trial processed 16 pages / 1,775 regions in 223.18s, with 32 model
calls and $0.2191391 estimated model usage. Of 193 proposals, three passed the
geometry/word/ink guards, two improved measurements, and one passed the visual
check. Many proposals overlapped neighbors or scan ink; twelve attempted wording
changes. Same-family visual judgment also vetoed a print-improving edit partly
because baseline problems remained, so its rationale is not evidence of a new
regression.

More importantly, full-viewer verification exposed a renderer bug: integer
`scrollWidth`/`scrollHeight` measurements were compared to fractional box sizes,
causing false overflow and minimum-font fallback. Fractional text/line bounds,
reader-border accounting and shared print measurement fixed that independently
of AI/JSON edits. A separate paired run on unchanged text/boxes measured desktop
DOM overflow falling from 765 to 475, with zero new overflowing regions; 307
regions grew by at least 0.5px and 309 shrank to fit. Mobile DOM overflow remained
1,773 and A4 remained 1,740. These are layout heuristics, not translation-error
counts or native Firefox print certification.

After correcting the renderer, the AI candidate's one retained box expansion
provided no additional measured gain. Kept the renderer correction and left the
published manual JSON unchanged. The model-coordinate pass is not justified as
a default pipeline stage by this trial. Remaining narrow-screen/print problems
need separate layout work.

Local artifact: `benchmark-runs/geartlinger-layout-v1/public/renderer-fit.html`.
Serve only that `public/` directory to inspect the complete comparison; the
original AI-only report remains beside it, with its original raw evidence.
