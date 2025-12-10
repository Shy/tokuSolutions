# Toy Manual Translation System

OCR and translation system for Japanese toy instruction manuals using Google Cloud Document AI, Translation API, and Temporal workflows.

## What This Does

- Extracts text blocks with bounding boxes from PDF manuals using Document AI
- Translates Japanese text to English using Google Translate API
- Generates interactive web viewer with side-by-side original and translated text
- Creates static site with searchable index (GitHub Pages ready)

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

3. **Add credentials**
   ```bash
   # Place your service-account.json in credentials/
   # Update .env with: CREDENTIALS_PATH=credentials/service-account.json
   ```

4. **Start Temporal server** (in separate terminal)
   ```bash
   temporal server start-dev
   ```

5. **Translate a PDF** (workers start automatically)
   ```bash
   uv run python -m src.cli translate Documents/YourManual.pdf
   ```

6. **View results**
   - Serve the site: `python -m http.server 8000`
   - Open http://localhost:8000/web/ in your browser

## CLI Reference

All commands use the unified CLI:

```bash
# Translate a PDF (workers start/stop automatically)
uv run python -m src.cli translate Documents/Manual.pdf
uv run python -m src.cli translate Documents/Manual.pdf -w 5  # Use 5 workers

# List translated manuals
uv run python -m src.cli list

# Add source URL to a manual
uv run python -m src.cli add-url "CSM-Fang-Memory" "https://toy.bandai.co.jp/..."

# Regenerate main index
uv run python -m src.cli reindex

# Get help
uv run python -m src.cli --help
uv run python -m src.cli translate --help
```

## Project Structure

```
tokuSolutions/
├── src/                      # Python translation pipeline
│   ├── cli.py               # Command-line interface
│   ├── workflow.py          # Temporal workflow
│   ├── activities.py        # Document AI & translation activities
│   ├── worker.py            # Temporal worker
│   └── html_template.py     # HTML generation
│
├── web/                      # Static web viewer
│   ├── index.html           # Main page
│   ├── app.js               # Application entry point
│   ├── styles.css           # Styles
│   ├── js/                  # ES6 modules
│   │   ├── config.js
│   │   ├── state.js
│   │   ├── renderer.js
│   │   ├── editor.js
│   │   ├── navigation.js
│   │   ├── github.js
│   │   └── ...
│   ├── meta.json            # Generated manual index
│   └── tags.json            # Tag definitions
│
├── manuals/                  # Generated manual data
│   ├── CSM-Den-O-Belt-v2/
│   │   ├── translations.json
│   │   └── pages/
│   └── ...
│
├── docs/                     # Documentation
│   ├── PERFORMANCE_IMPROVEMENTS.md
│   ├── MODULE_ARCHITECTURE.md
│   └── GITHUB_INTEGRATION.md
│
├── credentials/              # Credentials (git-ignored)
│   └── README.md
│
├── .github/workflows/
│   └── static.yml           # GitHub Pages deployment
│
├── pyproject.toml
├── .env.example
└── README.md
```

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
4. **Output** - Generate JSON, web viewer, and page images

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
    "target_lang": "en",
    "pages": 10,
    "blocks": 45,
    "thumbnail": "pages/page-0.webp"
  },
  "pages": [{
    "image": "pages/page-0.webp",
    "blocks": [{
      "text": "原文テキスト",
      "translation": "English text",
      "bbox": [0.1, 0.2, 0.3, 0.05]
    }]
  }]
}
```

## GitHub Pages Deployment

The site is served as a static site via GitHub Pages.

### Setup
1. Go to repository Settings → Pages
2. Set Source to "Deploy from a branch"
3. Select branch: `main`, folder: `/` (root)
4. Save

### Main Index Features
The index at `/web/index.html` includes:
- **Search**: Real-time filtering by manual name
- **Thumbnails**: First page preview for each manual
- **Stats**: Page and block counts
- **Source Links**: Links to original Bandai manuals (when added)
- **Tags**: Manual categorization and filtering
- Auto-generated after each translation

### Adding Source URLs
Link manuals to their original source on toy.bandai.co.jp:

```bash
uv run python -m src.cli add-url "CSM-Fang-Memory" "https://toy.bandai.co.jp/manuals/pdf.php?id=2588450"
```

This updates the manual's metadata and regenerates the main index.

### Manual Structure
Each manual gets its own subdirectory with clean URL-friendly names:
- `manuals/{ManualName}/translations.json` - Editable translation data
- `manuals/{ManualName}/pages/` - Page images (WebP format)

## Development

### Web App
The web viewer is a modular SPA with:
- ES6 modules for clean code organization
- Real-time search and filtering
- Inline editing with GitHub PR submission
- Lazy image loading for performance

### Testing
Unit tests coming soon - see tracked issue for web app testing setup.

## Notes

- Document AI blocks are used as-is without custom merging
- Manual adjustment of `translations.json` may be needed for optimal layout
- Source PDFs and credentials are git-ignored
- Translated outputs (`manuals/`) are committed for GitHub Pages
- Web app code (`web/`) is maintained separately from manual data
