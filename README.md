# TokuSolutions

Translate Japanese toy manuals into English while keeping the scanned pages and
text positions. Temporal coordinates Google Document AI OCR, source-backed toku
terminology research, Google Translation LLM and image-aware OpenAI cleanup.
The result opens directly in the reading and editing webpage.

The machine pipeline is automatic. Uncertain terminology becomes an advisory
note; there is no human approval gate or mandatory preview. Optional final edits
happen on the page that overlays English on the scans.

## Read or edit a manual

The published catalog is at [toku.solutions](https://toku.solutions/).
Search by name, combine series filters, open a manual and adjust the reader's
text size or hide the English overlay. On a capable desktop or tablet, choose
**Edit translations** to change text and boxes, add/delete blocks, undo/redo and
recover local drafts. Phones provide reading controls only.

Download JSON to save edits, or use the GitHub action to copy the JSON and open
the file editor for a pull request. Browser drafts are local to that browser;
changes do not automatically update the repository.

**Print Manual** prepares the complete manual for A4 and reports text-fit issues.
Firefox's native print-preview matrix is still unverified; see the
[viewer guide](docs/viewer.md#printing).

## Translate a PDF

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), the
[Temporal CLI](https://docs.temporal.io/cli), and a Google Cloud project with
Document AI and Cloud Translation enabled. Configure a Document AI OCR processor
and a service account able to use both APIs. Install Google Chrome for the automatic
browser layout audit. OpenAI API access enables automated
glossary web research and AI cleanup; a missing key or failed optional AI stage
retains the translated draft.

```bash
uv sync --locked --all-groups
cp .env.example .env
```

Edit `.env` using the variables in [.env.example](.env.example). Put your Google
key in `credentials/` and set `CREDENTIALS_PATH`; see the
[credential setup](credentials/README.md). Google OCR uploads the scan, and
image-aware AI cleanup sends page images to OpenAI. These are paid API services.

Start Temporal in one terminal:

```bash
temporal server start-dev
```

From the repository root in another terminal:

```bash
uv run python -m src.cli translate path/to/manual.pdf
```

The CLI starts three worker processes, tracks progress and stops its workers
when finished. Output is `manuals/<PDF filename without extension>/`, containing
`translations.json` and `pages/*.webp`. It also refreshes `web/meta.json`.
New translations stay in run-local staging until cleanup, source validation and
the browser audit finish. Replacements retain the previous folder as a backup;
an edit made to the published JSON during translation prevents replacement.
The CLI prints the quality status, warnings, audit path and any backup path.

Useful commands:

```bash
uv run python -m src.cli list
uv run python -m src.cli translate manual.pdf --workers 5
uv run python -m src.cli translate manual.pdf --skip-cleanup
uv run python -m src.cli add-url "ManualName" "https://example.com/original.pdf"
uv run python -m src.cli reindex
```

`--skip-cleanup` skips all three cleanup stages, including formatting and source
label restoration. More workers help only while ready Activities are queued;
they do not parallelize the sequential cleanup batches within one manual.

## Preview the website locally

Serve a public staging directory instead of exposing `.env`, credentials and
research artifacts from the repository root. From the repository root:

```bash
uv run python -m src.cli reindex
mkdir -p _site
cp -R web/. _site/
cp -R manuals _site/
python3 -m http.server 8767 --bind 127.0.0.1 --directory _site
```

Open [http://127.0.0.1:8767/](http://127.0.0.1:8767/).
Use an empty `_site` when rebuilding after removing manuals, so old files are
not left in the preview. Refresh the staged copy after generating or editing data.

## How translation works

1. **OCR:** Document AI reads each page with Japanese/English language hints.
   All nonempty source regions, including labels and symbols, are retained.
2. **Terminology and translation:** bounded deterministic lookup and an optional
   web-search agent verify names, freeze the applicable glossary, then translate
   with Google Translation LLM. Unverified names never block generation.
3. **Staging:** render WebP scans and save the normal manual JSON outside the
   published catalog, with a source snapshot for validation.
4. **Cleanup:** normalize English formatting, restore literal source labels and
   run one page-aware AI cleanup call per batch. Guards preserve the original
   Japanese, boxes, accepted names and literal labels.
5. **Audit:** validate every page, translation and image, then measure the real
   Chrome viewer at desktop, phone and A4 print sizes. Overflow, small text and
   unavailable browser checks become advisory warnings.
6. **Publish:** move the completed manual into place, retain any previous version
   and refresh the catalog. Incomplete OCR or changed source data prevents
   publication. There is no approval gate or extra preview page.

Production agents use the official
[Temporal OpenAI Agents integration](https://docs.temporal.io/develop/python/integrations/openai-agents).
Model calls are retryable Activities; replay reuses recorded results. Workers
must share the local `benchmark-runs/agent-pipeline/` artifacts for in-flight
runs. Staging, audits and replacement backups live under
`benchmark-runs/publication/`. The [developer guide](docs/development.md) explains orchestration,
operations and validation.

Extra whole-instruction checking and crop OCR were tested and then disabled for
new runs: the full Fang Memory comparison did not demonstrate a substantive
quality gain and added runtime. Old workflow histories are not a compatibility
target; new runs use the `manual-translation` task queue. See the [quality experiments and findings](benchmarks/README.md).

## Documentation

- [Development and GitHub deployment](docs/development.md): tests, workers,
  dependencies, deployment and known limitations.
- [Shared glossary](docs/glossary.md): automatic discovery, evidence, scope and
  maintenance.
- [Viewer and manual format](docs/viewer.md): editing, export, printing and the
  persisted JSON contract.
- [Quality experiments](benchmarks/README.md): dedicated OCR/translation models,
  controlled comparisons and retained findings.
- [Engineering journal](blog.md): dated measurements and decision history;
  earlier entries can describe superseded behavior.

The application code is MIT-licensed; see [LICENSE](LICENSE). Manual scans,
fonts and optional model weights retain their respective upstream terms.
