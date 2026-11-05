// Main application entry point - modular architecture
import { state, DOM, EditSession } from './js/state.js?v=17';
import { ImageLoader } from './js/image-loader.js?v=17';
import { debounce, throttle } from './js/utils.js?v=17';
import { UI_TIMINGS } from './js/config.js?v=17';
import {
  renderManualList,
  renderTagFilters,
  highlightBlock,
  clearHighlights,
  scheduleManualFit
} from './js/renderer.js?v=17';
import {
  commitBboxGesture,
  createTextBlock,
  deleteSelectedBlock,
  redoLastEdit,
  saveInspectorBbox,
  saveInspectorTranslation,
  syncInspectorBbox,
  undoLastEdit
} from './js/editor.js?v=17';
import {
  navigate,
  toggleTranslations,
  filterManuals,
  handlePopState,
  navigatePage,
  jumpToPage,
  manualNameFromHash,
  updatePageIndicator
} from './js/navigation.js?v=17';
import { submitToGitHub, downloadJSON } from './js/github.js?v=17';
import { createViewerState, reduceViewerState } from './js/viewer-state.js?v=17';
import {
  DEFAULT_READER_FONT_SIZE,
  adjustReaderScale,
  createReaderPreferences,
  pageJumpIndex,
  parseReaderPreferences,
  requiresLargeTextPopover,
  serializeReaderPreferences
} from './js/reader-controls.js?v=17';
import { createDraftAutosave, createDraftStore } from './js/draft-store.js?v=17';
import {
  beginBboxGesture,
  cancelBboxGesture,
  commitBboxGesture as finalizeBboxGesture,
  updateBboxGesture
} from './js/bbox-interaction.js?v=17';
import { createPrintPreparation } from './js/print-preparation.js?v=17';
import { fitPrintDocument, renderPrintDocument } from './js/print-renderer.js?v=17';

const finePointerQuery = window.matchMedia('(pointer: fine)');
const READER_PREFERENCES_KEY = 'tokusolutions:reader-preferences';
const PRINT_PREFLIGHT_KEY = 'tokusolutions:print-preflight-dismissed:v1';
let activeBboxGesture = null;
let printPreparation = null;

function gesturePointer(event, pageRect) {
  return {
    x: (event.clientX - pageRect.left) / pageRect.width,
    y: (event.clientY - pageRect.top) / pageRect.height
  };
}

function applyBboxPreview(overlay, bbox) {
  [overlay.style.left, overlay.style.top, overlay.style.width, overlay.style.height] = bbox.map(
    (value) => `${value * 100}%`
  );
  syncInspectorBbox(bbox);
}

function currentCapability() {
  return {
    width: window.innerWidth,
    finePointer: finePointerQuery.matches
  };
}

function renderViewerState() {
  const viewerView = document.getElementById('viewerView');
  viewerView.dataset.viewerMode = state.viewer.mode;
  viewerView.classList.toggle('is-editing', state.viewer.mode === 'editing');

  DOM.editTranslationsBtn.classList.toggle(
    'hidden',
    !state.viewer.canEdit || state.viewer.mode !== 'reading'
  );
  DOM.exitEditingBtn.classList.toggle('hidden', state.viewer.mode !== 'editing');
  const hasEdits = state.editSession?.dirty ?? false;
  DOM.undoBtn.classList.toggle(
    'hidden',
    state.viewer.mode !== 'editing' || !state.editSession?.canUndo
  );
  DOM.redoBtn.classList.toggle(
    'hidden',
    state.viewer.mode !== 'editing' || !state.editSession?.canRedo
  );
  DOM.downloadEditsBtn.classList.toggle('hidden', state.viewer.mode !== 'editing' || !hasEdits);
  DOM.submitGitHubBtn.classList.toggle('hidden', state.viewer.mode !== 'editing' || !hasEdits);
  DOM.discardDraftBtn.classList.toggle(
    'hidden',
    state.viewer.mode !== 'editing' ||
      (!state.editSession?.dirty && state.pendingDraft?.status !== 'compatible')
  );
  DOM.translationsBtn.textContent = `Corrected text: ${
    state.viewer.correctedTextVisible ? 'On' : 'Off'
  }`;
  DOM.translationsBtn.setAttribute('aria-pressed', String(state.viewer.correctedTextVisible));
}

