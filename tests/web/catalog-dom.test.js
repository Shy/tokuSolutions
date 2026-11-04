import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('home shell keeps search, filters, result count, and home-only footer', async () => {
  const html = await readFile(new URL('../../web/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="searchInput"/);
  assert.match(html, /id="tagFilters"/);
  assert.match(html, /id="statsDisplay"/);
  assert.match(html, /id="homeFooter"/);
  const viewer = html.slice(html.indexOf('id="viewerView"'));
  assert.equal(viewer.includes('site-footer'), false);
});

test('navigation retains deep-link loading and catalog filtering integration', async () => {
  const [app, navigation] = await Promise.all([
    readFile(new URL('../../web/app.js', import.meta.url), 'utf8'),
    readFile(new URL('../../web/js/navigation.js', import.meta.url), 'utf8')
  ]);
  assert.match(app, /window\.location\.hash/);
  assert.match(navigation, /filterCatalogManuals/);
  assert.match(navigation, /renderManualList\(filteredManuals\)/);
});
