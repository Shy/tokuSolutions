# Toy Manual Translation System

OCR and translation system for Japanese toy instruction manuals using Google Cloud Document AI, Translation API, and Temporal workflows.

## What This Does

- Extracts text blocks with bounding boxes from PDF manuals using Document AI
- Translates Japanese text to English using Google Translate API
- Generates HTML viewer with original and translated text overlays
- Produces translated PDF output

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Temporal server](https://docs.temporal.io/) running locally (default: localhost:7233)
- Google Cloud Project with Document AI and Translation API enabled
- Service account credentials JSON file

## Quick Start

1. **Install dependencies**
   ```bash
   uv sync
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your Google Cloud project details
   ```

3. **Update credentials path** in `docai_activities.py:26`
   ```python
   CREDENTIALS_PATH = "/path/to/your/service-account-key.json"
   ```

4. **Start Temporal server** (in separate terminal)
   ```bash
   temporal server start-dev
   ```

5. **Translate a PDF** (workers start automatically)
   ```bash
   uv run python tokuSolutions.py translate Documents/YourManual.pdf
   ```

6. **View results**
   ```bash
   uv run python tokuSolutions.py serve
   # Open http://localhost:5000
   ```

## CLI Reference

All commands use the unified `tokuSolutions.py` CLI:

```bash
# Translate a PDF (workers start/stop automatically)
uv run python tokuSolutions.py translate Documents/Manual.pdf
uv run python tokuSolutions.py translate Documents/Manual.pdf -w 5  # Use 5 workers

# Start web viewer
uv run python tokuSolutions.py serve              # Default: http://127.0.0.1:5000
uv run python tokuSolutions.py serve -p 8080      # Custom port

# List translated manuals
uv run python tokuSolutions.py list

# Add source URL to a manual
uv run python tokuSolutions.py add-url "CSM-Fang-Memory" "https://toy.bandai.co.jp/..."

# Regenerate main index
uv run python tokuSolutions.py reindex

# Manual worker management (optional)
uv run python tokuSolutions.py worker -c 3        # Keep workers running

# Get help
uv run python tokuSolutions.py --help
uv run python tokuSolutions.py translate --help
```

## Project Structure

### Core Files
- **[tokuSolutions.py](tokuSolutions.py)** - Unified CLI tool (use this!)
- [docai_activities.py](docai_activities.py) - Temporal activities for OCR, translation, and output generation
- [docai_workflow.py](docai_workflow.py) - Temporal workflow orchestrating the translation pipeline
- [worker.py](worker.py) - Temporal worker (called by tokuSolutions.py)
- [server.py](server.py) - Flask server (called by tokuSolutions.py)

### Configuration
- `.env` - Environment variables (not in git)
- `.env.example` - Template for required environment variables
- `pyproject.toml` - Python dependencies

### Directories
- `Documents/` - Input PDF files (not in git)
- `output/` - Translation results (committed for GitHub Pages)
  - `output/{PDF_NAME}/translations.json` - Structured translation data
  - `output/{PDF_NAME}/index.html` - Interactive viewer
  - `output/{PDF_NAME}/pages/` - Rendered page images (WebP)

## Architecture

### Document AI Block Detection
- Uses Google Cloud Document AI OCR with native `blocks` detection
- Returns text blocks with normalized coordinates (0-1)
- No custom merging - Document AI's block segmentation used as-is
- Human intervention required for fine-tuning block boundaries

### Translation Pipeline
1. **Page Count** - Get total pages from PDF
2. **OCR** - Process each page in parallel using Document AI
3. **Translation** - Batch translate text blocks with Google Translate API
4. **Output** - Generate JSON, HTML viewer, and translated PDF

### Temporal Workflow
- Parallel page processing for speed
- Activity retries for API resilience
- Workflow history for debugging

## Output Format

### translations.json
```json
{
  "meta": {
    "source": "Manual.pdf",
    "source_lang": "ja",
    "target_lang": "en"
  },
  "pages": [{
    "image": "pages/page-0.webp",
    "blocks": [{
      "original": "�,�ƭ��",
      "translated": "English text",
      "bounds": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}
    }]
  }]
}
```

## GitHub Pages Deployment

The `output/` folder is served as a static site via GitHub Pages.

### Setup
1. Go to repository Settings → Pages
2. Set Source to "Deploy from a branch"
3. Select branch: `main`, folder: `/output`
4. Save

### Main Index Features
The index at `output/index.html` includes:
- **Search**: Real-time filtering by manual name
- **Thumbnails**: First page preview for each manual
- **Stats**: Page and block counts
- **Source Links**: Links to original Bandai manuals (when added)
- Auto-generated after each translation

### Adding Source URLs
Link manuals to their original source on toy.bandai.co.jp:

```bash
uv run python tokuSolutions.py add-url "CSM-Fang-Memory" "https://toy.bandai.co.jp/manuals/pdf.php?id=2588450"
```

This updates the manual's metadata and regenerates the main index.

### Manual Structure
Each manual gets its own subdirectory with clean URL-friendly names:
- `output/{ManualName}/index.html` - Interactive viewer with back button and source link
- `output/{ManualName}/translations.json` - Editable translation data (includes source_url)
- `output/{ManualName}/pages/` - Page images (WebP format)

## Notes

- Document AI blocks are used as-is without custom merging
- Manual adjustment of `translations.json` may be needed for optimal layout
- Source PDFs and credentials are git-ignored
- Translated outputs (`output/`) are committed for GitHub Pages
