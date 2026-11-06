import assert from 'node:assert/strict';
import test from 'node:test';
import { layoutReadabilityPage, usesReadabilityLayout } from '../../web/js/readability-layout.js';

test('validated structural pages receive the canvas fitter outside the original pilot', () => {
  assert.equal(usesReadabilityLayout({ layout_provenance: { structure: { version: 1 } } }), true);
  assert.equal(usesReadabilityLayout({ layout_provenance: { readability: { version: 1 } } }), true);
  assert.equal(usesReadabilityLayout({ layout_provenance: { structure: { version: 0 } } }), false);
  assert.equal(usesReadabilityLayout({}), false);
});

const measure = (text, font, width) => ({
  width: Math.min(text.length * font * 0.45, width),
  height: Math.ceil((text.length * font * 0.45) / width) * font * 1.15,
  wordWidth: Math.max(...text.split(/\s+/).map((word) => word.length * font * 0.45))
});

test('expands into verified whitespace before shrinking and remains canvas relative', () => {
  const blocks = [
    {
      translation: 'Press the release button.',
      text: 'ボタンを押します。',
      bbox: [0.2, 0.2, 0.08, 0.025],
      role: 'body'
    }
  ];
  const render = (width) =>
    layoutReadabilityPage({ blocks, width, height: width * 1.4, measure, isSafe: () => true });
  const small = render(600)[0],
    large = render(1200)[0];
  assert.equal(large.placement, 'overlay');
  assert.ok(large.bbox[2] > 0.08);
  assert.deepEqual(
    small.bbox.map((x) => Math.round(x * 10000)),
    large.bbox.map((x) => Math.round(x * 10000))
  );
  assert.ok(Math.abs(large.fontSize - small.fontSize * 2) <= 0.25);
});

test('protected artwork and section borders keep instructions in place', () => {
  const blocks = [
    {
      translation: 'Transformation instructions are complete here.',
      text: '説明',
      bbox: [0.2, 0.2, 0.04, 0.01],
      role: 'body'
    }
  ];
  const [result] = layoutReadabilityPage({
    blocks,
    width: 1200,
    height: 1600,
    measure,
    isSafe: () => false
  });
  assert.equal(result.placement, 'overlay');
  assert.equal(result.status, 'fits');
  assert.deepEqual(result.bbox, blocks[0].bbox);
});

test('overlapping source boxes do not produce overlapping English text', () => {
  const blocks = [
    {
      translation: 'Press the button.',
      text: '押します。',
      bbox: [0.2, 0.2, 0.1, 0.02],
      role: 'body'
    },
    { translation: 'Reset button', text: 'リセット', bbox: [0.25, 0.2, 0.1, 0.02], role: 'label' }
  ];
  const results = layoutReadabilityPage({
    blocks,
    width: 1200,
    height: 1600,
    measure,
    isSafe: () => true
  });
  assert.ok(results.every((result) => result.placement === 'overlay'));
  const [a, b] = results.map((result) => result.bbox);
  assert.ok(
    a[0] + a[2] <= b[0] || b[0] + b[2] <= a[0] || a[1] + a[3] <= b[1] || b[1] + b[3] <= a[1]
  );
});

test('table projections stay separate and invalidate after a parent edit', async () => {
  const { displayBlocksForPage } = await import('../../web/js/readability-layout.js');
  const page = {
    blocks: [{ text: 'a\nb', translation: 'A\nB', bbox: [0.1, 0.2, 0.3, 0.2] }],
    layout_provenance: {
      row_layout: {
        0: {
          translation: 'A\nB',
          rows: [
            { text: 'a', translation: 'A', bbox: [0.1, 0.2, 0.3, 0.09] },
            { text: 'b', translation: 'B', bbox: [0.1, 0.3, 0.3, 0.09] }
          ]
        }
      }
    }
  };
  assert.deepEqual(
    displayBlocksForPage(page).map((row) => row.translation),
    ['A', 'B']
  );
  assert.ok(displayBlocksForPage(page).every((row) => row.isTableRow));
  page.blocks[0].translation = 'Edited paragraph';
  assert.deepEqual(
    displayBlocksForPage(page).map((row) => row.translation),
    ['Edited paragraph']
  );
});

