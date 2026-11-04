const DEFAULT_CAPABILITY = Object.freeze({ width: 0, finePointer: false });

export function canEditTranslations(capability = DEFAULT_CAPABILITY) {
  return capability.width >= 1024 && capability.finePointer === true;
}

export function createViewerState(capability = DEFAULT_CAPABILITY) {
  return {
    mode: 'reading',
    canEdit: canEditTranslations(capability),
    correctedTextVisible: true,
    selectedBlock: null,
    currentPage: 0,
    openInspector: null,
    openPopover: null
  };
}

function clearTransientEditorState(state) {
  return {
    ...state,
    selectedBlock: null,
    openInspector: null,
    openPopover: null
  };
}

export function reduceViewerState(state, action) {
  switch (action.type) {
    case 'capability-changed': {
      const canEdit = canEditTranslations(action.capability);
      const next = { ...state, canEdit };
      return canEdit ? next : { ...clearTransientEditorState(next), mode: 'reading' };
    }
    case 'enter-editing':
      return state.canEdit ? { ...state, mode: 'editing', openPopover: null } : state;
    case 'exit-editing':
      return { ...clearTransientEditorState(state), mode: 'reading' };
    case 'set-corrected-text-visible':
      return { ...state, correctedTextVisible: Boolean(action.visible) };
    case 'select-block':
      return { ...state, selectedBlock: action.block };
    case 'set-current-page':
      return { ...state, currentPage: Math.max(0, Math.trunc(action.page)) };
    case 'open-inspector':
      return { ...state, openInspector: action.inspector };
    case 'open-popover':
      return { ...state, openPopover: action.popover };
    case 'clear-transient-ui':
      return clearTransientEditorState(state);
    default:
      return state;
  }
}
