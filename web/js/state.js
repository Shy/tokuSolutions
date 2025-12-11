// Global application state
export const state = {
    manuals: [],
    tagsData: null,
    selectedTags: new Set(),
    currentManual: null,
    currentManualName: null,  // The folder name (e.g., "CSM-Den-O-Belt-v2")
    showOverlays: true,
    showTranslations: true,
    editMode: false,
    editedBlocks: new Set(),
    currentBboxEdit: null,
    currentlyEditingBlock: null,  // { pageIdx, blockIdx, element }
    currentRenderToken: 0
};

// DOM element cache - populated on DOMContentLoaded
export const DOM = {
    searchInput: null,
    manualsGrid: null,
    loadingIndicator: null,
    pagePanel: null,
    pageIndicator: null,
    textList: null,
    editModeBtn: null,
    downloadEditsBtn: null,
    submitGitHubBtn: null,
    createBlockBtn: null,
    prevPageBtn: null,
    nextPageBtn: null,
    overlaysBtn: null,
    translationsBtn: null
};

// Edit session management - enforce single document editing
export const EditSession = {
    activeManual: null,
    hasUnsavedChanges: false,

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
        this.activeManual = manualName;
        return true;
    },

    markDirty() {
        this.hasUnsavedChanges = true;
    },

    markClean() {
        this.hasUnsavedChanges = false;
    },

    clear() {
        this.activeManual = null;
        this.hasUnsavedChanges = false;
        state.editedBlocks.clear();
        if (state.editMode) {
            // Import toggleEditMode dynamically to avoid circular dependency
            import('./editor.js').then(({ toggleEditMode }) => {
                toggleEditMode();
            });
        }
    },

    canNavigateAway() {
        if (!this.hasUnsavedChanges) return true;
        return confirm(
            `You have unsaved changes.\n\n` +
            `Leaving will discard all edits.\n\n` +
            `Continue anyway?`
        );
    },

    isActive() {
        return this.activeManual !== null;
    }
};
