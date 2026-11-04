// DOM rendering module
import { state, DOM, EditSession } from './state.js?v=14';
import { ImageLoader } from './image-loader.js?v=14';
import { ErrorHandler } from './errors.js?v=14';
import { validateManualDocument } from './manual-data.js?v=14';
import { fitRenderedManual } from './text-fit-dom.js?v=14';
import { buildCatalogView } from './catalog-view.js?v=14';
import { formatPageIndicator } from './reader-controls.js?v=14';
import { reduceViewerState } from './viewer-state.js?v=14';

let pendingFitFrame = null;
const RESIZE_HANDLES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];

export function attachResizeHandles(overlay) {
  if (!overlay || overlay.querySelector('.resize-handle')) return;
  for (const handle of RESIZE_HANDLES) {
    const resizeHandle = document.createElement('span');
    resizeHandle.className = `resize-handle resize-handle--${handle}`;
    resizeHandle.dataset.handle = handle;
    resizeHandle.setAttribute('aria-hidden', 'true');
    overlay.appendChild(resizeHandle);
  }
}

export function scheduleManualFit() {
  if (!state.currentManual || !DOM.pagePanel) return;

  state.fitGeneration += 1;
  const generation = state.fitGeneration;
  if (pendingFitFrame !== null) {
    cancelAnimationFrame(pendingFitFrame);
  }

  pendingFitFrame = requestAnimationFrame(async () => {
    pendingFitFrame = null;
    const results = await fitRenderedManual({
      root: DOM.pagePanel,
      manual: state.currentManual,
      generation,
      isCurrentGeneration: (candidate) => candidate === state.fitGeneration,
      readerScale: state.reader.scale
    });
    if (generation === state.fitGeneration) {
      state.fitResults = results;
    }
  });
}

// Render manual list
export function renderManualList(filteredManuals) {
  const grid = DOM.manualsGrid;
  const statsDisplay = document.getElementById('statsDisplay');
  const noResults = document.getElementById('noResults');

  grid.replaceChildren();
  const view = buildCatalogView(filteredManuals, state.tagsData?.tags);

  if (view.status === 'empty') {
    grid.classList.add('hidden');
    noResults.classList.remove('hidden');
    statsDisplay.textContent = view.countLabel;
    return;
  }

  grid.classList.remove('hidden');
  noResults.classList.add('hidden');

  view.cards.forEach((manual) => {
    const card = document.createElement('article');
    card.className = 'card';
    card.tabIndex = 0;
    card.setAttribute('role', 'link');
    card.setAttribute('aria-label', `Open ${manual.title}`);

    const artwork = document.createElement('div');
    artwork.className = 'card-artwork';
    const image = document.createElement('img');
    image.src = manual.thumbnail;
    image.alt = manual.title;
    image.className = 'thumbnail';
    image.loading = 'lazy';
    artwork.appendChild(image);

    const content = document.createElement('div');
    content.className = 'card-content';
    const title = document.createElement('h2');
    title.className = 'card-title';
    title.textContent = manual.title;
    content.appendChild(title);

    if (manual.tags.length > 0) {
      const tagList = document.createElement('div');
      tagList.className = 'card-tags';
      for (const tag of manual.tags) {
        const label = document.createElement('span');
        label.className = 'tag';
        const marker = document.createElement('span');
        marker.className = 'tag-marker';
        marker.style.backgroundColor = tag.markerColor;
        label.append(marker, document.createTextNode(tag.label));
        tagList.appendChild(label);
      }
      content.appendChild(tagList);
    }

    const meta = document.createElement('div');
    meta.className = 'card-meta';
    meta.textContent = `${manual.pageCount} pages · ${manual.blockCount} corrected blocks`;
    content.appendChild(meta);

    if (manual.originalUrl) {
      const original = document.createElement('a');
      original.href = manual.originalUrl;
      original.target = '_blank';
      original.rel = 'noreferrer';
      original.className = 'card-original';
      original.textContent = 'Original PDF ↗';
      content.appendChild(original);
    }

    card.append(artwork, content);
    const openManual = () => {
      import('./navigation.js?v=14').then(({ navigate }) => {
        navigate('viewer', manual.manualName);
      });
    };
    card.addEventListener('click', (event) => {
      if (!event.target.closest('a')) openManual();
    });
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.target.closest('a')) openManual();
    });

    grid.appendChild(card);
  });

  statsDisplay.textContent = view.countLabel;
}

// Render tag filters
export function renderTagFilters() {
  if (!state.tagsData) return;

  const tagFilters = document.getElementById('tagFilters');
  tagFilters.replaceChildren();

  Object.entries(state.tagsData.tags).forEach(([tagId, tag]) => {
    const tagButton = document.createElement('button');
    tagButton.className = 'tag-filter';
    const marker = document.createElement('span');
    marker.className = 'tag-marker';
    marker.style.backgroundColor = /^#[0-9a-f]{6}$/i.test(tag.color) ? tag.color : '#98a2b3';
    tagButton.append(marker, document.createTextNode(tag.label));
    tagButton.dataset.tagId = tagId; // Store tag ID on button
    tagButton.onclick = () => {
      // Import toggleTagFilter dynamically
      import('./navigation.js?v=14').then(({ toggleTagFilter }) => {
        toggleTagFilter(tagId);
      });
    };
    tagFilters.appendChild(tagButton);
  });
}

