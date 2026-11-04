import assert from 'node:assert/strict';
import test from 'node:test';

import { filterManuals } from '../../web/js/catalog-model.js';

function createManuals() {
  return [
    {
      name: 'CSM-Den-O-Belt-v2',
      source: 'CSM Den-O v2.pdf',
      tags: ['csm', 'kamen-rider']
    },
    {
      name: 'Ranger-Key-Memorial-Edition',
      source: 'GoKaiKeys.pdf',
      tags: ['memorial', 'sentai']
    },
    {
      name: 'Unsorted-Manual'
    }
  ];
}

test('filterManuals normalizes query whitespace, case, and name separators', () => {
  const result = filterManuals(createManuals(), '  DEN O  ', new Set());

  assert.deepEqual(
    result.map((manual) => manual.name),
    ['CSM-Den-O-Belt-v2']
  );
});

test('filterManuals matches manual names and source filenames', () => {
  const manuals = createManuals();

  assert.deepEqual(
    filterManuals(manuals, 'ranger key', []).map((manual) => manual.name),
    ['Ranger-Key-Memorial-Edition']
  );
  assert.deepEqual(
    filterManuals(manuals, 'gokaikeys.pdf', []).map((manual) => manual.name),
    ['Ranger-Key-Memorial-Edition']
  );
});

test('filterManuals requires every selected tag', () => {
  const result = filterManuals(createManuals(), '', new Set(['csm']));

  assert.deepEqual(
    result.map((manual) => manual.name),
    ['CSM-Den-O-Belt-v2']
  );
});

test('filterManuals returns a new unfiltered array for empty search and tags', () => {
  const manuals = createManuals();
  const result = filterManuals(manuals, '   ', []);

  assert.deepEqual(result, manuals);
  assert.notEqual(result, manuals);
});

test('filterManuals handles missing source and tags', () => {
  const manuals = createManuals();

  assert.deepEqual(
    filterManuals(manuals, 'unsorted', []).map((manual) => manual.name),
    ['Unsorted-Manual']
  );
  assert.deepEqual(filterManuals(manuals, '', ['csm', 'sentai']), []);
});

test('filterManuals does not mutate manuals or selected tags', () => {
  const manuals = createManuals();
  const before = structuredClone(manuals);
  const selectedTags = new Set(['memorial']);

  filterManuals(manuals, 'key', selectedTags);

  assert.deepEqual(manuals, before);
  assert.deepEqual([...selectedTags], ['memorial']);
});

test('filterManuals combines source search with multiple required tags', () => {
  const manuals = createManuals();

  const result = filterManuals(manuals, 'gokaikeys', new Set(['memorial', 'sentai']));

  assert.deepEqual(
    result.map((manual) => manual.name),
    ['Ranger-Key-Memorial-Edition']
  );
});
