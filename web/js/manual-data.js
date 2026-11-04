export class ManualDataValidationError extends TypeError {
  constructor(path, message) {
    super(`${path} ${message}`);
    this.name = 'ManualDataValidationError';
    this.path = path;
  }
}

function requireObject(value, path) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new ManualDataValidationError(path, 'must be an object');
  }
}

function requireString(value, path) {
  if (typeof value !== 'string') {
    throw new ManualDataValidationError(path, 'must be a string');
  }
}

function validateBbox(bbox, path) {
  if (!Array.isArray(bbox) || bbox.length !== 4 || bbox.some((value) => !Number.isFinite(value))) {
    throw new ManualDataValidationError(path, 'must contain four finite numbers');
  }
}

/**
 * Validate the persisted manual document shape without changing the document.
 *
 * @param {unknown} manual
 * @returns {object}
 * @throws {ManualDataValidationError}
 */
export function validateManualDocument(manual) {
  requireObject(manual, 'manual');
  requireObject(manual.meta, 'manual.meta');

  if (!Array.isArray(manual.pages)) {
    throw new ManualDataValidationError('manual.pages', 'must be an array');
  }

  manual.pages.forEach((page, pageIdx) => {
    const pagePath = `manual.pages[${pageIdx}]`;
    requireObject(page, pagePath);
    requireString(page.image, `${pagePath}.image`);

    if (!Array.isArray(page.blocks)) {
      throw new ManualDataValidationError(`${pagePath}.blocks`, 'must be an array');
    }

    page.blocks.forEach((block, blockIdx) => {
      const blockPath = `${pagePath}.blocks[${blockIdx}]`;
      requireObject(block, blockPath);
      requireString(block.text, `${blockPath}.text`);
      requireString(block.translation, `${blockPath}.translation`);
      validateBbox(block.bbox, `${blockPath}.bbox`);
    });
  });

  return manual;
}

/**
 * Create a detached manual containing only fields persisted in translations.json.
 *
 * @param {unknown} manual
 * @returns {{meta: object, pages: Array<object>}}
 */
export function toPersistedManual(manual) {
  validateManualDocument(manual);

  return {
    meta: structuredClone(manual.meta),
    pages: manual.pages.map((page) => ({
      image: page.image,
      blocks: page.blocks.map((block) => ({
        text: block.text,
        translation: block.translation,
        bbox: [...block.bbox]
      }))
    }))
  };
}

/**
 * Serialize only the persisted manual document for download or GitHub copy.
 *
 * @param {unknown} manual
 * @returns {string}
 */
export function serializeManual(manual) {
  return JSON.stringify(toPersistedManual(manual), null, 2);
}
