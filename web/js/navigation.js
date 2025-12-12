// Navigation and routing module
import { state, DOM, EditSession } from './state.js';
import { renderManualList, loadManual } from './renderer.js';

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

// Toggle overlays
export function toggleOverlays() {
  state.showOverlays = !state.showOverlays;
  document.querySelectorAll('.overlay').forEach((overlay) => {
    overlay.style.display = state.showOverlays ? 'flex' : 'none';
  });
  DOM.overlaysBtn.innerHTML = state.showOverlays
    ? '<i class="fas fa-eye"></i> Hide Overlays'
    : '<i class="fas fa-eye-slash"></i> Show Overlays';
}

// Toggle translations
export function toggleTranslations() {
  state.showTranslations = !state.showTranslations;
  document.querySelectorAll('.overlay').forEach((overlay) => {
    const pageIdx = parseInt(overlay.dataset.pageId);
    const blockIdx = parseInt(overlay.dataset.blockId);
    const block = state.currentManual.pages[pageIdx].blocks[blockIdx];
    overlay.textContent = state.showTranslations ? block.translation : block.text;
  });
  document.querySelectorAll('.text-translation').forEach((el) => {
    el.style.display = state.showTranslations ? 'block' : 'none';
  });
  DOM.translationsBtn.innerHTML = state.showTranslations
    ? '<i class="fas fa-language"></i> Hide Translations'
    : '<i class="fas fa-language"></i> Show Translations';
}

// Toggle sidebar visibility
export function toggleSidebar() {
  state.showSidebar = !state.showSidebar;
  const textPanel = document.querySelector('.text-panel');

  if (state.showSidebar) {
    textPanel.classList.remove('sidebar-hidden');
    textPanel.style.display = 'flex';
  } else {
    textPanel.classList.add('sidebar-hidden');
    textPanel.style.display = 'none';
  }
}

// Filter manuals
export function filterManuals() {
  const searchTerm = DOM.searchInput.value.toLowerCase().trim();

  let filteredManuals = state.manuals;

  // Filter by search term
  if (searchTerm) {
    filteredManuals = filteredManuals.filter((manual) => {
      const displayName = manual.name.replace(/-/g, ' ').toLowerCase();
      const source = manual.source?.toLowerCase() || '';
      return displayName.includes(searchTerm) || source.includes(searchTerm);
    });
  }

  // Filter by selected tags
  if (state.selectedTags.size > 0) {
    filteredManuals = filteredManuals.filter((manual) => {
      if (!manual.tags || manual.tags.length === 0) return false;
      return Array.from(state.selectedTags).every((tagId) => manual.tags.includes(tagId));
    });
  }

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
    const hash = window.location.hash.slice(1);
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
      DOM.pageIndicator.textContent = `Page ${i + 1} of ${pages.length}`;
      return;
    }
  }
}
