import assert from 'node:assert/strict';
import test from 'node:test';

import { calculateFitGeometry, fitOverlayText } from '../../web/js/text-fit-dom.js';

test('calculateFitGeometry subtracts scaled padding from a page-relative bbox', () => {
  assert.deepEqual(
    calculateFitGeometry({
      bbox: [0.1, 0.2, 0.5, 0.25],
      sourceWidth: 1000,
      sourceHeight: 800,
      renderedWidth: 500,
      renderedHeight: 400,
      padding: 4
    }),
    {
      width: 242,
      height: 92,
      scaleX: 0.5,
      scaleY: 0.5
    }
  );
});

test('fitOverlayText passes proportional line height and geometry to measurement', () => {
  const calls = [];
  const result = fitOverlayText({
    text: 'Measured',
    bbox: [0, 0, 0.5, 0.5],
    sourceSize: { width: 1000, height: 800 },
    renderedSize: { width: 500, height: 400 },
    padding: 2,
    minFontSize: 9,
    maxFontSize: 18,
    lineHeight: 1.15,
    measure: (text, style) => {
      calls.push({ text, style });
      return { width: style.fontSize * 2, height: style.fontSize * style.lineHeight };
    }
  });

  assert.equal(result.status, 'fits');
  assert.equal(result.fontSize, 18);
  assert.ok(calls.length >= 2);
  assert.deepEqual(calls[0].text, 'Measured');
  assert.equal(calls[0].style.lineHeight, 1.15);
});

test('calculateFitGeometry reserves overlay borders as well as padding', () => {
  const geometry = calculateFitGeometry({
    bbox: [0.1, 0.2, 0.5, 0.25],
    sourceWidth: 1000,
    sourceHeight: 800,
    renderedWidth: 500,
    renderedHeight: 400,
    padding: 4,
    borderWidth: 1
  });
  assert.equal(geometry.width, 240);
  assert.equal(geometry.height, 90);
});
