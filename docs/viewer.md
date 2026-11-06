# Viewer and manual data

The static site provides the catalog, a continuous reader and an explicit editing
workspace. It does not run translation models in the browser or require a server
backend for reading and exporting edits.

## Reading and editing

Search by manual name/source and combine catalog tag filters. Deep links use the
manual folder name in the URL fragment. Reader controls change corrected-text
size, show/hide English, navigate pages and download the complete manual as a PDF.

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
PDF. Grouped pages retain `source_blocks` (all original regions) and
`layout_provenance` (source-ID membership, guide-parent links, OCR line geometry
and uncertainty notes). Export preserves these fields through final human edits.
Readable original artwork can have a ledger entry with `display_index: -1`
and disposition `native graphic retained without overlay`. This removes only
the redundant English box: polarity signs, arrows, readable English and other
confirmed artwork stay visible in the original scan. Battery instructions and
specifications still receive translation overlays. Export preserves both the
suppression ledger and the complete source regions.
English overlays receive a script check during generation, including labels
outside prose panels. Valid electrical units remain allowed. Source scans and
source ledgers intentionally retain their original language. A bounded repair
that cannot resolve an overlay safely retains it with an advisory warning.
These are source records, not an instruction to the browser or a review gate.
The combined readability process keeps English in boxes on the scan. It groups complete
instructions within visual sections, preserves separate contents/table entries,
and tries safe whitespace expansion before reducing text size. Sizes are relative
to the page and text role. No linked footer or appended reading panel is generated.
The webpage and PDF use the same layout; the existing text side panel remains
available. It is enabled per page by the readability marker or validated structural
evidence in the source-preserving layout ledger.
Draft versions, fit measurements, undo
history and UI state must never enter saved manual JSON.

Pure projection/validation lives in `web/js/manual-data.js`; editor state and
history in `edit-session.js`; drafts in `draft-store.js`; proportional text fitting
in `text-fit.js` and `text-fit-dom.js`. Catalog model/view and viewer state are
separate modules. `tests/web/` covers these contracts and combined acceptance flows.

## PDF download

**Download PDF** directly saves a complete manual, with no print dialog, paper
settings, preflight or review gate. One source scan produces one PDF page with
its original aspect ratio. Site controls, page badges, editor handles and browser
chrome are excluded. The responsive reader and editing workspace remain available.

The canonical desktop is a 1280×900 viewport with page content capped at 1200 CSS
pixels. A same-origin frame loads the same stylesheet, shared manual-page DOM and
reader text-fitting function, even when the download starts on a phone. It waits
for each real scan and the bundled font, renders pages sequentially and captures
them at 2× resolution. It uses current corrected-text visibility and text-size
controls and a snapshot of the current edited document. Unsaved inspector text
is committed before export. Unrestored drafts are not included. The document and
source ledger are never rewritten by PDF generation.

The PDF embeds lossless page images at about 192 pixels per inch, preserving
browser layout and original artwork. Text is rasterized, so it is not searchable
or selectable. Raster antialiasing can differ slightly from a native screenshot;
there is no independent text layout or font shrink. This avoids substituting a
second approximate canvas typesetter. Files can be large (several MB per page),
and long manuals use browser memory while assembling the final file. A failed
image/font load reports an error and never downloads a partial manual.

The browser regressions compare all pages of Zero One, landscape GoKaiKeys and
dense Charmant against real desktop captures. Mobile export, current edits,
reader settings and asset failures are checked separately. Chrome is the measured
browser; Safari/Firefox capture and mobile device memory limits remain unverified.
