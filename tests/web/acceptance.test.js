import assert from 'node:assert/strict';
import test from 'node:test';

import { filterManuals } from '../../web/js/catalog-model.js';
import { createDraftStore } from '../../web/js/draft-store.js';
import { createEditSession } from '../../web/js/edit-session.js';
import { serializeManual } from '../../web/js/manual-data.js';
import { manualNameFromHash } from '../../web/js/navigation.js';
import { createPrintPreparation } from '../../web/js/print-preparation.js';
import { createViewerState, reduceViewerState } from '../../web/js/viewer-state.js';

function manualFixture() {
  return {
    meta: { manual_name: 'Acceptance Manual', source: 'source.pdf' },
    pages: [
      {
        image: 'page.webp',
        blocks: [{ text: 'A', translation: 'Alpha', bbox: [0.1, 0.1, 0.2, 0.2] }]
      }
    ]
  };
}

test('catalog search, filtering, and encoded deep links agree on a manual', () => {
  const manuals = [
    { name: 'CSM-Test-Driver', source: 'test.pdf', tags: ['rider'] },
    { name: 'Sentai-Robo', source: 'robo.pdf', tags: ['sentai'] }
  ];
  assert.deepEqual(
    filterManuals(manuals, 'test driver', new Set(['rider'])).map((manual) => manual.name),
    ['CSM-Test-Driver']
  );
  assert.equal(manualNameFromHash('#CSM%20Test%20Driver'), 'CSM Test Driver');
});

test('reading defaults, corrected text, and capability cutover work together', () => {
  const phone = createViewerState({ width: 390, finePointer: true });
  assert.equal(phone.mode, 'reading');
  assert.equal(phone.canEdit, false);

  let desktop = createViewerState({ width: 1280, finePointer: true });
  desktop = reduceViewerState(desktop, { type: 'enter-editing' });
  assert.equal(desktop.mode, 'editing');
  desktop = reduceViewerState(desktop, {
    type: 'set-corrected-text-visible',
    visible: false
  });
  assert.equal(desktop.correctedTextVisible, false);
});

test('desktop edit, bbox, undo, redo, draft restore, and export purity form one flow', () => {
  const source = manualFixture();
  const session = createEditSession(source);
  session.updateTranslation(0, 0, 'Changed');
  session.updateBbox(0, 0, [0.2, 0.2, 0.3, 0.3]);
  session.addBlock(0, { text: 'B', translation: 'Beta', bbox: [0, 0, 0.2, 0.2] });
  session.deleteBlock(0, 1);
  session.undo();
  session.redo();

  const values = new Map();
  const store = createDraftStore({
    storage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key)
    }
  });
  store.save('Acceptance Manual', source, session.manual);
  const restored = store.load('Acceptance Manual', source);
  const json = serializeManual(restored.manual);

  assert.equal(restored.status, 'compatible');
  assert.equal(JSON.parse(json).pages[0].blocks[0].translation, 'Changed');
  for (const transient of ['undo', 'redo', 'draft', 'fingerprint', 'viewer']) {
    assert.equal(json.toLowerCase().includes(transient), false);
  }
});

test('full-manual print preparation waits for all pages and requires warning override', async () => {
  const decoded = [];
  let prints = 0;
  const preparation = createPrintPreparation({
    loadImage: async (_page, index) => {
      decoded.push(index);
      return { width: 800, height: 1200, decoded: true };
    },
    loadFont: async () => {},
    render: () => ({ cleanup() {} }),
    fit: () => [{ pageIndex: 0, blockIndex: 0, status: 'overflow' }],
    print: () => {
      prints += 1;
    }
  });
  const warning = await preparation.prepare(manualFixture());
  assert.deepEqual(decoded, [0]);
  assert.equal(warning.status, 'warning');
  assert.equal(prints, 0);
  await preparation.prepare(manualFixture(), { printAnyway: true });
  assert.equal(prints, 1);
});
