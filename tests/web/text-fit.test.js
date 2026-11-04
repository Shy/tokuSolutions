import assert from 'node:assert/strict';
import test from 'node:test';

import { fitText } from '../../web/js/text-fit.js';

function proportionalMeasure(text) {
  return (fontSize) => {
    const lines = text.split('\n');
    return {
      width: Math.max(...lines.map((line) => line.length * fontSize * 0.6)),
      height: lines.length * fontSize * 1.15
    };
  };
}

test('fitText returns the largest fitting size within tolerance', () => {
  const result = fitText({
    text: 'abcdefghij',
    box: { width: 60, height: 30 },
    minFontSize: 9,
    maxFontSize: 20,
    tolerance: 0.01,
    measure: proportionalMeasure('abcdefghij')
  });

  assert.equal(result.status, 'fits');
  assert.ok(result.fontSize >= 9.99 && result.fontSize <= 10.01);
});

test('fitText detects width-only and height-only overflow at the minimum', () => {
  assert.equal(
    fitText({
      text: 'wide',
      box: { width: 20, height: 100 },
      minFontSize: 9,
      maxFontSize: 20,
      measure: () => ({ width: 21, height: 10 })
    }).status,
    'overflow'
  );
  assert.equal(
    fitText({
      text: 'tall',
      box: { width: 100, height: 20 },
      minFontSize: 9,
      maxFontSize: 20,
      measure: () => ({ width: 10, height: 21 })
    }).status,
    'overflow'
  );
});

test('fitText treats an exact boundary as fitting', () => {
  const result = fitText({
    text: 'exact',
    box: { width: 100, height: 23 },
    minFontSize: 10,
    maxFontSize: 20,
    measure: (fontSize) => ({ width: fontSize * 5, height: fontSize * 1.15 })
  });

  assert.equal(result.status, 'fits');
  assert.equal(result.fontSize, 20);
});

test('fitText respects explicit newlines', () => {
  const result = fitText({
    text: 'one\ntwo',
    box: { width: 100, height: 23 },
    minFontSize: 9,
    maxFontSize: 20,
    tolerance: 0.01,
    measure: proportionalMeasure('one\ntwo')
  });

  assert.equal(result.status, 'fits');
  assert.ok(result.fontSize >= 9.99 && result.fontSize <= 10.01);
});

test('fitText reports empty translations as missing', () => {
  assert.deepEqual(
    fitText({
      text: '   ',
      box: { width: 100, height: 100 },
      minFontSize: 9,
      maxFontSize: 20,
      measure: () => ({ width: 0, height: 0 })
    }),
    { status: 'missing', fontSize: null, iterations: 0 }
  );
});

test('fitText reports overflow when no size fits at the 9pt floor', () => {
  const result = fitText({
    text: 'too much',
    box: { width: 10, height: 10 },
    minFontSize: 9,
    maxFontSize: 20,
    measure: proportionalMeasure('too much')
  });

  assert.deepEqual(result, { status: 'overflow', fontSize: 9, iterations: 0 });
});

test('fitText obeys deterministic tolerance and iteration bounds', () => {
  const result = fitText({
    text: 'bounded',
    box: { width: 50, height: 50 },
    minFontSize: 9,
    maxFontSize: 100,
    tolerance: 0.000001,
    maxIterations: 4,
    measure: (fontSize) => ({ width: fontSize, height: fontSize })
  });

  assert.equal(result.iterations, 4);
});

test('fitText rejects stale generations without returning a fit', () => {
  let current = true;
  const result = fitText({
    text: 'stale',
    box: { width: 100, height: 100 },
    minFontSize: 9,
    maxFontSize: 20,
    generation: 2,
    isCurrentGeneration: () => {
      const wasCurrent = current;
      current = false;
      return wasCurrent;
    },
    measure: (fontSize) => ({ width: fontSize, height: fontSize })
  });

  assert.deepEqual(result, { status: 'stale', fontSize: null, iterations: 0 });
});

test('fitText tolerates CSS subpixel quantization but not half-pixel overflow', () => {
  const options = {
    text: 'fractional',
    box: { width: 19.999999999999996, height: 20 },
    minFontSize: 8,
    maxFontSize: 16,
    measure: () => ({ width: 20, height: 10 })
  };
  assert.equal(fitText(options).status, 'fits');
  assert.equal(
    fitText({ ...options, measure: () => ({ width: 20.5, height: 10 }) }).status,
    'overflow'
  );
});
