# Viewer and manual data

The static site provides the catalog, a continuous reader and an explicit editing
workspace. It does not run translation models in the browser or require a server
backend for reading and exporting edits.

## Reading and editing

Search by manual name/source and combine catalog tag filters. Deep links use the
manual folder name in the URL fragment. Reader controls change corrected-text
size, show/hide English, navigate pages and prepare the complete manual for print.

Desktop and sufficiently capable tablet layouts expose **Edit translations**.
Phones/coarse-pointer layouts remain reading-only. Editing supports text changes,
adding/deleting blocks, dragging/resizing the selected box, numeric box fields,
undo/redo and versioned local-draft recovery. A local draft belongs to its browser
and origin; it is not a repository save or a synchronized backup.

Download JSON exports the edited manual. The GitHub action copies that same JSON
and opens `manuals/<name>/translations.json` in GitHub's file editor. Paste the
payload there and follow GitHub's commit/pull-request flow. There is no automatic
repository write from editing the overlay.

## Persisted contract

Each manual directory contains `translations.json` and its referenced page images.
The shared catalog is `web/meta.json`, regenerated with `src.cli reindex`.

```json
{
  "meta": {
    "manual_name": "Example manual",
    "source": "Example.pdf",
    "source_lang": "ja",
    "target_lang": "en"
  },
  "pages": [
    {
      "image": "pages/page-0.webp",
      "blocks": [
        {
          "text": "取扱説明書",
          "translation": "Instruction manual",
          "bbox": [0.1, 0.2, 0.5, 0.1]
        }
      ]
    }
  ]
}
```

`bbox` is normalized `[x, y, width, height]`, relative to the scan. Metadata may
also contain source/blog URLs, tags, counts and thumbnail information. Export
preserves metadata while keeping only persisted page/block fields: `image`,
`blocks`, `text`, `translation`, `bbox`, plus optional semantic fields. A display
block with `layout: "panel"` uses an opaque paragraph mask in both reader and
print. Grouped pages retain `source_blocks` (all original regions) and
`layout_provenance` (source-ID membership, guide-parent links, OCR line geometry
and uncertainty notes). Export preserves these fields through final human edits.
Readable original artwork can have a ledger entry with `display_index: -1`
and disposition `native graphic retained without overlay`. This removes only
the redundant English box: polarity signs, arrows, readable English and other
confirmed artwork stay visible in the original scan. Battery instructions and
specifications still receive translation overlays. Export preserves both the
suppression ledger and the complete source regions.
These are source records, not an instruction to the browser or a review gate.
Draft versions, fit measurements, undo
history and UI state must never enter saved manual JSON.

Pure projection/validation lives in `web/js/manual-data.js`; editor state and
history in `edit-session.js`; drafts in `draft-store.js`; proportional text fitting
in `text-fit.js` and `text-fit-dom.js`. Catalog model/view and viewer state are
separate modules. `tests/web/` covers these contracts and combined acceptance flows.

## Printing

Print preparation loads all source pages, waits for images/fonts, derives an A4
layout and warns about missing translations, overflow and text below the 9pt
print floor. The preflight is advisory; it is not a translation approval step.

In Firefox's native dialog select A4, one page per sheet, backgrounds enabled
and headers/footers disabled. Check orientation and scaling against the preflight.
The webpage cannot enforce all browser-dialog settings.

**Outstanding validation:** native Firefox preview has not been verified across
the complete representative matrix. Check a short portrait manual, a long one,
a landscape page, dense warnings and a forced-overflow case. Confirm one source
page per sheet, complete overlays, sharp readable text, no blank lazy pages, no
cropping and no site/browser chrome. Automated preparation tests do not replace
this browser-specific visual check.
