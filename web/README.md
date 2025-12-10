# Translated Manuals

This directory contains translated toy instruction manuals, served as a static site via GitHub Pages.

## Structure

```
output/
├── index.html              # Main index listing all manuals
├── {ManualName}/
│   ├── index.html         # Interactive viewer for this manual
│   ├── translations.json  # Translation data (editable)
│   ├── translated.pdf     # PDF with English overlays
│   └── pages/
│       ├── page-0.webp   # Page images
│       └── ...
```

## Viewing Locally

```bash
# From project root
uv run python server.py
# Open http://localhost:5000
```

## GitHub Pages

This folder is served as a static site. The main index automatically updates when new translations are added.

## Editing Translations

To fix or adjust translations:

1. Edit `{ManualName}/translations.json`
2. Commit changes
3. GitHub Pages will automatically update

The translation JSON structure:
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
      "original": "日本語",
      "translated": "English",
      "bounds": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}
    }]
  }]
}
```