function setDraftStatus(message, isError = false) {
  state.draftStatus = { message, isError };
  DOM.draftStatus.textContent = message;
  DOM.draftStatus.classList.toggle('is-error', isError);
}

function setPrintStatus(message, isError = false) {
  DOM.printStatus.textContent = message;
  DOM.printStatus.classList.toggle('is-error', isError);
}

function preloadPrintImage(page) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = async () => {
      try {
        await image.decode();
        resolve({
          width: image.naturalWidth,
          height: image.naturalHeight,
          decoded: true,
          src: image.src
        });
      } catch (error) {
        reject(error);
      }
    };
    image.onerror = () => reject(new Error(`Could not load ${page.image}`));
    image.src = `../manuals/${state.currentManualName}/${page.image}`;
  });
}

function showPrintWarnings(warnings) {
  DOM.printWarningsList.innerHTML = '';
  for (const warning of warnings) {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'warning-link';
    button.textContent = `Page ${warning.pageIndex + 1}, block ${warning.blockIndex + 1}: ${
      warning.status
    }`;
    button.addEventListener('click', () => {
      DOM.printWarningsDialog.close();
      document
        .querySelector(
          `.overlay[data-page-id="${warning.pageIndex}"][data-block-id="${warning.blockIndex}"]`
        )
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    item.appendChild(button);
    DOM.printWarningsList.appendChild(item);
  }
  DOM.printWarningsDialog.showModal();
}

async function runPrintPreparation(printAnyway = false) {
  setPrintStatus('Preparing all manual pages…');
  const result = await printPreparation.prepare(state.currentManual, { printAnyway });
  if (result.status === 'blocked') {
    setPrintStatus(`Print blocked: ${result.error} failed to load`, true);
  } else if (result.status === 'warning') {
    setPrintStatus(`${result.warnings.length} print item(s) need review`, true);
    showPrintWarnings(result.warnings);
  } else {
    setPrintStatus('Print preview opened');
  }
}

function printPreflightDismissed() {
  try {
    return localStorage.getItem(PRINT_PREFLIGHT_KEY) === 'true';
  } catch {
    return false;
  }
}

function openPrintPreflight() {
  document.getElementById('dismissPrintPreflight').checked = false;
  DOM.printPreflightDialog.showModal();
}

function prepareDraftForEditing() {
  if (state.pendingDraft?.status === 'compatible') {
    const restore = confirm(
      `A local draft from ${state.pendingDraft.envelope.savedAt} is available.\n\n` +
        'Choose OK to restore it, or Cancel to continue with the current source.'
    );
    if (restore) {
      state.currentManual = structuredClone(state.pendingDraft.manual);
      state.restoreDraftOnStart = true;
      setDraftStatus('Local draft restored');
    }
  } else if (state.pendingDraft?.status === 'mismatch') {
    alert(
      'A local draft exists, but the source manual has changed. It was not merged or restored.'
    );
    setDraftStatus('Draft needs review: source changed', true);
  } else if (state.pendingDraft?.error) {
    setDraftStatus(`Draft storage unavailable: ${state.pendingDraft.error}`, true);
  }
}

function dispatchViewer(action) {
  const wasEditing = state.viewer.mode === 'editing';
  state.viewer = reduceViewerState(state.viewer, action);

  if (wasEditing && state.viewer.mode === 'reading') {
    import('./js/editor.js?v=17').then(({ exitBlockEditMode }) => exitBlockEditMode());
  }

  renderViewerState();
}

function updateEditingCapability() {
  dispatchViewer({
    type: 'capability-changed',
    capability: currentCapability()
  });
  scheduleManualFit();
}

function saveReaderPreferences() {
  try {
    localStorage.setItem(READER_PREFERENCES_KEY, serializeReaderPreferences(state.reader));
  } catch (error) {
    console.warn('Could not save reader preferences', error);
  }
}

function loadReaderPreferences() {
  try {
    const stored = localStorage.getItem(READER_PREFERENCES_KEY);
    return stored ? parseReaderPreferences(stored) : createReaderPreferences();
  } catch {
    return createReaderPreferences();
  }
}

function setReaderScale(direction) {
  state.reader.scale = direction === 0 ? 1 : adjustReaderScale(state.reader.scale, direction);
  saveReaderPreferences();
  scheduleManualFit();
}

function closeLargeTextPopover() {
  DOM.largeTextPopover.classList.add('hidden');
  dispatchViewer({ type: 'open-popover', popover: null });
}

function maybeOpenLargeTextPopover(pageIndex, blockIndex, overlay) {
  const fontSize = Number.parseFloat(overlay.style.fontSize);
  if (
    !requiresLargeTextPopover({
      status: overlay.dataset.fitStatus,
      fontSize,
      preferredFontSize: DEFAULT_READER_FONT_SIZE * state.reader.scale
    })
  ) {
    return;
  }

  DOM.largeTextPopoverContent.textContent =
    state.currentManual.pages[pageIndex].blocks[blockIndex].translation;
  DOM.largeTextPopover.classList.remove('hidden');
  dispatchViewer({ type: 'open-popover', popover: 'large-text' });
}

// Initialize DOM cache and event listeners
function initializeApp() {
  // Cache DOM elements
  DOM.searchInput = document.getElementById('searchInput');
  DOM.manualsGrid = document.getElementById('manualsGrid');
  DOM.loadingIndicator = document.getElementById('loadingIndicator');
  DOM.pagePanel = document.getElementById('pagePanel');
  DOM.pageIndicator = document.getElementById('pageIndicator');
  DOM.textList = document.getElementById('textList');
  DOM.downloadEditsBtn = document.getElementById('downloadEditsBtn');
  DOM.submitGitHubBtn = document.getElementById('submitGitHubBtn');
  DOM.undoBtn = document.getElementById('undoBtn');
  DOM.redoBtn = document.getElementById('redoBtn');
  DOM.createBlockBtn = document.getElementById('createBlockBtn');
  DOM.prevPageBtn = document.getElementById('prevPageBtn');
  DOM.nextPageBtn = document.getElementById('nextPageBtn');
  DOM.translationsBtn = document.getElementById('translationsBtn');
  DOM.editTranslationsBtn = document.getElementById('editTranslationsBtn');
  DOM.exitEditingBtn = document.getElementById('exitEditingBtn');
  DOM.pageJumpInput = document.getElementById('pageJumpInput');
  DOM.decreaseTextSizeBtn = document.getElementById('decreaseTextSizeBtn');
  DOM.resetTextSizeBtn = document.getElementById('resetTextSizeBtn');
  DOM.increaseTextSizeBtn = document.getElementById('increaseTextSizeBtn');
  DOM.printManualBtn = document.getElementById('printManualBtn');
  DOM.printSettingsBtn = document.getElementById('printSettingsBtn');
  DOM.printStatus = document.getElementById('printStatus');
  DOM.printPreflightDialog = document.getElementById('printPreflightDialog');
  DOM.printWarningsDialog = document.getElementById('printWarningsDialog');
  DOM.printWarningsList = document.getElementById('printWarningsList');
  DOM.largeTextPopover = document.getElementById('largeTextPopover');
  DOM.largeTextPopoverContent = document.getElementById('largeTextPopoverContent');
  DOM.discardDraftBtn = document.getElementById('discardDraftBtn');
  DOM.draftStatus = document.getElementById('draftStatus');
  DOM.allTranslationsBtn = document.getElementById('allTranslationsBtn');
  DOM.translationsDrawer = document.getElementById('translationsDrawer');
  DOM.inspectorTranslation = document.getElementById('inspectorTranslation');
  DOM.inspectorBboxInputs = ['X', 'Y', 'W', 'H'].map((axis) =>
    document.getElementById(`inspectorBbox${axis}`)
  );
  DOM.deleteSelectedBlockBtn = document.getElementById('deleteSelectedBlockBtn');

  try {
    state.draftStore = createDraftStore({ storage: window.localStorage });
  } catch {
    state.draftStore = createDraftStore({ storage: null });
  }
  state.draftAutosave = createDraftAutosave({
    save: (...args) => state.draftStore.save(...args),
    onStatus(result) {
      setDraftStatus(
        result.status === 'saved' ? 'Draft saved locally' : `Draft save failed: ${result.error}`,
        result.status === 'error'
      );
    }
  });
  state.onEditSessionChange = (notification) => {
    if (['commit', 'undo', 'redo'].includes(notification.kind)) {
      state.draftAutosave.queue(
        state.currentManualName,
        state.sourceManual,
        state.editSession.manual
      );
    }
    renderViewerState();
  };
  printPreparation = createPrintPreparation({
    loadImage: preloadPrintImage,
    loadFont: async () => {
      await document.fonts.load('16px "Source Sans 3"');
      await document.fonts.ready;
    },
    render: renderPrintDocument,
    fit: fitPrintDocument,
    print: () => window.print(),
    onState: (printState) => {
      DOM.printManualBtn.disabled = !['idle', 'warning'].includes(printState);
    }
  });

  state.viewer = createViewerState(currentCapability());
  state.reader = loadReaderPreferences();
  state.viewer = reduceViewerState(state.viewer, {
    type: 'set-corrected-text-visible',
    visible: state.reader.correctedTextVisible
  });
  renderViewerState();

  // Initialize lazy image loader
  ImageLoader.init();

  // Setup event listeners
  setupEventListeners();

  // Load initial data
  loadInitialData();
}

// Setup all event listeners
function setupEventListeners() {
  const saveTranslationDebounced = debounce(saveInspectorTranslation, UI_TIMINGS.EDIT_DEBOUNCE);
  const saveBboxDebounced = debounce(saveInspectorBbox, UI_TIMINGS.EDIT_DEBOUNCE);
  // Search and filter
  DOM.searchInput.addEventListener('input', filterManuals);

  // Navigation
  document.getElementById('backToHome').addEventListener('click', () => navigate('home'));
  window.addEventListener('popstate', handlePopState);

  // Viewer controls
  DOM.translationsBtn.addEventListener('click', () => {
    toggleTranslations();
    state.reader.correctedTextVisible = state.viewer.correctedTextVisible;
    saveReaderPreferences();
  });
  DOM.prevPageBtn.addEventListener('click', () => navigatePage('prev'));
  DOM.nextPageBtn.addEventListener('click', () => navigatePage('next'));
  DOM.pageJumpInput.addEventListener('change', () => {
    const target = pageJumpIndex(DOM.pageJumpInput.value, state.currentManual?.pages.length ?? 0);
    if (target === null || !jumpToPage(target)) {
      DOM.pageJumpInput.value = String(state.viewer.currentPage + 1);
    }
  });
  DOM.decreaseTextSizeBtn.addEventListener('click', () => setReaderScale(-1));
  DOM.resetTextSizeBtn.addEventListener('click', () => setReaderScale(0));
  DOM.increaseTextSizeBtn.addEventListener('click', () => setReaderScale(1));
  DOM.printManualBtn.addEventListener('click', () => {
    if (printPreflightDismissed()) {
      runPrintPreparation();
    } else {
      openPrintPreflight();
    }
  });
  DOM.printSettingsBtn.addEventListener('click', openPrintPreflight);
  document.getElementById('cancelPrintPreflight').addEventListener('click', () => {
    DOM.printPreflightDialog.close();
  });
  document.getElementById('continuePrintPreflight').addEventListener('click', () => {
    if (document.getElementById('dismissPrintPreflight').checked) {
      try {
        localStorage.setItem(PRINT_PREFLIGHT_KEY, 'true');
      } catch {
        setPrintStatus('Could not save print preference', true);
      }
    }
    DOM.printPreflightDialog.close();
    runPrintPreparation();
  });
  document.getElementById('cancelPrintWarnings').addEventListener('click', () => {
    DOM.printWarningsDialog.close();
    printPreparation.cleanup();
  });
  document.getElementById('printAnywayBtn').addEventListener('click', () => {
    DOM.printWarningsDialog.close();
    runPrintPreparation(true);
  });
  document.getElementById('closeLargeTextPopover').addEventListener('click', closeLargeTextPopover);
  document.addEventListener('click', (event) => {
    if (
      state.viewer.openPopover === 'large-text' &&
      !DOM.largeTextPopover.contains(event.target) &&
      !event.target.closest('.overlay')
    ) {
      closeLargeTextPopover();
    }
  });
  DOM.editTranslationsBtn.addEventListener('click', () => {
    prepareDraftForEditing();
    if (EditSession.start(state.currentManualName)) {
      dispatchViewer({ type: 'enter-editing' });
    }
  });
  DOM.exitEditingBtn.addEventListener('click', () => {
    dispatchViewer({ type: 'exit-editing' });
  });
  DOM.discardDraftBtn.addEventListener('click', () => {
    const result = state.draftStore.discard(state.currentManualName);
    if (result.ok) {
      state.pendingDraft = null;
      setDraftStatus('Local draft discarded');
    } else {
      setDraftStatus(`Could not discard draft: ${result.error}`, true);
    }
    renderViewerState();
  });
  DOM.allTranslationsBtn.addEventListener('click', () => {
    const willOpen = DOM.translationsDrawer.classList.contains('hidden');
    DOM.translationsDrawer.classList.toggle('hidden', !willOpen);
    DOM.allTranslationsBtn.setAttribute('aria-expanded', String(willOpen));
  });
  DOM.inspectorTranslation.addEventListener('input', saveTranslationDebounced);
  DOM.inspectorBboxInputs.forEach((input) => {
    input.addEventListener('input', saveBboxDebounced);
  });
  DOM.deleteSelectedBlockBtn.addEventListener('click', () => {
    if (confirm('Delete this text block?')) deleteSelectedBlock();
  });
  window.addEventListener('resize', updateEditingCapability);
  finePointerQuery.addEventListener('change', updateEditingCapability);

  // Edit actions
  DOM.downloadEditsBtn.addEventListener('click', () => downloadJSON(state.currentManual));
  DOM.submitGitHubBtn.addEventListener('click', () => submitToGitHub(state.currentManual));
  DOM.undoBtn.addEventListener('click', undoLastEdit);
  DOM.redoBtn.addEventListener('click', redoLastEdit);

  // Page panel scroll with throttling
  const throttledUpdatePageIndicator = throttle(updatePageIndicator, UI_TIMINGS.SCROLL_THROTTLE);
  DOM.pagePanel.addEventListener('scroll', throttledUpdatePageIndicator);

  // Event delegation for page overlays
  DOM.pagePanel.addEventListener('click', (e) => {
    const overlay = e.target.closest('.overlay');
    if (overlay) {
      const pageIdx = parseInt(overlay.dataset.pageId);
      const blockIdx = parseInt(overlay.dataset.blockId);
      highlightBlock(pageIdx, blockIdx);
      if (state.viewer.mode === 'reading') {
        maybeOpenLargeTextPopover(pageIdx, blockIdx, overlay);
      } else {
        import('./js/editor.js?v=17').then(({ enterBlockEditMode }) => {
          enterBlockEditMode(pageIdx, blockIdx);
        });
      }
    }
  });

  DOM.pagePanel.addEventListener(
    'mouseenter',
    (e) => {
      const overlay = e.target.closest('.overlay');
      if (overlay) {
        const pageIdx = parseInt(overlay.dataset.pageId);
        const blockIdx = parseInt(overlay.dataset.blockId);
        highlightBlock(pageIdx, blockIdx);
      }
    },
    true
  );

  DOM.pagePanel.addEventListener('pointerdown', (event) => {
    if (state.viewer.mode !== 'editing') return;
    const overlay = event.target.closest('.overlay');
    if (!overlay) return;
    const image = overlay.closest('.page')?.querySelector('img');
    if (!image) return;

    const pageIndex = Number(overlay.dataset.pageId);
    const blockIndex = Number(overlay.dataset.blockId);
    const bbox = state.currentManual.pages[pageIndex].blocks[blockIndex].bbox;
    const pageRect = image.getBoundingClientRect();
    const handle = event.target.closest('.resize-handle')?.dataset.handle ?? null;
    activeBboxGesture = {
      pageIndex,
      blockIndex,
      overlay,
      pageRect,
      gesture: beginBboxGesture({
        kind: handle ? 'resize' : 'drag',
        handle,
        bbox,
        pointer: gesturePointer(event, pageRect)
      })
    };
    event.preventDefault();
  });

  window.addEventListener('pointermove', (event) => {
    if (!activeBboxGesture) return;
    activeBboxGesture.gesture = updateBboxGesture(
      activeBboxGesture.gesture,
      gesturePointer(event, activeBboxGesture.pageRect)
    );
    applyBboxPreview(activeBboxGesture.overlay, activeBboxGesture.gesture.current);
  });

  window.addEventListener('pointerup', () => {
    if (!activeBboxGesture) return;
    const result = finalizeBboxGesture(activeBboxGesture.gesture);
    commitBboxGesture(
      activeBboxGesture.pageIndex,
      activeBboxGesture.blockIndex,
      result.before,
      result.after
    );
    activeBboxGesture = null;
  });

  window.addEventListener('pointercancel', () => {
    if (!activeBboxGesture) return;
    const bbox = cancelBboxGesture(activeBboxGesture.gesture);
    applyBboxPreview(activeBboxGesture.overlay, bbox);
    activeBboxGesture = null;
  });

  DOM.pagePanel.addEventListener(
    'mouseleave',
    (e) => {
      const overlay = e.target.closest('.overlay');
      if (overlay) {
        clearHighlights();
      }
    },
    true
  );

  // Event delegation for text list - direct block editing on click
  DOM.textList.addEventListener('click', (e) => {
    const textItem = e.target.closest('.text-item');
    if (!textItem) return;

    const pageIdx = parseInt(textItem.dataset.pageId);
    const blockIdx = parseInt(textItem.dataset.blockId);

    highlightBlock(pageIdx, blockIdx);
    if (state.viewer.mode === 'editing') {
      import('./js/editor.js?v=17').then(({ enterBlockEditMode }) => {
        enterBlockEditMode(pageIdx, blockIdx);
      });
    }
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && activeBboxGesture) {
      const bbox = cancelBboxGesture(activeBboxGesture.gesture);
      applyBboxPreview(activeBboxGesture.overlay, bbox);
      activeBboxGesture = null;
      return;
    }
    if (e.key === 'Escape' && state.viewer.openPopover === 'large-text') {
      closeLargeTextPopover();
      return;
    }
    // Ctrl/Cmd+Z for undo
    if (
      state.viewer.mode === 'editing' &&
      (e.ctrlKey || e.metaKey) &&
      e.key.toLowerCase() === 'z'
    ) {
      e.preventDefault();
      if (e.shiftKey) {
        redoLastEdit();
      } else {
        undoLastEdit();
      }
    }
  });

  // Create block button with page detection
  DOM.createBlockBtn.addEventListener('click', () => {
    if (!state.currentManual || state.viewer.mode !== 'editing') return;

    const pagePanel = DOM.pagePanel;
    const pages = pagePanel.querySelectorAll('.page');

    if (pages.length === 0) return;

    // Find current visible page
    const panelRect = pagePanel.getBoundingClientRect();
    const panelCenter = panelRect.top + panelRect.height / 2;

    for (let i = 0; i < pages.length; i++) {
      const pageRect = pages[i].getBoundingClientRect();
      if (pageRect.top <= panelCenter && pageRect.bottom >= panelCenter) {
        createTextBlock(i);
        return;
      }
    }

    // Fallback to first page
    createTextBlock(0);
  });

  // Warn on navigation with unsaved changes
  window.addEventListener('beforeunload', (e) => {
    if (EditSession.hasUnsavedChanges) {
      e.preventDefault();
      e.returnValue = '';
      return '';
    }
  });
}

// Load initial data (manuals and tags)
async function loadInitialData() {
  try {
    // Load meta.json
    const metaResponse = await fetch('meta.json');
    const metaData = await metaResponse.json();
    state.manuals = metaData.manuals;

    // Load tags from meta.json
    if (metaData.tags) {
      state.tagsData = { tags: metaData.tags };
      renderTagFilters();
    }

    // Render manuals
    renderManualList(state.manuals);
    DOM.loadingIndicator.classList.add('hidden');

    // Check URL hash for direct navigation
    const hash = manualNameFromHash(window.location.hash);
    if (hash) {
      navigate('viewer', hash);
    }
  } catch (error) {
    console.error('Failed to load manuals:', error);
    DOM.loadingIndicator.classList.add('is-error');
    DOM.loadingIndicator.textContent = 'Catalog unavailable. Please refresh the page.';
  }
}

// Initialize app when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeApp);
} else {
  initializeApp();
}
