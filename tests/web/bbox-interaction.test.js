import assert from 'node:assert/strict';
import test from 'node:test';

import {
  beginBboxGesture,
  cancelBboxGesture,
  commitBboxGesture,
  normalizedPointerDelta,
  updateBboxGesture
} from '../../web/js/bbox-interaction.js';

test('pointer deltas convert to normalized page coordinates', () => {
  assert.deepEqual(normalizedPointerDelta({ x: 50, y: -20 }, { width: 500, height: 400 }), {
    x: 0.1,
    y: -0.05
  });
});

test('drag clamps the box inside page bounds', () => {
  const gesture = beginBboxGesture({
    kind: 'drag',
    bbox: [0.8, 0.8, 0.2, 0.2],
    pointer: { x: 0, y: 0 }
  });
  const updated = updateBboxGesture(gesture, { x: 0.5, y: 0.5 });
  assert.deepEqual(updated.current, [0.8, 0.8, 0.2, 0.2]);
});

test('each resize edge and corner moves the expected sides', () => {
  const expected = {
    n: [0.2, 0.3, 0.4, 0.3],
    s: [0.2, 0.2, 0.4, 0.5],
    e: [0.2, 0.2, 0.5, 0.4],
    w: [0.3, 0.2, 0.3, 0.4],
    ne: [0.2, 0.3, 0.5, 0.3],
    nw: [0.3, 0.3, 0.3, 0.3],
    se: [0.2, 0.2, 0.5, 0.5],
    sw: [0.3, 0.2, 0.3, 0.5]
  };

  for (const [handle, bbox] of Object.entries(expected)) {
    const gesture = beginBboxGesture({
      kind: 'resize',
      handle,
      bbox: [0.2, 0.2, 0.4, 0.4],
      pointer: { x: 0, y: 0 }
    });
    assert.deepEqual(updateBboxGesture(gesture, { x: 0.1, y: 0.1 }).current, bbox);
  }
});

test('resize enforces minimum dimensions and page bounds', () => {
  const gesture = beginBboxGesture({
    kind: 'resize',
    handle: 'nw',
    bbox: [0.01, 0.01, 0.03, 0.03],
    pointer: { x: 0, y: 0 },
    minSize: { width: 0.02, height: 0.02 }
  });
  const updated = updateBboxGesture(gesture, { x: 1, y: 1 });
  assert.deepEqual(updated.current, [0.02, 0.02, 0.02, 0.02]);
});

test('gesture start, update, commit, and cancel preserve one logical before/after pair', () => {
  const started = beginBboxGesture({
    kind: 'drag',
    bbox: [0.1, 0.1, 0.2, 0.2],
    pointer: { x: 0.2, y: 0.2 }
  });
  const updated = updateBboxGesture(started, { x: 0.4, y: 0.5 });
  assert.deepEqual(commitBboxGesture(updated), {
    before: [0.1, 0.1, 0.2, 0.2],
    after: [0.3, 0.4, 0.2, 0.2]
  });
  assert.deepEqual(cancelBboxGesture(updated), [0.1, 0.1, 0.2, 0.2]);
});