test('structural picture bounds protect blank artwork interiors during expansion', async () => {
  const { structureAllowsBox } = await import('../../web/js/readability-layout.js');
  const page = {
    layout_provenance: {
      structure: {
        variants: [{ regions: [{ kind: 'picture', bbox: [0.3, 0.1, 0.4, 0.3], source_ids: [] }] }]
      }
    }
  };
  assert.equal(structureAllowsBox(page, [0.1, 0.15, 0.15, 0.03], [0.1, 0.15, 0.25, 0.03]), false);
  assert.equal(structureAllowsBox(page, [0.1, 0.15, 0.15, 0.03], [0.08, 0.15, 0.17, 0.03]), true);
  // A label already inside an illustration can still occupy its source extent.
  assert.equal(structureAllowsBox(page, [0.4, 0.2, 0.1, 0.03], [0.4, 0.2, 0.1, 0.03]), true);
});

test('a text box inside a detected cell cannot expand into its neighbor', async () => {
  const { structureAllowsBox } = await import('../../web/js/readability-layout.js');
  const page = {
    layout_provenance: {
      structure: {
        variants: [{ regions: [{ kind: 'cell', bbox: [0.1, 0.1, 0.3, 0.1], source_ids: [0] }] }]
      }
    }
  };
  assert.equal(structureAllowsBox(page, [0.12, 0.12, 0.1, 0.03], [0.12, 0.12, 0.25, 0.05]), true);
  assert.equal(structureAllowsBox(page, [0.12, 0.12, 0.1, 0.03], [0.12, 0.12, 0.3, 0.05]), false);
});

test('a single-cell projection expands one entry without changing its saved translation', async () => {
  const { displayBlocksForPage } = await import('../../web/js/readability-layout.js');
  const page = {
    blocks: [{ text: 'a', translation: 'A', bbox: [0.1, 0.1, 0.1, 0.02] }],
    layout_provenance: {
      row_layout: {
        0: {
          translation: 'A',
          rows: [{ text: 'a', translation: 'A', bbox: [0.1, 0.1, 0.6, 0.04] }]
        }
      }
    }
  };
  assert.equal(displayBlocksForPage(page)[0].bbox[2], 0.6);
  assert.equal(page.blocks[0].bbox[2], 0.1);
});

test('a near-page-sized picture detection remains advisory rather than freezing all text', async () => {
  const { structureAllowsBox } = await import('../../web/js/readability-layout.js');
  const page = {
    layout_provenance: {
      structure: { variants: [{ regions: [{ kind: 'picture', bbox: [0.03, 0.02, 0.94, 0.94] }] }] }
    }
  };
  assert.equal(structureAllowsBox(page, [0.1, 0.1, 0.2, 0.03], [0.1, 0.1, 0.25, 0.03]), true);
});

test('ink outside a candidate cannot subsidize expansion across a border', async () => {
  const { addedInkOutside } = await import('../../web/js/readability-layout.js');
  const points = [
    [0.06, 0.15],
    [0.061, 0.15],
    [0.062, 0.15],
    [0.15, 0.15],
    ...Array.from({ length: 20 }, (_, index) => [0.11 + index * 0.001, 0.095])
  ];
  const ink = ([x, y, w, h]) =>
    points.filter(([px, py]) => px >= x && px < x + w && py >= y && py < y + h).length;
  const candidate = [0.05, 0.1, 0.15, 0.1],
    allowance = [0.09, 0.09, 0.12, 0.12];
  assert.ok(ink(candidate) - ink(allowance) < 0);
  assert.equal(addedInkOutside(candidate, allowance, ink), 3);
});

test('semantic cell text extents do not restrict expansion into verified whitespace', async () => {
  const { structureAllowsBox } = await import('../../web/js/readability-layout.js');
  const page = {
    layout_provenance: {
      structure: {
        variants: [
          { regions: [{ kind: 'cell_text', bbox: [0.1, 0.1, 0.1, 0.03], source_ids: [0] }] }
        ]
      }
    }
  };
  assert.equal(structureAllowsBox(page, [0.1, 0.1, 0.1, 0.03], [0.1, 0.1, 0.3, 0.03]), true);
});
