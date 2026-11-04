import assert from 'node:assert/strict';
import test from 'node:test';

import { buildCatalogCard, buildCatalogView } from '../../web/js/catalog-view.js';

const tags = {
  rider: { label: 'Kamen Rider', color: '#d92d20' },
  unsafe: { label: 'Fallback', color: 'javascript:alert(1)' }
};

test('catalog card view model includes title, counts, tags, thumbnail, and Original', () => {
  const card = buildCatalogCard(
    {
      name: 'CSM-Test-Driver',
      pages: 12,
      blocks: 34,
      thumbnail: 'CSM-Test-Driver/pages/page-0.webp',
      tags: ['rider'],
      source_url: 'https://example.com/manual.pdf'
    },
    tags
  );

  assert.equal(card.title, 'CSM Test Driver');
  assert.equal(card.pageCount, 12);
  assert.equal(card.blockCount, 34);
  assert.equal(card.thumbnail, '../manuals/CSM-Test-Driver/pages/page-0.webp');
  assert.equal(card.originalUrl, 'https://example.com/manual.pdf');
  assert.deepEqual(card.tags, [{ label: 'Kamen Rider', markerColor: '#d92d20' }]);
});

test('tag models are neutral labels with validated marker colors', () => {
  const card = buildCatalogCard(
    { name: 'Test', pages: 1, blocks: 1, thumbnail: 'x.webp', tags: ['unsafe'] },
    tags
  );
  assert.deepEqual(card.tags, [{ label: 'Fallback', markerColor: '#98a2b3' }]);
  assert.equal(Object.hasOwn(card.tags[0], 'backgroundColor'), false);
});

test('blog metadata never creates a Tech Blog link', () => {
  const card = buildCatalogCard(
    {
      name: 'Test',
      pages: 1,
      blocks: 1,
      thumbnail: 'x.webp',
      blog_url: 'https://example.com/blog',
      blog_links: [{ title: 'Tech Blog', url: 'https://example.com/blog' }]
    },
    tags
  );
  assert.equal(JSON.stringify(card).includes('blog'), false);
  assert.equal(JSON.stringify(card).includes('Tech Blog'), false);
});

test('catalog view exposes empty and failed states', () => {
  assert.deepEqual(buildCatalogView([], tags), {
    status: 'empty',
    countLabel: '0 manuals',
    cards: []
  });
  assert.deepEqual(buildCatalogView([], tags, { error: 'Network failed' }), {
    status: 'failed',
    countLabel: 'Catalog unavailable',
    message: 'Network failed',
    cards: []
  });
});
