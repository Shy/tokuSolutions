import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { buildPrintPageModels } from '../../web/js/print-renderer.js';

test('print model creates one named A4 sheet per source page with corrected content only', () => {
  const manual = {
    meta: { manual_name: 'Print' },
    pages: [
      {
        image: 'p1.webp',
        blocks: [{ text: 'Japanese', translation: 'English', bbox: [0.1, 0.2, 0.3, 0.4] }]
      },
      { image: 'p2.webp', blocks: [] }
    ]
  };
  const models = buildPrintPageModels(manual, [
    { width: 800, height: 1200, src: '/p1.webp' },
    { width: 1200, height: 800, src: '/p2.webp' }
  ]);

  assert.equal(models.length, 2);
  assert.equal(models[0].pageName, 'manual-portrait');
  assert.equal(models[1].pageName, 'manual-landscape');
  assert.deepEqual(models[0].blocks, [
    { translation: 'English', bbox: [0.1, 0.2, 0.3, 0.4], blockIndex: 0 }
  ]);
  assert.equal(JSON.stringify(models).includes('Japanese'), false);
});

test('print renderer source excludes site chrome and editing controls', async () => {
  const source = await readFile(new URL('../../web/js/print-renderer.js', import.meta.url), 'utf8');
  assert.match(source, /print-sheet/);
  assert.match(source, /print-overlay/);
  for (const forbidden of ['viewer-toolbar', 'page-label', 'resize-handle', 'text-panel']) {
    assert.equal(source.includes(forbidden), false);
  }
});
