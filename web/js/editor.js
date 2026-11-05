import { BBOX_DEFAULTS } from './config.js?v=17';
import { state, DOM, EditSession } from './state.js?v=17';
import {
  attachResizeHandles,
  renderPages,
  renderTextList,
  scheduleManualFit,
  showEditButtons
} from './renderer.js?v=17';

function ensureEditSession() {
  if (state.editSession) return state.editSession;
  if (!EditSession.start(state.currentManualName)) return null;
  return state.editSession;
}

function refreshHistoryControls() {
  DOM.undoBtn?.classList.toggle('hidden', !state.editSession?.canUndo);
  DOM.redoBtn?.classList.toggle('hidden', !state.editSession?.canRedo);
  showEditButtons();
}

function rerenderEditedManual() {
  renderPages(state.currentManualName, state.currentRenderToken);
  renderTextList();
  refreshHistoryControls();
  scheduleManualFit();
  const selected = state.editSession?.selectedBlock;
  if (selected) populateInspector(selected.pageIndex, selected.blockIndex);
}

function populateInspector(pageIdx, blockIdx) {
  const block = state.currentManual.pages[pageIdx]?.blocks[blockIdx];
  if (!block) return false;
  document.getElementById('inspectorEmpty').classList.add('hidden');
  document.getElementById('inspectorFields').classList.remove('hidden');
  document.getElementById('inspectorOriginal').value = block.text;
  document.getElementById('inspectorTranslation').value = block.translation;
  syncInspectorBbox(block.bbox);
  document.getElementById('inspectorValidation').textContent =
    document.querySelector(`.overlay[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`)
      ?.dataset.fitStatus ?? 'Ready';
  return true;
}

export function enterBlockEditMode(pageIdx, blockIdx) {
  const session = ensureEditSession();
  if (!session || state.viewer.mode !== 'editing') return false;
  if (!populateInspector(pageIdx, blockIdx)) return false;

  document.querySelectorAll('.overlay.is-selected').forEach((element) => {
    element.classList.remove('is-selected');
    element.querySelectorAll('.resize-handle').forEach((handle) => handle.remove());
  });
  const selectedOverlay = document.querySelector(
    `.overlay[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`
  );
  selectedOverlay?.classList.add('is-selected');
  attachResizeHandles(selectedOverlay);
  document.querySelectorAll('.text-item.editing').forEach((element) => {
    element.classList.remove('editing');
  });
  document
    .querySelector(`.text-item[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`)
    ?.classList.add('editing');

  session.selectBlock({ pageIndex: pageIdx, blockIndex: blockIdx });
  state.viewer.selectedBlock = { pageIndex: pageIdx, blockIndex: blockIdx };
  state.currentlyEditingBlock = { pageIdx, blockIdx };
  return true;
}

export function exitBlockEditMode() {
  document.querySelectorAll('.overlay.is-selected, .text-item.editing').forEach((element) => {
    element.classList.remove('is-selected', 'editing');
    element.querySelectorAll('.resize-handle').forEach((handle) => handle.remove());
  });
  document.getElementById('inspectorFields')?.classList.add('hidden');
  document.getElementById('inspectorEmpty')?.classList.remove('hidden');
  state.currentlyEditingBlock = null;
}

export function createTextBlock(pageIdx) {
  const session = ensureEditSession();
  if (!session) return;
  const image = document.querySelector(`#page-${pageIdx} img`);
  if (!image?.complete) {
    alert('Please wait for the page to load before adding text blocks');
    return;
  }

  session.addBlock(pageIdx, {
    text: prompt('Enter text for new block:', '') || 'New text block',
    translation: prompt('Enter translation:', '') || '',
    bbox: [
      BBOX_DEFAULTS.X_RATIO,
      BBOX_DEFAULTS.Y_RATIO,
      BBOX_DEFAULTS.WIDTH_RATIO,
      BBOX_DEFAULTS.HEIGHT_RATIO
    ]
  });
  rerenderEditedManual();
}

export function deleteTextBlock(pageIdx, blockIdx) {
  const session = ensureEditSession();
  if (!session?.deleteBlock(pageIdx, blockIdx)) return;
  rerenderEditedManual();
}

export function syncInspectorBbox(bbox) {
  ['X', 'Y', 'W', 'H'].forEach((axis, index) => {
    const input = document.getElementById(`inspectorBbox${axis}`);
    if (input) input.value = bbox[index].toFixed(3);
  });
}

export function saveInspectorBbox() {
  const selection = state.editSession?.selectedBlock;
  if (!selection) return false;
  const bbox = ['X', 'Y', 'W', 'H'].map((axis) =>
    Number.parseFloat(document.getElementById(`inspectorBbox${axis}`).value)
  );
  const session = ensureEditSession();
  if (!session?.updateBbox(selection.pageIndex, selection.blockIndex, bbox)) return false;
  const savedBbox = session.manual.pages[selection.pageIndex].blocks[selection.blockIndex].bbox;
  const overlay = document.querySelector(
    `.overlay[data-page-id="${selection.pageIndex}"][data-block-id="${selection.blockIndex}"]`
  );
  if (overlay) {
    [overlay.style.left, overlay.style.top, overlay.style.width, overlay.style.height] =
      savedBbox.map((value) => `${value * 100}%`);
  }
  syncInspectorBbox(savedBbox);
  refreshHistoryControls();
  scheduleManualFit();
  return true;
}

export function saveInspectorTranslation() {
  const selection = state.editSession?.selectedBlock;
  if (!selection) return false;
  const session = ensureEditSession();
  const translation = document.getElementById('inspectorTranslation').value.trim();
  if (session?.updateTranslation(selection.pageIndex, selection.blockIndex, translation)) {
    const overlay = document.querySelector(
      `.overlay[data-page-id="${selection.pageIndex}"][data-block-id="${selection.blockIndex}"]`
    );
    if (overlay) {
      const handles = [...overlay.querySelectorAll('.resize-handle')];
      overlay.textContent = translation;
      handles.forEach((handle) => overlay.appendChild(handle));
    }
    const drawerTranslation = document.querySelector(
      `.text-item[data-page-id="${selection.pageIndex}"][data-block-id="${selection.blockIndex}"] .text-translation`
    );
    if (drawerTranslation) drawerTranslation.textContent = translation;
  }
  refreshHistoryControls();
  scheduleManualFit();
  return true;
}

export function deleteSelectedBlock() {
  const selection = state.editSession?.selectedBlock;
  if (!selection) return false;
  return deleteTextBlock(selection.pageIndex, selection.blockIndex);
}

export function undoLastEdit() {
  if (!state.editSession?.undo()) return;
  rerenderEditedManual();
}

export function redoLastEdit() {
  if (!state.editSession?.redo()) return;
  rerenderEditedManual();
}

export function commitBboxGesture(pageIdx, blockIdx, before, after) {
  const session = ensureEditSession();
  if (!session?.commitBboxGesture(pageIdx, blockIdx, before, after)) return false;
  rerenderEditedManual();
  return true;
}
