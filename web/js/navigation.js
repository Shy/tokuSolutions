// Navigation and routing module
import { state, DOM, EditSession } from './state.js?v=14';
import { renderManualList, loadManual } from './renderer.js?v=14';
import { filterManuals as filterCatalogManuals } from './catalog-model.js?v=14';
import { reduceViewerState } from './viewer-state.js?v=14';
import { formatPageIndicator } from './reader-controls.js?v=14';

export function manualNameFromHash(hash) {
  const value = hash.replace(/^#/, '');
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

// Navigate between views
export function navigate(view, manualName = null) {
  // Check for unsaved changes
  if (!EditSession.canNavigateAway()) {
    return;
  }

  // Hide all views
  document.getElementById('homeView').classList.add('hidden');
  document.getElementById('viewerView').classList.add('hidden');

  if (view === 'home') {
    document.getElementById('homeView').classList.remove('hidden');
    window.history.pushState({ view: 'home' }, '', '#');
    EditSession.clear();
  } else if (view === 'viewer' && manualName) {
    document.getElementById('viewerView').classList.remove('hidden');
    window.history.pushState({ view: 'viewer', manual: manualName }, '', `#${manualName}`);
    loadManual(manualName);
  }
}

// Toggle translations
export function toggleTranslations() {
  state.viewer = reduceViewerState(state.viewer, {
    type: 'set-corrected-text-visible',
    visible: !state.viewer.correctedTextVisible
  });
  document.querySelectorAll('.overlay').forEach((overlay) => {
    overlay.style.display = state.viewer.correctedTextVisible ? 'flex' : 'none';
  });
  DOM.translationsBtn.textContent = `Corrected text: ${
    state.viewer.correctedTextVisible ? 'On' : 'Off'
  }`;
  DOM.translationsBtn.setAttribute('aria-pressed', String(state.viewer.correctedTextVisible));
}

// Filter manuals
export function filterManuals() {
  const filteredManuals = filterCatalogManuals(
    state.manuals,
    DOM.searchInput.value,
    state.selectedTags
  );

  renderManualList(filteredManuals);
}

// Toggle tag filter
export function toggleTagFilter(tagId) {
  if (state.selectedTags.has(tagId)) {
    state.selectedTags.delete(tagId);
  } else {
    state.selectedTags.add(tagId);
  }

  // Update UI
  document.querySelectorAll('.tag-filter').forEach((btn) => {
    const btnTagId = btn.dataset.tagId;

    if (btnTagId && state.selectedTags.has(btnTagId)) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  filterManuals();
}

// Handle browser back/forward
export function handlePopState(event) {
  if (event.state) {
    if (event.state.view === 'home') {
      navigate('home');
    } else if (event.state.view === 'viewer' && event.state.manual) {
      navigate('viewer', event.state.manual);
    }
  } else {
    // No state - check hash
    const hash = manualNameFromHash(window.location.hash);
    if (hash) {
      navigate('viewer', hash);
    } else {
      navigate('home');
    }
  }
}

// Navigate to page
export function navigatePage(direction) {
  if (!state.currentManual) return;

  const pagePanel = DOM.pagePanel;
  const pages = pagePanel.querySelectorAll('.page');

  if (pages.length === 0) return;

  // Find current visible page
  let currentPageIdx = 0;
  const panelRect = pagePanel.getBoundingClientRect();
  const panelCenter = panelRect.top + panelRect.height / 2;

  for (let i = 0; i < pages.length; i++) {
    const pageRect = pages[i].getBoundingClientRect();
    if (pageRect.top <= panelCenter && pageRect.bottom >= panelCenter) {
      currentPageIdx = i;
      break;
    }
  }

  // Navigate
  let targetIdx = currentPageIdx;
  if (direction === 'prev' && currentPageIdx > 0) {
    targetIdx = currentPageIdx - 1;
  } else if (direction === 'next' && currentPageIdx < pages.length - 1) {
    targetIdx = currentPageIdx + 1;
  }

  if (targetIdx !== currentPageIdx) {
    pages[targetIdx].scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

export function jumpToPage(pageIndex) {
  if (!state.currentManual) return false;
  const pages = DOM.pagePanel.querySelectorAll('.page');
  const target = pages[pageIndex];
  if (!target) return false;
  target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return true;
}

// Update page indicator
export function updatePageIndicator() {
  if (!state.currentManual) return;

  const pagePanel = DOM.pagePanel;
  const pages = pagePanel.querySelectorAll('.page');

  if (pages.length === 0) return;

  const panelRect = pagePanel.getBoundingClientRect();
  const panelCenter = panelRect.top + panelRect.height / 2;

  for (let i = 0; i < pages.length; i++) {
    const pageRect = pages[i].getBoundingClientRect();
    if (pageRect.top <= panelCenter && pageRect.bottom >= panelCenter) {
      state.viewer = reduceViewerState(state.viewer, { type: 'set-current-page', page: i });
      DOM.pageIndicator.textContent = formatPageIndicator(i, pages.length);
      if (DOM.pageJumpInput) {
        DOM.pageJumpInput.value = String(i + 1);
        DOM.pageJumpInput.max = String(pages.length);
      }
      return;
    }
  }
}
