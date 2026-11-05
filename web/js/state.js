import { createViewerState } from './viewer-state.js?v=18';
import { createReaderPreferences } from './reader-controls.js?v=18';
import { createEditSession } from './edit-session.js?v=18';

// Global application state
export const state = {
  manuals: [],
  tagsData: null,
  selectedTags: new Set(),
  currentManual: null,
  sourceManual: null,
  currentManualName: null, // The folder name (e.g., "CSM-Den-O-Belt-v2")
  viewer: createViewerState(),
  reader: createReaderPreferences(),
  currentlyEditingBlock: null, // { pageIdx, blockIdx, element }
  currentRenderToken: 0,
  fitGeneration: 0,
  fitResults: [],
  editSession: null,
  pendingDraft: null,
  restoreDraftOnStart: false,
  draftStore: null,
  draftAutosave: null,
  draftStatus: null,
  onEditSessionChange: null
};

// DOM element cache - populated on DOMContentLoaded
export const DOM = {
  searchInput: null,
  manualsGrid: null,
  loadingIndicator: null,
  pagePanel: null,
  pageIndicator: null,
  textList: null,
  downloadEditsBtn: null,
  submitGitHubBtn: null,
  undoBtn: null,
  redoBtn: null,
  createBlockBtn: null,
  prevPageBtn: null,
  nextPageBtn: null,
  translationsBtn: null,
  editTranslationsBtn: null,
  exitEditingBtn: null,
  pageJumpInput: null,
  decreaseTextSizeBtn: null,
  resetTextSizeBtn: null,
  increaseTextSizeBtn: null,
  downloadPdfBtn: null,
  pdfStatus: null,
  largeTextPopover: null,
  largeTextPopoverContent: null,
  discardDraftBtn: null,
  draftStatus: null,
  allTranslationsBtn: null,
  translationsDrawer: null,
  inspectorTranslation: null,
  inspectorBboxInputs: [],
  deleteSelectedBlockBtn: null
};

// Edit session management - enforce single document editing
export const EditSession = {
  activeManual: null,

  get hasUnsavedChanges() {
    return state.editSession?.dirty ?? false;
  },

  start(manualName) {
    if (this.hasUnsavedChanges && this.activeManual !== manualName) {
      const confirmed = confirm(
        `You have unsaved changes in "${this.activeManual}".\n\n` +
          `Switching documents will discard these changes.\n\n` +
          `Continue anyway?`
      );
      if (!confirmed) {
        return false;
      }
      this.clear();
    }

    if (!state.editSession) {
      state.editSession = createEditSession(state.currentManual, {
        initialDirty: state.restoreDraftOnStart,
        onChange(notification) {
          state.currentManual = state.editSession.manual;
          state.viewer.selectedBlock = notification.selectedBlock;
          state.onEditSessionChange?.(notification);
        }
      });
      state.currentManual = state.editSession.manual;
      state.restoreDraftOnStart = false;
    }
    this.activeManual = manualName;
    return true;
  },

  clear() {
    this.activeManual = null;
    state.editSession = null;
  },

  canNavigateAway() {
    if (!this.hasUnsavedChanges) return true;
    return confirm(
      `You have unsaved changes.\n\n` + `Leaving will discard all edits.\n\n` + `Continue anyway?`
    );
  },

  isActive() {
    return this.activeManual !== null;
  }
};
