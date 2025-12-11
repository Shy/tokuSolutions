// Main application entry point - modular architecture
import { state, DOM, EditSession } from './js/state.js';
import { ImageLoader } from './js/image-loader.js';
import { throttle } from './js/utils.js';
import { UI_TIMINGS } from './js/config.js';
import {
    renderManualList,
    renderTagFilters,
    highlightBlock,
    clearHighlights
} from './js/renderer.js';
import {
    createTextBlock,
    undoLastEdit
} from './js/editor.js';
import {
    navigate,
    toggleOverlays,
    toggleTranslations,
    filterManuals,
    handlePopState,
    navigatePage,
    updatePageIndicator
} from './js/navigation.js';
import { submitToGitHub, downloadJSON } from './js/github.js';

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
    DOM.createBlockBtn = document.getElementById('createBlockBtn');
    DOM.prevPageBtn = document.getElementById('prevPageBtn');
    DOM.nextPageBtn = document.getElementById('nextPageBtn');
    DOM.overlaysBtn = document.getElementById('overlaysBtn');
    DOM.translationsBtn = document.getElementById('translationsBtn');

    // Initialize lazy image loader
    ImageLoader.init();

    // Setup event listeners
    setupEventListeners();

    // Load initial data
    loadInitialData();
}

// Setup all event listeners
function setupEventListeners() {
    // Search and filter
    DOM.searchInput.addEventListener('input', filterManuals);

    // Navigation
    document.getElementById('backToHome').addEventListener('click', () => navigate('home'));
    window.addEventListener('popstate', handlePopState);

    // Viewer controls
    DOM.overlaysBtn.addEventListener('click', toggleOverlays);
    DOM.translationsBtn.addEventListener('click', toggleTranslations);
    DOM.prevPageBtn.addEventListener('click', () => navigatePage('prev'));
    DOM.nextPageBtn.addEventListener('click', () => navigatePage('next'));

    // Edit actions
    DOM.downloadEditsBtn.addEventListener('click', () => downloadJSON(state.currentManual));
    DOM.submitGitHubBtn.addEventListener('click', () => submitToGitHub(state.currentManual));
    DOM.undoBtn.addEventListener('click', undoLastEdit);

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
        }
    });

    DOM.pagePanel.addEventListener('mouseenter', (e) => {
        const overlay = e.target.closest('.overlay');
        if (overlay) {
            const pageIdx = parseInt(overlay.dataset.pageId);
            const blockIdx = parseInt(overlay.dataset.blockId);
            highlightBlock(pageIdx, blockIdx);
        }
    }, true);

    DOM.pagePanel.addEventListener('mouseleave', (e) => {
        const overlay = e.target.closest('.overlay');
        if (overlay) {
            clearHighlights();
        }
    }, true);

    // Event delegation for text list - direct block editing on click
    DOM.textList.addEventListener('click', (e) => {
        const textItem = e.target.closest('.text-item');
        if (!textItem) return;

        const pageIdx = parseInt(textItem.dataset.pageId);
        const blockIdx = parseInt(textItem.dataset.blockId);

        // Import enterBlockEditMode dynamically
        import('./js/editor.js').then(({ enterBlockEditMode }) => {
            enterBlockEditMode(pageIdx, blockIdx);
        });
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        // Ctrl/Cmd+Z for undo
        if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
            e.preventDefault();
            undoLastEdit();
        }
    });

    // Create block button with page detection
    DOM.createBlockBtn.addEventListener('click', () => {
        if (!state.currentManual) return;

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

        // Try to load tags.json
        try {
            const tagsResponse = await fetch('tags.json');
            state.tagsData = await tagsResponse.json();
            renderTagFilters();
        } catch (tagsError) {
            // Tags are optional - silently continue without them
        }

        // Render manuals
        renderManualList(state.manuals);
        DOM.loadingIndicator.classList.add('hidden');

        // Check URL hash for direct navigation
        const hash = window.location.hash.slice(1);
        if (hash) {
            navigate('viewer', hash);
        }

    } catch (error) {
        console.error('Failed to load manuals:', error);
        DOM.loadingIndicator.innerHTML = '<p style="color: red;">Failed to load manuals. Please refresh the page.</p>';
    }
}

// Initialize app when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeApp);
} else {
    initializeApp();
}
