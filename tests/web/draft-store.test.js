import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DRAFT_SCHEMA_VERSION,
  createDraftAutosave,
  createDraftStore,
  draftKey,
  fingerprintManual
} from '../../web/js/draft-store.js';
import { createEditSession } from '../../web/js/edit-session.js';
import { serializeManual } from '../../web/js/manual-data.js';

function manualFixture(translation = 'Alpha') {
  return {
    meta: { manual_name: 'Draft Test', source: 'source.pdf' },
    pages: [
      {
        image: 'page.webp',
        blocks: [{ text: 'A', translation, bbox: [0.1, 0.1, 0.2, 0.2] }]
      }
    ]
  };
}

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
    values
  };
}

test('draft keys are versioned and per manual', () => {
  assert.equal(draftKey('Manual A'), 'tokusolutions:draft:v1:Manual%20A');
  assert.notEqual(draftKey('Manual A'), draftKey('Manual B'));
  assert.equal(DRAFT_SCHEMA_VERSION, 1);
});

test('draft store saves, restores, and discards compatible drafts', () => {
  const storage = memoryStorage();
  const store = createDraftStore({ storage, clock: () => '2026-07-24T12:00:00.000Z' });
  const source = manualFixture();
  const edited = manualFixture('Changed');

  assert.equal(store.save('Manual A', source, edited).ok, true);
  const restored = store.load('Manual A', source);
  assert.equal(restored.status, 'compatible');
  assert.equal(restored.manual.pages[0].blocks[0].translation, 'Changed');
  assert.equal(restored.envelope.savedAt, '2026-07-24T12:00:00.000Z');
  assert.equal(store.discard('Manual A').ok, true);
  assert.equal(store.load('Manual A', source).status, 'none');
});

test('source fingerprint mismatch warns without auto-merging', () => {
  const storage = memoryStorage();
  const store = createDraftStore({ storage });
  const source = manualFixture();
  store.save('Manual A', source, manualFixture('Changed'));
  const changedSource = manualFixture();
  changedSource.pages[0].blocks[0].text = 'Different OCR';

  const result = store.load('Manual A', changedSource);
  assert.equal(result.status, 'mismatch');
  assert.equal(result.manual, null);
  assert.notEqual(result.envelope.sourceFingerprint, fingerprintManual(changedSource));
});

test('storage quota, parse, and unavailable failures are non-blocking', () => {
  const quotaStore = createDraftStore({
    storage: {
      getItem: () => null,
      setItem: () => {
        const error = new Error('full');
        error.name = 'QuotaExceededError';
        throw error;
      },
      removeItem: () => {}
    }
  });
  assert.deepEqual(quotaStore.save('A', manualFixture(), manualFixture()), {
    ok: false,
    error: 'quota'
  });

  const parseStore = createDraftStore({
    storage: { getItem: () => '{bad', setItem: () => {}, removeItem: () => {} }
  });
  assert.equal(parseStore.load('A', manualFixture()).error, 'parse');

  const unavailableStore = createDraftStore({ storage: null });
  assert.equal(unavailableStore.load('A', manualFixture()).error, 'unavailable');
  assert.equal(unavailableStore.discard('A').error, 'unavailable');
});

test('restored drafts start with empty history and serialize without envelope data', () => {
  const storage = memoryStorage();
  const store = createDraftStore({ storage });
  const source = manualFixture();
  store.save('A', source, manualFixture('Changed'));
  const restored = store.load('A', source);
  const session = createEditSession(restored.manual);

  assert.equal(session.undoDepth, 0);
  assert.equal(session.redoDepth, 0);
  assert.equal(session.dirty, false);
  const json = serializeManual(session.manual);
  assert.equal(json.includes('sourceFingerprint'), false);
  assert.equal(json.includes('savedAt'), false);
});

test('debounced autosave emits saved and failure notifications', () => {
  const callbacks = [];
  const notifications = [];
  const saves = [];
  const autosave = createDraftAutosave({
    delay: 50,
    schedule: (callback) => {
      callbacks.push(callback);
      return callbacks.length;
    },
    cancel: () => {},
    save: (...args) => {
      saves.push(args);
      return { ok: saves.length === 1, error: 'quota' };
    },
    onStatus: (status) => notifications.push(status)
  });

  autosave.queue('A', manualFixture(), manualFixture('One'));
  autosave.queue('A', manualFixture(), manualFixture('Two'));
  callbacks.at(-1)();
  autosave.queue('A', manualFixture(), manualFixture('Three'));
  callbacks.at(-1)();

  assert.equal(saves.length, 2);
  assert.deepEqual(
    notifications.map((item) => item.status),
    ['saved', 'error']
  );
});
