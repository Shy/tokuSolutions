export const READER_PREFERENCES_VERSION = 1;
export const READER_SCALE_STEPS = Object.freeze([0.85, 1, 1.15, 1.3]);
export const DEFAULT_READER_FONT_SIZE = 14;

export function createReaderPreferences(value = {}) {
  return {
    correctedTextVisible: value.correctedTextVisible !== false,
    scale: READER_SCALE_STEPS.includes(value.scale) ? value.scale : 1
  };
}

export function toggleCorrectedText(preferences) {
  return {
    ...preferences,
    correctedTextVisible: !preferences.correctedTextVisible
  };
}

export function adjustReaderScale(currentScale, direction) {
  const currentIndex = Math.max(0, READER_SCALE_STEPS.indexOf(currentScale));
  const nextIndex = Math.min(
    READER_SCALE_STEPS.length - 1,
    Math.max(0, currentIndex + Math.sign(direction))
  );
  return READER_SCALE_STEPS[nextIndex];
}

export function serializeReaderPreferences(preferences) {
  return JSON.stringify({
    version: READER_PREFERENCES_VERSION,
    correctedTextVisible: preferences.correctedTextVisible,
    scale: preferences.scale
  });
}

export function parseReaderPreferences(serialized) {
  try {
    const value = JSON.parse(serialized);
    if (value.version !== READER_PREFERENCES_VERSION) return createReaderPreferences();
    return createReaderPreferences(value);
  } catch {
    return createReaderPreferences();
  }
}

export function pageJumpIndex(value, pageCount) {
  const page = Number(value);
  if (!Number.isInteger(page) || page < 1 || page > pageCount) return null;
  return page - 1;
}

export function formatPageIndicator(pageIndex, pageCount) {
  return `Page ${pageIndex + 1} of ${pageCount}`;
}

export function requiresLargeTextPopover({ status, fontSize, preferredFontSize }) {
  if (status === 'missing') return false;
  return status === 'overflow' || (status === 'fits' && fontSize < preferredFontSize);
}
