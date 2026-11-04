import { toPersistedManual } from './manual-data.js?v=14';

export const DRAFT_SCHEMA_VERSION = 1;
const DRAFT_PREFIX = `tokusolutions:draft:v${DRAFT_SCHEMA_VERSION}:`;

export function draftKey(manualName) {
  return `${DRAFT_PREFIX}${encodeURIComponent(manualName)}`;
}

export function fingerprintManual(manual) {
  const serialized = JSON.stringify(toPersistedManual(manual));
  let hash = 0x811c9dc5;
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function storageError(error) {
  return error?.name === 'QuotaExceededError' ? 'quota' : 'storage';
}

export function createDraftStore({ storage, clock = () => new Date().toISOString() } = {}) {
  return {
    save(manualName, sourceManual, editedManual) {
      if (!storage) return { ok: false, error: 'unavailable' };
      const envelope = {
        version: DRAFT_SCHEMA_VERSION,
        manualName,
        sourceFingerprint: fingerprintManual(sourceManual),
        savedAt: clock(),
        manual: toPersistedManual(editedManual)
      };
      try {
        storage.setItem(draftKey(manualName), JSON.stringify(envelope));
        return { ok: true, envelope: structuredClone(envelope) };
      } catch (error) {
        return { ok: false, error: storageError(error) };
      }
    },

    load(manualName, sourceManual) {
      if (!storage) return { ok: false, status: 'none', error: 'unavailable' };
      let serialized;
      try {
        serialized = storage.getItem(draftKey(manualName));
      } catch {
        return { ok: false, status: 'none', error: 'storage' };
      }
      if (serialized === null) return { ok: true, status: 'none', manual: null };

      let envelope;
      try {
        envelope = JSON.parse(serialized);
      } catch {
        return { ok: false, status: 'invalid', error: 'parse', manual: null };
      }

      if (
        envelope.version !== DRAFT_SCHEMA_VERSION ||
        envelope.manualName !== manualName ||
        !envelope.manual ||
        typeof envelope.sourceFingerprint !== 'string'
      ) {
        return { ok: false, status: 'invalid', error: 'schema', manual: null };
      }

      if (envelope.sourceFingerprint !== fingerprintManual(sourceManual)) {
        return {
          ok: true,
          status: 'mismatch',
          manual: null,
          envelope: structuredClone(envelope)
        };
      }

      return {
        ok: true,
        status: 'compatible',
        manual: structuredClone(envelope.manual),
        envelope: structuredClone(envelope)
      };
    },

    discard(manualName) {
      if (!storage) return { ok: false, error: 'unavailable' };
      try {
        storage.removeItem(draftKey(manualName));
        return { ok: true };
      } catch {
        return { ok: false, error: 'storage' };
      }
    }
  };
}

export function createDraftAutosave({
  save,
  delay = 300,
  schedule = setTimeout,
  cancel = clearTimeout,
  onStatus = () => {}
}) {
  let pending = null;
  return {
    queue(...args) {
      if (pending !== null) cancel(pending);
      pending = schedule(() => {
        pending = null;
        const result = save(...args);
        onStatus(
          result.ok
            ? { status: 'saved', envelope: result.envelope }
            : { status: 'error', error: result.error }
        );
      }, delay);
    },
    cancel() {
      if (pending !== null) cancel(pending);
      pending = null;
    }
  };
}
