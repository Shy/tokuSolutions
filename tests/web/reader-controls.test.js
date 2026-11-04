import assert from 'node:assert/strict';
import test from 'node:test';

import {
  READER_SCALE_STEPS,
  adjustReaderScale,
  createReaderPreferences,
  pageJumpIndex,
  requiresLargeTextPopover,
  serializeReaderPreferences,
  toggleCorrectedText
} from '../../web/js/reader-controls.js';

test('reader corrected-text control toggles On and Off state', () => {
  const initial = createReaderPreferences();
  assert.equal(initial.correctedTextVisible, true);
  assert.equal(toggleCorrectedText(initial).correctedTextVisible, false);
  assert.equal(toggleCorrectedText(toggleCorrectedText(initial)).correctedTextVisible, true);
});

test('reader size controls stay within defined bounds and serialize safely', () => {
  assert.equal(adjustReaderScale(READER_SCALE_STEPS[0], -1), READER_SCALE_STEPS[0]);
  assert.equal(adjustReaderScale(READER_SCALE_STEPS.at(-1), 1), READER_SCALE_STEPS.at(-1));
  assert.equal(adjustReaderScale(1, 1), 1.15);
  assert.equal(adjustReaderScale(1.15, -1), 1);
  assert.equal(
    serializeReaderPreferences({ correctedTextVisible: false, scale: 1.15 }),
    '{"version":1,"correctedTextVisible":false,"scale":1.15}'
  );
});

test('pageJumpIndex accepts one-based pages and rejects invalid targets', () => {
  assert.equal(pageJumpIndex('1', 8), 0);
  assert.equal(pageJumpIndex('8', 8), 7);
  assert.equal(pageJumpIndex('0', 8), null);
  assert.equal(pageJumpIndex('9', 8), null);
  assert.equal(pageJumpIndex('nope', 8), null);
});

test('large-text popover is required below preference or for overflow', () => {
  assert.equal(
    requiresLargeTextPopover({ status: 'fits', fontSize: 11, preferredFontSize: 14 }),
    true
  );
  assert.equal(
    requiresLargeTextPopover({ status: 'fits', fontSize: 14, preferredFontSize: 14 }),
    false
  );
  assert.equal(
    requiresLargeTextPopover({ status: 'overflow', fontSize: 9, preferredFontSize: 14 }),
    true
  );
  assert.equal(
    requiresLargeTextPopover({ status: 'missing', fontSize: null, preferredFontSize: 14 }),
    false
  );
});
