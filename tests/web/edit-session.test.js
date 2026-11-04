import assert from 'node:assert/strict';
import test from 'node:test';

import { createEditSession } from '../../web/js/edit-session.js';
import { serializeManual } from '../../web/js/manual-data.js';

function manualFixture() {
  return {
    meta: { manual_name: 'Editing Test' },
    pages: [
      {
        image: 'page.webp',
        blocks: [
          { text: 'A', translation: 'Alpha', bbox: [0.1, 0.1, 0.2, 0.2] },
          { text: 'B', translation: 'Beta', bbox: [0.4, 0.4, 0.2, 0.2] }
        ]
      }
    ]
  };
}

test('translation and bbox actions are undoable and redoable', () => {
  const session = createEditSession(manualFixture());
  session.updateTranslation(0, 0, 'Updated');
  session.updateBbox(0, 0, [-1, 0.9, 2, 0.5]);

  assert.equal(session.manual.pages[0].blocks[0].translation, 'Updated');
  assert.deepEqual(session.manual.pages[0].blocks[0].bbox, [0, 0.9, 1, 0.1]);

  session.undo();
  assert.deepEqual(session.manual.pages[0].blocks[0].bbox, [0.1, 0.1, 0.2, 0.2]);
  session.undo();
  assert.equal(session.manual.pages[0].blocks[0].translation, 'Alpha');

  session.redo();
  session.redo();
  assert.equal(session.manual.pages[0].blocks[0].translation, 'Updated');
  assert.deepEqual(session.manual.pages[0].blocks[0].bbox, [0, 0.9, 1, 0.1]);
});

test('add and delete restore exact indices through undo and redo', () => {
  const session = createEditSession(manualFixture());
  const added = { text: 'C', translation: 'Gamma', bbox: [0.2, 0.2, 0.2, 0.2] };

  session.addBlock(0, added, 1);
  assert.deepEqual(
    session.manual.pages[0].blocks.map((block) => block.text),
    ['A', 'C', 'B']
  );
  session.deleteBlock(0, 1);
  assert.deepEqual(
    session.manual.pages[0].blocks.map((block) => block.text),
    ['A', 'B']
  );
  session.undo();
  assert.deepEqual(
    session.manual.pages[0].blocks.map((block) => block.text),
    ['A', 'C', 'B']
  );
  session.undo();
  assert.deepEqual(
    session.manual.pages[0].blocks.map((block) => block.text),
    ['A', 'B']
  );
  session.redo();
  assert.equal(session.manual.pages[0].blocks[1].text, 'C');
});

test('a new action after undo clears redo history', () => {
  const session = createEditSession(manualFixture());
  session.updateTranslation(0, 0, 'One');
  session.undo();
  assert.equal(session.canRedo, true);
  session.updateTranslation(0, 0, 'Two');
  assert.equal(session.canRedo, false);
});

test('one drag gesture commits exactly one bbox action', () => {
  const session = createEditSession(manualFixture());
  session.commitBboxGesture(0, 0, [0.1, 0.1, 0.2, 0.2], [0.3, 0.3, 0.2, 0.2]);
  assert.equal(session.undoDepth, 1);
  session.undo();
  assert.deepEqual(session.manual.pages[0].blocks[0].bbox, [0.1, 0.1, 0.2, 0.2]);
});

test('history and session metadata never enter persisted manual JSON', () => {
  const session = createEditSession(manualFixture());
  session.updateTranslation(0, 0, 'Changed');
  session.selectBlock({ pageIndex: 0, blockIndex: 0 });
  const json = serializeManual(session.manual);

  assert.equal(json.includes('undoStack'), false);
  assert.equal(json.includes('selectedBlock'), false);
  assert.equal(json.includes('dirty'), false);
  assert.equal(JSON.parse(json).pages[0].blocks[0].translation, 'Changed');
});

test('notifications and selection stay coherent across add and delete', () => {
  const notifications = [];
  const session = createEditSession(manualFixture(), {
    onChange: (notification) => notifications.push(notification)
  });

  session.addBlock(0, { text: 'C', translation: 'Gamma', bbox: [0, 0, 0.2, 0.2] });
  assert.deepEqual(session.selectedBlock, { pageIndex: 0, blockIndex: 2 });
  session.deleteBlock(0, 2);
  assert.deepEqual(session.selectedBlock, { pageIndex: 0, blockIndex: 1 });
  assert.deepEqual(
    notifications.map((item) => item.kind),
    ['commit', 'commit']
  );
  assert.equal(notifications.at(-1).canUndo, true);
  assert.equal(notifications.at(-1).canRedo, false);
});
