// Edit mode functionality module
import { BBOX_DEFAULTS, UI_TIMINGS } from './config.js';
import { state, DOM, EditSession } from './state.js';
import { validateBbox, clampBbox, debounce } from './utils.js';
import { renderOverlays, showEditButtons, hideEditButtons } from './renderer.js';

// Enter edit mode for a specific block
export function enterBlockEditMode(pageIdx, blockIdx) {
    // Start edit session if not already started
    if (!EditSession.isActive()) {
        if (!EditSession.start(state.currentManualName)) {
            return false;
        }
    }

    // Exit any currently editing block
    if (state.currentlyEditingBlock) {
        exitBlockEditMode();
    }

    // Find the text item
    const textItem = document.querySelector(`[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"].text-item`);
    if (!textItem) return false;

    // Mark as editing
    state.currentlyEditingBlock = { pageIdx, blockIdx, element: textItem };
    textItem.classList.add('editing');

    // Show bbox editor and delete button for this block
    const bboxEditor = textItem.querySelector('.bbox-editor');
    const deleteBtn = textItem.querySelector('.delete-btn');
    if (bboxEditor) bboxEditor.classList.remove('hidden');
    if (deleteBtn) deleteBtn.classList.remove('hidden');

    // Make translation editable
    const translationDiv = textItem.querySelector('.text-translation');
    if (translationDiv) {
        translationDiv.style.display = 'block'; // Ensure visible
        translationDiv.contentEditable = true;
        translationDiv.focus();

        // Select all text
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(translationDiv);
        selection.removeAllRanges();
        selection.addRange(range);

        // Save on blur
        const handleBlur = () => {
            saveTranslationEdit(pageIdx, blockIdx);
            translationDiv.removeEventListener('blur', handleBlur);
            translationDiv.removeEventListener('keydown', handleKeydown);
        };

        // Handle Enter/Escape
        const handleKeydown = (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                translationDiv.blur(); // Triggers save via handleBlur
            } else if (e.key === 'Escape') {
                e.preventDefault();
                // Restore original text without saving
                translationDiv.textContent = state.currentManual.pages[pageIdx].blocks[blockIdx].translation;
                exitBlockEditMode();
            }
        };

        translationDiv.addEventListener('blur', handleBlur);
        translationDiv.addEventListener('keydown', handleKeydown);
    }

    return true;
}

// Exit edit mode for currently editing block
export function exitBlockEditMode() {
    if (!state.currentlyEditingBlock) return;

    const { element } = state.currentlyEditingBlock;
    element.classList.remove('editing');

    // Hide bbox editor and delete button
    const bboxEditor = element.querySelector('.bbox-editor');
    const deleteBtn = element.querySelector('.delete-btn');
    if (bboxEditor) bboxEditor.classList.add('hidden');
    if (deleteBtn) deleteBtn.classList.add('hidden');

    // Make translation non-editable
    const translationDiv = element.querySelector('.text-translation');
    if (translationDiv && translationDiv.isContentEditable) {
        translationDiv.contentEditable = false;
    }

    state.currentlyEditingBlock = null;
}

// Toggle edit mode (keep for button compatibility, but simplified)
export function toggleEditMode() {
    state.editMode = !state.editMode;

    if (state.editMode) {
        // Start edit session
        if (!EditSession.start(state.currentManualName)) {
            state.editMode = false;
            return;
        }

        DOM.editModeBtn.textContent = '✏️ Exit Edit Mode';
        DOM.editModeBtn.style.background = '#ff6b6b';
        DOM.createBlockBtn.classList.remove('hidden');
    } else {
        // Exit any currently editing block
        exitBlockEditMode();

        DOM.editModeBtn.textContent = '✏️ Edit Mode';
        DOM.editModeBtn.style.background = '';
        DOM.createBlockBtn.classList.add('hidden');

        // Close any open bbox editor
        if (state.currentBboxEdit) {
            const editor = state.currentBboxEdit.editor;
            editor.classList.remove('active');
            state.currentBboxEdit = null;
        }
    }

    // Show/hide edit buttons based on whether we have edits
    if (state.editedBlocks.size > 0) {
        showEditButtons();
    } else {
        hideEditButtons();
    }
}

// Create a new text block
export function createTextBlock(pageIdx) {
    if (!state.editMode) {
        alert('Please enable Edit Mode first');
        return;
    }

    const page = state.currentManual.pages[pageIdx];
    const img = document.querySelector(`#page-${pageIdx} img`);

    if (!img || !img.complete) {
        alert('Please wait for the page to load before adding text blocks');
        return;
    }

    const newBlock = {
        text: prompt('Enter text for new block:', '') || 'New text block',
        translation: prompt('Enter translation:', '') || '',
        bbox: [
            BBOX_DEFAULTS.X_RATIO,
            BBOX_DEFAULTS.Y_RATIO,
            BBOX_DEFAULTS.WIDTH_RATIO,
            BBOX_DEFAULTS.HEIGHT_RATIO
        ]
    };

    page.blocks.push(newBlock);

    // Mark as edited
    const blockIdx = page.blocks.length - 1;
    state.editedBlocks.add(`${pageIdx}-${blockIdx}`);
    EditSession.markDirty();

    // Re-render the page
    const pageDiv = document.getElementById(`page-${pageIdx}`);
    const overlays = pageDiv.querySelectorAll('.overlay');
    overlays.forEach(overlay => overlay.remove());
    renderOverlays(pageDiv, page, pageIdx);

    // Re-render text list
    import('./renderer.js').then(({ renderTextList }) => {
        renderTextList();
        reapplyEditMode();
        showEditButtons();
    });
}

