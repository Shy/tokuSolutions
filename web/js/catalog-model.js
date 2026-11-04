function normalizeSearchText(value) {
  return String(value ?? '')
    .toLowerCase()
    .replace(/[-\s]+/g, ' ')
    .trim();
}

/**
 * Filter manuals by normalized name/source text and all selected tags.
 *
 * @param {Array<object>} manuals
 * @param {string} query
 * @param {Iterable<string>} selectedTags
 * @returns {Array<object>}
 */
export function filterManuals(manuals, query = '', selectedTags = []) {
  const searchTerm = normalizeSearchText(query);
  const requiredTags = [...selectedTags];

  return manuals.filter((manual) => {
    if (searchTerm) {
      const name = normalizeSearchText(manual.name);
      const source = normalizeSearchText(manual.source);
      if (!name.includes(searchTerm) && !source.includes(searchTerm)) {
        return false;
      }
    }

    if (requiredTags.length > 0) {
      const manualTags = Array.isArray(manual.tags) ? manual.tags : [];
      if (!requiredTags.every((tagId) => manualTags.includes(tagId))) {
        return false;
      }
    }

    return true;
  });
}
