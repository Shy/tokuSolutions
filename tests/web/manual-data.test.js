import assert from 'node:assert/strict';
import test from 'node:test';

import {
  serializeManual,
  toPersistedManual,
  validateManualDocument
} from '../../web/js/manual-data.js';

function createManual() {
  return {
    meta: {
      manual_name: 'Test Manual',
      source: 'test.pdf',
      custom_metadata: {
        contributor: 'A real person'
      }
    },
    pages: [
      {
        image: 'pages/page-0.webp',
        blocks: [
          {
            text: '取扱説明書',
            translation: 'Instruction manual',
            bbox: [0.1, 0.2, 0.3, 0.4]
          }
        ]
      }
    ]
  };
}

test('validateManualDocument accepts a valid manual without mutating it', () => {
  const manual = createManual();
  const before = structuredClone(manual);

  assert.equal(validateManualDocument(manual), manual);
  assert.deepEqual(manual, before);
});

test('validateManualDocument rejects missing meta and non-array pages', () => {
  assert.throws(() => validateManualDocument({ pages: [] }), /manual\.meta must be an object/);
  assert.throws(
    () => validateManualDocument({ meta: {}, pages: {} }),
    /manual\.pages must be an array/
  );
});

test('validateManualDocument reports paths for malformed blocks and bboxes', () => {
  const invalidBlocks = createManual();
  invalidBlocks.pages[0].blocks = {};
  assert.throws(
    () => validateManualDocument(invalidBlocks),
    /manual\.pages\[0\]\.blocks must be an array/
  );

  const invalidBbox = createManual();
  invalidBbox.pages[0].blocks[0].bbox = [0.1, 'nope', 0.3];
  assert.throws(() => validateManualDocument(invalidBbox), /manual\.pages\[0\]\.blocks\[0\]\.bbox/);
});

test('toPersistedManual projects only persisted manual, page, and block fields', () => {
  const manual = createManual();
  manual.history = [{ type: 'translation' }];
  manual.draft = { version: 1 };
  manual.fitResults = { '0-0': { fontSize: 12 } };
  manual.viewerState = { mode: 'editing' };
  manual.pages[0].layoutCache = { width: 800 };
  manual.pages[0].blocks[0].selected = true;
  manual.pages[0].blocks[0].fit = { status: 'fits' };

  const persisted = toPersistedManual(manual);

  assert.deepEqual(Object.keys(persisted), ['meta', 'pages']);
  assert.deepEqual(Object.keys(persisted.pages[0]), ['image', 'blocks']);
  assert.deepEqual(Object.keys(persisted.pages[0].blocks[0]), ['text', 'translation', 'bbox']);
  assert.deepEqual(persisted.meta.custom_metadata, {
    contributor: 'A real person'
  });
});

test('serializeManual excludes transient state from copied JSON', () => {
  const manual = createManual();
  manual.history = [{ transientMarker: 'UNDO_HISTORY' }];
  manual.draft = { transientMarker: 'DRAFT_ENVELOPE' };
  manual.pages[0].blocks[0].fit = { transientMarker: 'FIT_RESULT' };

  const json = serializeManual(manual);
  const parsed = JSON.parse(json);

  assert.deepEqual(parsed, toPersistedManual(manual));
  assert.doesNotMatch(json, /UNDO_HISTORY|DRAFT_ENVELOPE|FIT_RESULT/);
});

test('persisted output is deeply detached from the active edited manual', () => {
  const manual = createManual();
  const persisted = toPersistedManual(manual);

  persisted.meta.custom_metadata.contributor = 'Changed';
  persisted.pages[0].image = 'changed.webp';
  persisted.pages[0].blocks[0].translation = 'Changed';
  persisted.pages[0].blocks[0].bbox[0] = 0.9;

  assert.deepEqual(manual, createManual());
});

test('an edited browser session exports only its manual value', () => {
  const session = {
    manual: createManual(),
    undoStack: [{ transientMarker: 'UNDO_STACK' }],
    redoStack: [{ transientMarker: 'REDO_STACK' }],
    draftEnvelope: {
      version: 1,
      transientMarker: 'DRAFT_METADATA'
    },
    fitResults: {
      '0-0': {
        status: 'fits',
        transientMarker: 'FIT_CACHE'
      }
    },
    viewerState: {
      mode: 'editing',
      transientMarker: 'VIEWER_STATE'
    }
  };

  session.manual.pages[0].blocks[0].translation = 'Corrected by a person';

  const json = serializeManual(session.manual);
  const parsed = JSON.parse(json);

  assert.equal(parsed.pages[0].blocks[0].translation, 'Corrected by a person');
  assert.doesNotMatch(json, /UNDO_STACK|REDO_STACK|DRAFT_METADATA|FIT_CACHE|VIEWER_STATE/);
  assert.throws(() => serializeManual(session), /manual\.meta must be an object/);
});

test('semantic layout and source provenance survive editor export without transient state', () => {
  const manual = createManual();
  manual.pages[0].source_blocks = structuredClone(manual.pages[0].blocks);
  manual.pages[0].layout_provenance = { version: 1, mapping: [{ source_ids: [0] }] };
  manual.pages[0].blocks[0].layout = 'panel';
  const persisted = toPersistedManual(manual);
  assert.equal(persisted.pages[0].blocks[0].layout, 'panel');
  assert.deepEqual(persisted.pages[0].source_blocks, manual.pages[0].source_blocks);
  assert.deepEqual(persisted.pages[0].layout_provenance, manual.pages[0].layout_provenance);
  persisted.pages[0].source_blocks[0].text = 'Changed';
  assert.notEqual(manual.pages[0].source_blocks[0].text, 'Changed');
});

test('native artwork suppression survives editing and serialized export', () => {
  const manual = createManual();
  const page = manual.pages[0];
  page.source_blocks = [
    structuredClone(page.blocks[0]),
    { text: '+', translation: '+', bbox: [0.6, 0.2, 0.02, 0.02] }
  ];
  page.layout_provenance = {
    version: 2,
    plan: { panels: [], furigana: [], notes: [], native_artwork: [1] },
    mapping: [
      { display_index: 0, source_ids: [0], furigana_ids: [] },
      {
        display_index: -1,
        source_ids: [1],
        furigana_ids: [],
        disposition: 'native graphic retained without overlay'
      }
    ]
  };
  page.blocks[0].translation = 'Edited instruction';
  const exported = JSON.parse(serializeManual(manual));
  validateManualDocument(exported);
  assert.equal(exported.pages[0].blocks.length, 1);
  assert.deepEqual(exported.pages[0].source_blocks, page.source_blocks);
  assert.deepEqual(exported.pages[0].layout_provenance, page.layout_provenance);
});