// Delete a text block
export function deleteTextBlock(pageIdx, blockIdx) {
    if (!state.editMode) return;

    const page = state.currentManual.pages[pageIdx];
    page.blocks.splice(blockIdx, 1);

    // Update edited blocks (remove deleted, update indices)
    const newEditedBlocks = new Set();
    state.editedBlocks.forEach(key => {
        const [p, b] = key.split('-').map(Number);
        if (p === pageIdx) {
            if (b < blockIdx) {
                newEditedBlocks.add(`${p}-${b}`);
            } else if (b > blockIdx) {
                newEditedBlocks.add(`${p}-${b - 1}`);
            }
        } else {
            newEditedBlocks.add(key);
        }
    });
    state.editedBlocks = newEditedBlocks;

    // Mark as having edits
    EditSession.markDirty();

    // Re-render
    const pageDiv = document.getElementById(`page-${pageIdx}`);
    const overlays = pageDiv.querySelectorAll('.overlay');
    overlays.forEach(overlay => overlay.remove());
    renderOverlays(pageDiv, page, pageIdx);

    import('./renderer.js').then(({ renderTextList }) => {
        renderTextList();
        reapplyEditMode();
    });

    if (state.editedBlocks.size > 0) {
        showEditButtons();
    } else {
        hideEditButtons();
    }
}

// Open bbox editor
export function openBboxEditor(pageIdx, blockIdx, bboxEditor) {
    if (!state.editMode) return;

    // Close previous editor
    if (state.currentBboxEdit && state.currentBboxEdit.editor !== bboxEditor) {
        state.currentBboxEdit.editor.classList.remove('active');
    }

    bboxEditor.classList.add('active');
    state.currentBboxEdit = { pageIdx, blockIdx, editor: bboxEditor };

    highlightBlock(pageIdx, blockIdx);
    document.getElementById(`page-${pageIdx}`).scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// Update bbox in real-time (called on input)
export function updateBboxLive(pageIdx, blockIdx, bboxEditor) {
    if (!state.editMode) return;

    const inputs = {
        x: bboxEditor.querySelector('.bbox-x'),
        y: bboxEditor.querySelector('.bbox-y'),
        w: bboxEditor.querySelector('.bbox-w'),
        h: bboxEditor.querySelector('.bbox-h')
    };

    const rawBbox = [
        parseFloat(inputs.x.value),
        parseFloat(inputs.y.value),
        parseFloat(inputs.w.value),
        parseFloat(inputs.h.value)
    ];

    const validBbox = validateBbox(rawBbox);
    if (!validBbox) return;

    const clampedBbox = clampBbox(validBbox);

    // Update the actual data
    const page = state.currentManual.pages[pageIdx];
    page.blocks[blockIdx].bbox = clampedBbox;

    // Mark as edited
    state.editedBlocks.add(`${pageIdx}-${blockIdx}`);
    EditSession.markDirty();

    // Update the overlay position
    const overlay = document.querySelector(
        `.overlay[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`
    );
    if (overlay) {
        overlay.style.left = (clampedBbox[0] * 100) + '%';
        overlay.style.top = (clampedBbox[1] * 100) + '%';
        overlay.style.width = (clampedBbox[2] * 100) + '%';
        overlay.style.height = (clampedBbox[3] * 100) + '%';
    }

    // Update input values if they were clamped
    if (clampedBbox[0] !== rawBbox[0]) inputs.x.value = clampedBbox[0].toFixed(2);
    if (clampedBbox[1] !== rawBbox[1]) inputs.y.value = clampedBbox[1].toFixed(2);
    if (clampedBbox[2] !== rawBbox[2]) inputs.w.value = clampedBbox[2].toFixed(2);
    if (clampedBbox[3] !== rawBbox[3]) inputs.h.value = clampedBbox[3].toFixed(2);

    showEditButtons();
}

// Debounced version for input events
export const updateBboxLiveDebounced = debounce(updateBboxLive, 150);

// Save translation edits
export function saveTranslationEdit(pageIdx, blockIdx) {
    const textItem = document.querySelector(`[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"].text-item`);
    if (!textItem) return;

    const translationDiv = textItem.querySelector('.text-translation');
    if (!translationDiv || !translationDiv.isContentEditable) return;

    const newTranslation = translationDiv.textContent.trim();

    // Update data
    state.currentManual.pages[pageIdx].blocks[blockIdx].translation = newTranslation;

    // Update overlay if visible
    const overlay = document.querySelector(`.overlay[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`);
    if (overlay && state.showTranslations) {
        overlay.textContent = newTranslation;
    }

    // Mark as edited
    state.editedBlocks.add(`${pageIdx}-${blockIdx}`);
    EditSession.markDirty();
    showEditButtons();

    // Exit block edit mode
    exitBlockEditMode();
}

// Reapply edit mode styles after re-rendering
export function reapplyEditMode() {
    if (!state.editMode) return;

    setTimeout(() => {
        document.querySelectorAll('.delete-btn').forEach(btn => btn.classList.remove('hidden'));
        document.querySelectorAll('.bbox-editor').forEach(editor => editor.classList.remove('hidden'));
        document.querySelectorAll('.text-item').forEach(item => item.classList.add('editable'));
    }, UI_TIMINGS.EDIT_MODE_REAPPLY_DELAY);
}
