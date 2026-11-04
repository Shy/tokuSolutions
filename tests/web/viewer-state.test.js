import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canEditTranslations,
  createViewerState,
  reduceViewerState
} from '../../web/js/viewer-state.js';

test('viewer state starts in reading mode with corrected text visible', () => {
  assert.deepEqual(createViewerState(), {
    mode: 'reading',
    canEdit: false,
    correctedTextVisible: true,
    selectedBlock: null,
    currentPage: 0,
    openInspector: null,
    openPopover: null
  });
});

test('editing requires at least 1024 CSS pixels and a fine pointer', () => {
  assert.equal(canEditTranslations({ width: 1023, finePointer: true }), false);
  assert.equal(canEditTranslations({ width: 1024, finePointer: false }), false);
  assert.equal(canEditTranslations({ width: 1024, finePointer: true }), true);
});

test('losing editing capability safely returns to reading mode', () => {
  let viewer = createViewerState({ width: 1200, finePointer: true });
  viewer = reduceViewerState(viewer, { type: 'enter-editing' });
  viewer = reduceViewerState(viewer, {
    type: 'select-block',
    block: { pageIndex: 2, blockIndex: 3 }
  });
  viewer = reduceViewerState(viewer, { type: 'open-inspector', inspector: 'block' });
  viewer = reduceViewerState(viewer, { type: 'open-popover', popover: 'page-jump' });

  const next = reduceViewerState(viewer, {
    type: 'capability-changed',
    capability: { width: 800, finePointer: true }
  });

  assert.equal(next.mode, 'reading');
  assert.equal(next.canEdit, false);
  assert.equal(next.selectedBlock, null);
  assert.equal(next.openInspector, null);
  assert.equal(next.openPopover, null);
});

test('viewer transitions corrected text, selection, and current page independently', () => {
  let viewer = createViewerState();
  viewer = reduceViewerState(viewer, {
    type: 'set-corrected-text-visible',
    visible: false
  });
  viewer = reduceViewerState(viewer, {
    type: 'select-block',
    block: { pageIndex: 1, blockIndex: 4 }
  });
  viewer = reduceViewerState(viewer, { type: 'set-current-page', page: 5 });

  assert.equal(viewer.correctedTextVisible, false);
  assert.deepEqual(viewer.selectedBlock, { pageIndex: 1, blockIndex: 4 });
  assert.equal(viewer.currentPage, 5);
});

test('entering and exiting editing clears transient editor UI state', () => {
  let viewer = createViewerState({ width: 1024, finePointer: true });
  viewer = reduceViewerState(viewer, { type: 'enter-editing' });
  viewer = reduceViewerState(viewer, {
    type: 'select-block',
    block: { pageIndex: 0, blockIndex: 1 }
  });
  viewer = reduceViewerState(viewer, { type: 'open-inspector', inspector: 'block' });
  viewer = reduceViewerState(viewer, { type: 'open-popover', popover: 'display' });

  assert.equal(viewer.mode, 'editing');
  assert.equal(viewer.openInspector, 'block');
  assert.equal(viewer.openPopover, 'display');

  viewer = reduceViewerState(viewer, { type: 'exit-editing' });

  assert.equal(viewer.mode, 'reading');
  assert.equal(viewer.selectedBlock, null);
  assert.equal(viewer.openInspector, null);
  assert.equal(viewer.openPopover, null);
});

test('enter editing is ignored when the device is not capable', () => {
  const viewer = reduceViewerState(createViewerState(), { type: 'enter-editing' });
  assert.equal(viewer.mode, 'reading');
});

test('viewer mode changes never mutate manual data', () => {
  const manual = {
    meta: { manual_name: 'Test Manual' },
    pages: [{ image: 'page.jpg', blocks: [{ text: 'A', translation: 'B', bbox: [0, 0, 1, 1] }] }]
  };
  const before = structuredClone(manual);

  let viewer = createViewerState({ width: 1400, finePointer: true });
  viewer = reduceViewerState(viewer, { type: 'enter-editing', manual });
  viewer = reduceViewerState(viewer, { type: 'exit-editing', manual });

  assert.deepEqual(manual, before);
});
