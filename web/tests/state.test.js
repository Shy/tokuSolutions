import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state, EditSession } from '../js/state.js';

describe('EditSession', () => {
  beforeEach(() => {
    // Reset session state
    EditSession.clear();
    state.editedBlocks.clear();
    state.lastEdit = null;
  });

  describe('start', () => {
    it('should allow starting new session', () => {
      const result = EditSession.start('manual1');
      expect(result).toBe(true);
      expect(EditSession.activeManual).toBe('manual1');
    });

    it('should allow continuing same manual', () => {
      EditSession.start('manual1');
      const result = EditSession.start('manual1');
      expect(result).toBe(true);
    });

    it('should warn when switching with unsaved changes', () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

      EditSession.start('manual1');
      EditSession.markDirty();

      const result = EditSession.start('manual2');

      expect(result).toBe(false);
      expect(confirmSpy).toHaveBeenCalled();
      expect(EditSession.activeManual).toBe('manual1'); // unchanged

      confirmSpy.mockRestore();
    });

    it('should allow switching when user confirms', () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

      EditSession.start('manual1');
      EditSession.markDirty();

      const result = EditSession.start('manual2');

      expect(result).toBe(true);
      expect(EditSession.activeManual).toBe('manual2');
      expect(EditSession.hasUnsavedChanges).toBe(false);

      confirmSpy.mockRestore();
    });

    it('should not warn when switching without unsaved changes', () => {
      const confirmSpy = vi.spyOn(window, 'confirm');

      EditSession.start('manual1');
      const result = EditSession.start('manual2');

      expect(result).toBe(true);
      expect(confirmSpy).not.toHaveBeenCalled();

      confirmSpy.mockRestore();
    });
  });

  describe('markDirty', () => {
    it('should mark session as having unsaved changes', () => {
      expect(EditSession.hasUnsavedChanges).toBe(false);
      EditSession.markDirty();
      expect(EditSession.hasUnsavedChanges).toBe(true);
    });
  });

  describe('markClean', () => {
    it('should mark session as clean', () => {
      EditSession.markDirty();
      expect(EditSession.hasUnsavedChanges).toBe(true);

      EditSession.markClean();
      expect(EditSession.hasUnsavedChanges).toBe(false);
    });
  });

  describe('clear', () => {
    it('should reset all session state', () => {
      EditSession.start('manual1');
      EditSession.markDirty();
      state.editedBlocks.add('0-1');
      state.lastEdit = { pageIdx: 0, blockIdx: 1, previousState: {} };

      EditSession.clear();

      expect(EditSession.activeManual).toBe(null);
      expect(EditSession.hasUnsavedChanges).toBe(false);
      expect(state.editedBlocks.size).toBe(0);
      expect(state.lastEdit).toBe(null);
    });
  });

  describe('canNavigateAway', () => {
    it('should return true when no unsaved changes', () => {
      expect(EditSession.canNavigateAway()).toBe(true);
    });

    it('should prompt when has unsaved changes', () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

      EditSession.markDirty();
      const result = EditSession.canNavigateAway();

      expect(result).toBe(true);
      expect(confirmSpy).toHaveBeenCalled();

      confirmSpy.mockRestore();
    });

    it('should return false when user cancels', () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

      EditSession.markDirty();
      const result = EditSession.canNavigateAway();

      expect(result).toBe(false);

      confirmSpy.mockRestore();
    });
  });
});

describe('state', () => {
  it('should have expected initial values', () => {
    expect(state.manuals).toEqual([]);
    expect(state.tagsData).toBe(null);
    expect(state.selectedTags).toBeInstanceOf(Set);
    expect(state.currentManual).toBe(null);
    expect(state.showOverlays).toBe(true);
    expect(state.showTranslations).toBe(true);
    expect(state.editedBlocks).toBeInstanceOf(Set);
    expect(state.currentBboxEdit).toBe(null);
    expect(state.lastEdit).toBe(null);
    expect(typeof state.currentRenderToken).toBe('number');
  });

  it('should allow state modifications', () => {
    state.manuals = [{ name: 'test' }];
    expect(state.manuals).toEqual([{ name: 'test' }]);

    state.showOverlays = false;
    expect(state.showOverlays).toBe(false);

    state.editedBlocks.add('test');
    expect(state.editedBlocks.has('test')).toBe(true);
  });
});
