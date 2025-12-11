# Translation Cleanup Setup Guide

The three-stage hybrid cleanup pipeline has been implemented and integrated into the translation workflow.

## What It Does

The cleanup system runs automatically after translation and performs three stages:

1. **Stage 1: ftfy** - Fixes encoding and OCR text issues (broken Unicode, mojibake, etc.)
2. **Stage 2: Rule-based** - Removes noise like page numbers `(1)`, lone punctuation, copyright symbols, manufacturer names
3. **Stage 3: Gemini (optional)** - LLM-based intelligent corrections for OCR errors and awkward phrasing

## Setup Instructions

### 1. Install New Dependencies

```bash
uv pip install ftfy google-generativeai
```

### 2. Set Up Google AI Studio API Key

The cleanup uses Google AI Studio (Gemini Developer API) for Stage 3.

Add your API key to `.env`:

```bash
GEMINI_API_KEY=your_api_key_here
```

You can get a free API key from [Google AI Studio](https://ai.google.dev/).

**Note:** If the API key isn't configured, cleanup will still work but Stage 3 will be skipped with a warning.

## Usage

### Default Behavior (All Stages)

By default, all three cleanup stages run automatically:

```bash
toku translate manual.pdf
# or
toku translate https://example.com/manual.pdf
```

### Skip Cleanup Entirely

Use `--skip-cleanup` to disable all cleanup stages:

```bash
toku translate manual.pdf --skip-cleanup
```

## Cost Estimate

Stage 3 (Gemini) uses `gemini-2.5-flash` which is free with generous rate limits:

- **Free tier:** 15 requests per minute, 1 million tokens per minute
- **Estimated cost per manual:** $0 (within free tier limits)

Stages 1 and 2 are completely free (local processing).

## What Gets Cleaned Up

### Removed Automatically:
- Page numbers: `(1)`, `(2)`, etc.
- Lone punctuation: `.`, `!`, `?`
- Single characters/digits
- Copyright symbols alone: `©`, `®`, `™`
- Manufacturer names: `BANDAI`
- Whitespace-only blocks

### Corrected Automatically:
- Broken Unicode (ftfy)
- OCR spacing errors: `b a t t e r y` → `battery`
- Awkward phrasing (Gemini)
- Product name consistency (Gemini)

### Always Kept:
- Actual instructions
- Warnings
- Feature descriptions
- Part names
- Assembly steps

## Output

The CLI will show cleanup statistics:

```
Step 6: Cleaning up translations...
  Stage 1 (ftfy): 3 fixes
  Stage 2 (rules): 12 removals
  Stage 3 (Gemini): 8 corrections
Cleanup complete: 145 → 128 blocks
```

## Files Modified

- [src/cleanup.py](src/cleanup.py) - Three-stage cleanup implementation
- [src/activities.py](src/activities.py:1603) - `cleanup_translations_activity`
- [src/workflow.py](src/workflow.py:236) - Step 6 integration
- [src/worker.py](src/worker.py:38) - Activity registration
- [src/cli.py](src/cli.py:38) - `--skip-cleanup` flag
- [pyproject.toml](pyproject.toml:21) - New dependencies added

## Notes

- Cleanup modifies `translations.json` in-place after site generation
- Stage 3 (Gemini) errors are non-fatal - workflow continues with a warning
- Cleanup can be re-run manually by calling the activity directly if needed
- Product name corrections are applied if Gemini finds a better match from the Tokullectibles page
