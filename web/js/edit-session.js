import { clampBbox } from './utils.js?v=14';

function clone(value) {
  return structuredClone(value);
}

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function normalizeBbox(bbox) {
  return clampBbox(bbox).map((value) => Number(value.toFixed(6)));
}

function applyAction(manual, action, direction) {
  const page = manual.pages[action.pageIndex];
  const forward = direction === 'forward';

  switch (action.type) {
    case 'translation':
      page.blocks[action.blockIndex].translation = forward ? action.after : action.before;
      break;
    case 'bbox':
      page.blocks[action.blockIndex].bbox = clone(forward ? action.after : action.before);
      break;
    case 'add':
      if (forward) {
        page.blocks.splice(action.blockIndex, 0, clone(action.block));
      } else {
        page.blocks.splice(action.blockIndex, 1);
      }
      break;
    case 'delete':
      if (forward) {
        page.blocks.splice(action.blockIndex, 1);
      } else {
        page.blocks.splice(action.blockIndex, 0, clone(action.block));
      }
      break;
    default:
      throw new Error(`Unknown edit action: ${action.type}`);
  }
}

export class EditSessionModel {
  #undoStack = [];
  #redoStack = [];
  #cleanDepth = 0;
  #onChange;

  constructor(manual, { onChange = () => {}, initialDirty = false } = {}) {
    this.manual = clone(manual);
    this.selectedBlock = null;
    this.#onChange = onChange;
    if (initialDirty) this.#cleanDepth = -1;
  }

  get dirty() {
    return this.#undoStack.length !== this.#cleanDepth;
  }

  get canUndo() {
    return this.#undoStack.length > 0;
  }

  get canRedo() {
    return this.#redoStack.length > 0;
  }

  get undoDepth() {
    return this.#undoStack.length;
  }

  get redoDepth() {
    return this.#redoStack.length;
  }

  #notify(kind, action) {
    this.#onChange({
      kind,
      action: clone(action),
      dirty: this.dirty,
      canUndo: this.canUndo,
      canRedo: this.canRedo,
      selectedBlock: clone(this.selectedBlock)
    });
  }

  #commit(action) {
    if (this.#redoStack.length > 0 && this.#cleanDepth > this.#undoStack.length) {
      this.#cleanDepth = -1;
    }
    applyAction(this.manual, action, 'forward');
    this.#undoStack.push(Object.freeze(clone(action)));
    this.#redoStack = [];
    this.#notify('commit', action);
    return true;
  }

  selectBlock(selection) {
    this.selectedBlock = selection ? clone(selection) : null;
  }

  markClean() {
    this.#cleanDepth = this.#undoStack.length;
    this.#notify('clean', { type: 'checkpoint' });
  }

  updateTranslation(pageIndex, blockIndex, translation) {
    const before = this.manual.pages[pageIndex].blocks[blockIndex].translation;
    if (before === translation) return false;
    return this.#commit({
      type: 'translation',
      pageIndex,
      blockIndex,
      before,
      after: translation
    });
  }

  updateBbox(pageIndex, blockIndex, bbox) {
    const before = this.manual.pages[pageIndex].blocks[blockIndex].bbox;
    const after = normalizeBbox(bbox);
    if (sameValue(before, after)) return false;
    return this.#commit({ type: 'bbox', pageIndex, blockIndex, before, after });
  }

  commitBboxGesture(pageIndex, blockIndex, before, after) {
    const normalizedBefore = normalizeBbox(before);
    const normalizedAfter = normalizeBbox(after);
    if (sameValue(normalizedBefore, normalizedAfter)) return false;
    return this.#commit({
      type: 'bbox',
      pageIndex,
      blockIndex,
      before: normalizedBefore,
      after: normalizedAfter
    });
  }

  addBlock(pageIndex, block, blockIndex = this.manual.pages[pageIndex].blocks.length) {
    this.selectedBlock = { pageIndex, blockIndex };
    const committed = this.#commit({
      type: 'add',
      pageIndex,
      blockIndex,
      block: { ...clone(block), bbox: normalizeBbox(block.bbox) }
    });
    return committed;
  }

  deleteBlock(pageIndex, blockIndex) {
    const page = this.manual.pages[pageIndex];
    const block = page.blocks[blockIndex];
    if (!block) return false;

    const nextIndex = Math.min(blockIndex, page.blocks.length - 2);
    this.selectedBlock = nextIndex >= 0 ? { pageIndex, blockIndex: nextIndex } : null;
    return this.#commit({
      type: 'delete',
      pageIndex,
      blockIndex,
      block: clone(block)
    });
  }

  undo() {
    const action = this.#undoStack.pop();
    if (!action) return false;
    applyAction(this.manual, action, 'backward');
    this.#redoStack.push(action);
    this.#notify('undo', action);
    return true;
  }

  redo() {
    const action = this.#redoStack.pop();
    if (!action) return false;
    applyAction(this.manual, action, 'forward');
    this.#undoStack.push(action);
    this.#notify('redo', action);
    return true;
  }
}

export function createEditSession(manual, options) {
  return new EditSessionModel(manual, options);
}
