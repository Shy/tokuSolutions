import assert from 'node:assert/strict';
import test from 'node:test';

import {
  classifyPrintOrientation,
  createPrintPreparation
} from '../../web/js/print-preparation.js';

function manualFixture() {
  return {
    meta: { manual_name: 'Print Test' },
    pages: [
      {
        image: 'portrait.webp',
        blocks: [{ text: 'A', translation: 'Alpha', bbox: [0, 0, 0.5, 0.5] }]
      },
      {
        image: 'landscape.webp',
        blocks: [{ text: 'B', translation: '', bbox: [0, 0, 0.5, 0.5] }]
      }
    ]
  };
}

test('print orientation classifies portrait, landscape, and square pages', () => {
  assert.equal(classifyPrintOrientation({ width: 800, height: 1200 }), 'portrait');
  assert.equal(classifyPrintOrientation({ width: 1200, height: 800 }), 'landscape');
  assert.equal(classifyPrintOrientation({ width: 1000, height: 1000 }), 'portrait');
});

test('image and font failures block printing', async () => {
  for (const failure of ['image', 'font']) {
    let printed = false;
    const preparation = createPrintPreparation({
      loadImage: async () => {
        if (failure === 'image') throw new Error('image failed');
        return { width: 800, height: 1200, decoded: true };
      },
      loadFont: async () => {
        if (failure === 'font') throw new Error('font failed');
      },
      render: () => ({ cleanup() {} }),
      fit: () => [],
      print: () => {
        printed = true;
      }
    });

    const result = await preparation.prepare(manualFixture());
    assert.equal(result.status, 'blocked');
    assert.equal(result.error, failure);
    assert.equal(printed, false);
  }
});

test('overflow and missing translations are advisory warnings', async () => {
  let printed = false;
  const preparation = createPrintPreparation({
    loadImage: async (page) => ({
      width: page.image.includes('landscape') ? 1200 : 800,
      height: page.image.includes('landscape') ? 800 : 1200,
      decoded: true
    }),
    loadFont: async () => {},
    render: () => ({ cleanup() {} }),
    fit: () => [
      { pageIndex: 0, blockIndex: 0, status: 'overflow' },
      { pageIndex: 1, blockIndex: 0, status: 'missing' }
    ],
    print: () => {
      printed = true;
    }
  });

  const result = await preparation.prepare(manualFixture());
  assert.equal(result.status, 'warning');
  assert.equal(result.requiresPrintAnyway, true);
  assert.equal(result.warnings.length, 2);
  assert.equal(printed, false);
});

test('Print anyway deliberately invokes printing after warnings', async () => {
  let printCount = 0;
  const preparation = createPrintPreparation({
    loadImage: async () => ({ width: 800, height: 1200, decoded: true }),
    loadFont: async () => {},
    render: () => ({ cleanup() {} }),
    fit: () => [{ pageIndex: 0, blockIndex: 0, status: 'overflow' }],
    print: () => {
      printCount += 1;
    }
  });

  const result = await preparation.prepare(manualFixture(), { printAnyway: true });
  assert.equal(result.status, 'printed');
  assert.equal(printCount, 1);
});

test('preparation waits until every lazy page image is decoded', async () => {
  const events = [];
  const preparation = createPrintPreparation({
    loadImage: async (page, index) => {
      await Promise.resolve();
      events.push(`decoded-${index}`);
      return { width: 800, height: 1200, decoded: true };
    },
    loadFont: async () => events.push('font'),
    render: () => {
      events.push('render');
      return { cleanup() {} };
    },
    fit: () => [],
    print: () => events.push('print')
  });

  await preparation.prepare(manualFixture());
  assert.deepEqual(events.slice(0, 3).sort(), ['decoded-0', 'decoded-1', 'font'].sort());
  assert.ok(events.indexOf('render') > events.indexOf('decoded-1'));
  assert.ok(events.indexOf('print') > events.indexOf('render'));
});

test('cleanup removes temporary print state after preview', async () => {
  let cleaned = false;
  const preparation = createPrintPreparation({
    loadImage: async () => ({ width: 800, height: 1200, decoded: true }),
    loadFont: async () => {},
    render: () => ({ cleanup: () => (cleaned = true) }),
    fit: () => [],
    print: () => {},
    afterPrint: (cleanup) => cleanup()
  });

  const result = await preparation.prepare(manualFixture());
  assert.equal(result.status, 'printed');
  assert.equal(cleaned, true);
  assert.equal(preparation.state, 'idle');
});
