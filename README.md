# TokuSolutions: PDF Translation with Temporal

![Kamen Rider Belt](images/kamen-rider-belt.jpg)

I buy a lot of Kamen Rider toys. The problem? All the manuals are in Japanese, and these toys have *a lot* of features.

Google Translate on your phone works for simple text, but PDF translation is terrible—it loses layout context, mangles technical terms, and produces nonsense for complex instructions. This project attempts to do the best possible automated PDF translation while providing an interface for humans to fix the inevitable mistakes AI makes.

**The approach**: Use Google Document AI for OCR with layout detection, batch translate via Google Translate API, clean up with Gemini AI using product context, then present everything in an editable web interface. All orchestrated by Temporal workflows for reliable, parallel execution.

## How Temporal Orchestrates Translation

The [workflow](src/workflow.py) coordinates six activities:

1. **[search_product_url_activity](src/activities/product_search.py)** - Find product metadata on Tokullectibles
2. **[get_pdf_page_count_activity](src/activities/ocr.py)** - Extract page count from PDF
3. **[ocr_page_activity](src/activities/ocr.py)** - OCR a single page with Document AI (runs in parallel for all pages)
4. **[translate_blocks_activity](src/activities/translation.py)** - Batch translate all text blocks
5. **[generate_site_activity](src/activities/site_generation.py)** - Convert pages to WebP and generate viewer HTML
6. **Cleanup activities** ([src/activities/cleanup.py](src/activities/cleanup.py)) - Three-stage cleanup: ftfy → rules → Gemini AI

**Why Temporal?**
- **Fan-out/fan-in**: Pages 0-19 all OCR in parallel, results merge when done
- **Automatic retries**: API rate limits and transient failures handled automatically
- **Multi-document processing**: Run multiple workers to process different manuals simultaneouslyC
- **Durable execution**: 50-page documents complete reliably without custom state management

The timeline view shows parallel OCR execution:

![Workflow Detail](images/temporal-workflow-detail.png)

## Prerequisites

- Python 3.12+, [uv](https://docs.astral.sh/uv/), [Temporal CLI](https://docs.temporal.io/cli)
- Google Cloud Project with Document AI and Translation API enabled
- Service account credentials JSON
- (Optional) Gemini API key for AI cleanup

## Quick Start

```bash
# Install
uv sync

# Configure
cp .env.example .env
# Edit .env with your credentials

# Start Temporal (Terminal 1)
temporal server start-dev

# Translate (Terminal 2)
uv run python -m src.cli translate path/to/manual.pdf

# View results
python -m http.server 8000  # Open http://localhost:8000/
```

Required `.env`:
```bash
ProjectID=your-gcp-project-id
ProcessorID=your-documentai-processor-id
Location=us
CREDENTIALS_PATH=credentials/service-account.json
GEMINI_API_KEY=your-gemini-api-key  # Optional
```

## Web Viewer

![Web Viewer](images/websiteScreenshot.png)

The viewer provides overlays for editing translations. Click any text block to fix machine translation errors.

## CLI Commands

![CLI Example](images/cli-example.svg)

```bash
# Translate with multiple workers (process multiple PDFs simultaneously)
uv run python -m src.cli translate manual.pdf -w 5

# Skip AI cleanup
uv run python -m src.cli translate manual.pdf --skip-cleanup

# List manuals
uv run python -m src.cli list

# Add product URL
uv run python -m src.cli add-url "ManualName" "https://..."

# Regenerate index
uv run python -m src.cli reindex
```

## Temporal Best Practices

The workflow implements several Temporal best practices for reliable, scalable execution:

### 1. Retry Policies
Three retry strategies for different operation types:
- **QUICK_RETRY**: Fast operations (file I/O, simple processing) - 3 attempts, 1-10s backoff
- **API_RETRY**: External API calls (Document AI, Translation API) - 5 attempts, 2-30s backoff with exponential growth
- **LLM_RETRY**: AI model calls (Gemini cleanup) - 3 attempts, 5s-2min backoff

### 2. Activity Separation
Cleanup split into three distinct activities for proper retry and determinism:
- **ftfy_cleanup_activity** - Deterministic Unicode/encoding fixes
- **rule_based_cleanup_activity** - Deterministic pattern-based removal
- **gemini_cleanup_activity** - Non-deterministic AI corrections with Pydantic validation

### 3. Heartbeats
Long-running activities send heartbeats to prevent timeouts:
- Site generation: Heartbeat every 5 pages during image rendering
- Gemini cleanup: Heartbeats before/after LLM API calls (2min timeout)

### 4. Workflow Determinism
Non-deterministic operations moved outside workflows:
- `Path` operations (stem, name extraction) moved to CLI layer
- Manual name and output directory computed before workflow starts
- Only deterministic data transformations in workflow code

## Type-Safe AI with Pydantic + Temporal

LLM responses are unpredictable. Combining [Pydantic](https://docs.pydantic.dev) validation with Temporal's retry logic ensures the AI cleanup is both safe and reliable.

**Three-stage cleanup pipeline:**
1. **ftfy** - Fix Unicode encoding and OCR corruption
2. **Rule-based** - Remove page numbers, symbols, artifacts
3. **Gemini AI** - Context-aware corrections with product metadata

**Pydantic validation** ([cleanup.py](src/cleanup.py#L13-L26)) enforces the response schema:
```python
class GeminiCleanupResponse(BaseModel):
    remove: list[str]           # Block indices to remove
    corrections: dict[str, str] # Index → corrected text
    product_name: str           # Official product name
```

**How it works:**
- Gemini returns JSON → Pydantic validates structure and types
- Invalid responses trigger Temporal activity retry (with exponential backoff)
- Activities can't return malformed data to the workflow
- Type safety across the entire pipeline from OCR → AI → storage

This pattern prevents corrupt data from reaching `translations.json` while leveraging Temporal's built-in retry logic for transient failures.

## Monitoring

![Temporal Workflow](images/temporal-workflows.png)

Temporal Web UI at http://localhost:8233 shows workflow execution, retry attempts, and parallel tasks.

## Performance

Typical 20-page manual: ~50 seconds with single worker
- 30s OCR (parallel across pages)
- 5s translation (batch)
- 10s AI cleanup
- 5s site generation

Run multiple workers (`-w 5`) to process multiple documents simultaneously.

## License

MIT
