import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { formatPageIndicator } from '../../web/js/reader-controls.js';

test('reading toolbar exposes the required control labels', async () => {
  const html = await readFile(new URL('../../web/index.html', import.meta.url), 'utf8');
  for (const label of [
    'Catalog',
    'Original PDF',
    'Corrected text: On',
    'A−',
    'Default text size',
    'A+',
    'Download PDF',
    'Edit translations'
  ]) {
    assert.match(html, new RegExp(label.replace('+', '\\+')));
  }
});

test('large-text popover has dialog semantics and a close control', async () => {
  const html = await readFile(new URL('../../web/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="largeTextPopover"[^>]*role="dialog"/);
  assert.match(html, /id="closeLargeTextPopover"/);
});

test('page indicator output uses current and total one-based pages', () => {
  assert.equal(formatPageIndicator(0, 12), 'Page 1 of 12');
  assert.equal(formatPageIndicator(11, 12), 'Page 12 of 12');
});

test('rendering a manual initializes page count before the user scrolls', async () => {
  const source = await readFile(new URL('../../web/js/renderer.js', import.meta.url), 'utf8');
  assert.match(source, /formatPageIndicator\(0,\s*state\.currentManual\.pages\.length\)/);
  assert.match(source, /pageJumpInput\.max/);
});
