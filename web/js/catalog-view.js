const FALLBACK_MARKER = '#98a2b3';

function markerColor(value) {
  return /^#[0-9a-f]{6}$/i.test(value ?? '') ? value : FALLBACK_MARKER;
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export function buildCatalogCard(manual, tagsById = {}) {
  return {
    manualName: manual.name,
    title: manual.name.replaceAll('-', ' '),
    pageCount: Number(manual.pages) || 0,
    blockCount: Number(manual.blocks) || 0,
    thumbnail: `../manuals/${manual.thumbnail}`,
    originalUrl: safeExternalUrl(manual.source_url),
    tags: (manual.tags ?? [])
      .map((tagId) => tagsById[tagId])
      .filter(Boolean)
      .map((tag) => ({
        label: tag.label,
        markerColor: markerColor(tag.color)
      }))
  };
}

export function buildCatalogView(manuals, tagsById = {}, { error = null } = {}) {
  if (error) {
    return {
      status: 'failed',
      countLabel: 'Catalog unavailable',
      message: error,
      cards: []
    };
  }
  const cards = manuals.map((manual) => buildCatalogCard(manual, tagsById));
  return {
    status: cards.length === 0 ? 'empty' : 'ready',
    countLabel: `${cards.length} manual${cards.length === 1 ? '' : 's'}`,
    cards
  };
}