// Render pages
export function renderPages(manualName, renderToken) {
  // Check if render was cancelled
  if (renderToken !== undefined && renderToken !== state.currentRenderToken) {
    return;
  }

  const pagePanel = document.getElementById('pagePanel');
  pagePanel.innerHTML = '';

  // Use DocumentFragment for batch DOM insertion
  const fragment = document.createDocumentFragment();

  state.currentManual.pages.forEach((page, pageIdx) => {
    const pageDiv = document.createElement('div');
    pageDiv.className = 'page';
    pageDiv.id = `page-${pageIdx}`;

    const img = document.createElement('img');
    // Use lazy loading for images beyond the first 2 pages
    if (pageIdx < 2) {
      img.src = `../manuals/${manualName}/${page.image}`;
    } else {
      img.dataset.src = `../manuals/${manualName}/${page.image}`;
      img.src =
        'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"%3E%3C/svg%3E';
    }
    img.alt = `Page ${pageIdx + 1}`;
    img.onload = function () {
      renderOverlays(pageDiv, page, pageIdx);
      scheduleManualFit();
    };

    // Setup lazy loading
    if (pageIdx >= 2) {
      ImageLoader.observe(img);
    }

    const label = document.createElement('div');
    label.className = 'page-label';
    label.textContent = `Page ${pageIdx + 1}`;

    pageDiv.appendChild(img);
    pageDiv.appendChild(label);
    fragment.appendChild(pageDiv);
  });

  pagePanel.appendChild(fragment);
  state.viewer = reduceViewerState(state.viewer, { type: 'set-current-page', page: 0 });
  DOM.pageIndicator.textContent = formatPageIndicator(0, state.currentManual.pages.length);
  DOM.pageJumpInput.value = '1';
  DOM.pageJumpInput.max = String(state.currentManual.pages.length);
}

// Render overlays
export function renderOverlays(pageDiv, page, pageIdx) {
  const fragment = document.createDocumentFragment();

  page.blocks.forEach((block, blockIdx) => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.textContent = block.translation;
    overlay.dataset.pageId = pageIdx;
    overlay.dataset.blockId = blockIdx;
    const selected =
      state.viewer.selectedBlock?.pageIndex === pageIdx &&
      state.viewer.selectedBlock?.blockIndex === blockIdx;
    if (selected) {
      overlay.classList.add('is-selected');
    }

    // bbox is [x, y, w, h] normalized (0-1), convert to percentage
    overlay.style.left = block.bbox[0] * 100 + '%';
    overlay.style.top = block.bbox[1] * 100 + '%';
    overlay.style.width = block.bbox[2] * 100 + '%';
    overlay.style.height = block.bbox[3] * 100 + '%';

    if (!state.viewer.correctedTextVisible) {
      overlay.style.display = 'none';
    }

    if (selected) attachResizeHandles(overlay);

    fragment.appendChild(overlay);
  });

  pageDiv.appendChild(fragment);
}

// Render text list
export function renderTextList() {
  DOM.textList.innerHTML = '';

  const fragment = document.createDocumentFragment();

  state.currentManual.pages.forEach((page, pageIdx) => {
    page.blocks.forEach((block, blockIdx) => {
      const item = document.createElement('div');
      item.className = 'text-item';
      item.dataset.pageId = pageIdx;
      item.dataset.blockId = blockIdx;

      // Drawer rows locate a block; editing lives in the contextual inspector.
      const header = document.createElement('div');
      header.className = 'text-item-header';

      const original = document.createElement('div');
      original.className = 'text-original';
      original.textContent = block.text;

      header.appendChild(original);

      const translation = document.createElement('div');
      translation.className = 'text-translation';
      translation.textContent = block.translation;
      item.appendChild(header);
      item.appendChild(translation);

      fragment.appendChild(item);
    });
  });

  DOM.textList.appendChild(fragment);
}

// Highlight block
export function highlightBlock(pageIdx, blockIdx) {
  document.querySelectorAll('.highlight').forEach((el) => el.classList.remove('highlight'));

  document
    .querySelectorAll(`[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`)
    .forEach((el) => {
      el.classList.add('highlight');

      if (el.classList.contains('text-item')) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });
}

// Clear highlights
export function clearHighlights() {
  document.querySelectorAll('.highlight').forEach((el) => el.classList.remove('highlight'));
}

// Load manual
export async function loadManual(manualName) {
  const renderToken = ++state.currentRenderToken;

  // Store the manual name
  state.currentManualName = manualName;

  // Reset edit state
  EditSession.clear();
  hideEditButtons();

  try {
    const response = await fetch(`../manuals/${manualName}/translations.json`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();

    if (renderToken !== state.currentRenderToken) {
      // Render cancelled - user navigated away
      return;
    }

    validateManualDocument(data);

    state.currentManual = data;
    state.sourceManual = structuredClone(data);
    state.pendingDraft = state.draftStore?.load(manualName, state.sourceManual) ?? null;

    const displayName = state.currentManual.meta.manual_name || state.currentManual.meta.source;
    document.title = displayName;
    document.getElementById('manualTitle').textContent = displayName;

    const sourceLink = document.getElementById('sourceLink');
    if (state.currentManual.meta.source_url) {
      sourceLink.href = state.currentManual.meta.source_url;
      sourceLink.classList.remove('hidden');
    } else {
      sourceLink.classList.add('hidden');
    }

    renderPages(manualName, renderToken);
    renderTextList();
  } catch (err) {
    ErrorHandler.network(err, `../manuals/${manualName}/translations.json`);
    const failure = document.createElement('p');
    failure.className = 'viewer-error';
    failure.textContent = `Failed to load: ${err.message}`;
    document.getElementById('pagePanel').replaceChildren(failure);
  }
}

// Helper functions
export function showEditButtons() {
  DOM.downloadEditsBtn.classList.remove('hidden');
  DOM.submitGitHubBtn.classList.remove('hidden');
}

export function hideEditButtons() {
  DOM.downloadEditsBtn.classList.add('hidden');
  DOM.submitGitHubBtn.classList.add('hidden');
}
