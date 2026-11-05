import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('editor shell includes contextual inspector, drawer, history, and draft actions', async () => {
  const html = await readFile(new URL('../../web/index.html', import.meta.url), 'utf8');
  for (const id of [
    'blockInspector',
    'inspectorOriginal',
    'inspectorTranslation',
    'inspectorBboxX',
    'inspectorBboxY',
    'inspectorBboxW',
    'inspectorBboxH',
    'allTranslationsBtn',
    'translationsDrawer',
    'undoBtn',
    'redoBtn',
    'discardDraftBtn'
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test('renderer includes all eight resize handles for selected overlays', async () => {
  const source = await readFile(new URL('../../web/js/renderer.js', import.meta.url), 'utf8');
  assert.match(source, /selected\?\.pageIndex === pageIdx/);
  assert.match(source, /attachResizeHandles\(overlay\)/);
  for (const handle of ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']) {
    assert.match(source, new RegExp(`['"]${handle}['"]`));
  }
});
