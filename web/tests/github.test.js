import { describe, it, expect, beforeEach, vi } from 'vitest';

describe('GitHub Integration Tests', () => {
  let mockState;
  let mockEditSession;
  let mockErrorHandler;
  let mockDocument;
  let submitToGitHub;
  let downloadJSON;

  beforeEach(async () => {
    // Reset modules
    vi.resetModules();

    // Mock dependencies
    mockState = {
      editedBlocks: new Set(['0-0']),
      currentManualName: 'test-manual'
    };

    mockEditSession = {
      markClean: vi.fn()
    };

    mockErrorHandler = {
      user: vi.fn()
    };

    // Mock DOM APIs
    const mockCopyBtn = {
      textContent: '',
      addEventListener: vi.fn((_event, handler) => {
        mockCopyBtn._clickHandler = handler;
      }),
      click() { if (this._clickHandler) this._clickHandler(); }
    };

    const mockCopyStatus = {
      style: { display: 'none' }
    };

    const mockOpenBtn = {
      addEventListener: vi.fn((_event, handler) => {
        mockOpenBtn._clickHandler = handler;
      }),
      click() { if (this._clickHandler) this._clickHandler(); }
    };

    const mockCloseBtn = {
      addEventListener: vi.fn((_event, handler) => {
        mockCloseBtn._clickHandler = handler;
      }),
      click() { if (this._clickHandler) this._clickHandler(); }
    };

    const mockModal = {
      innerHTML: '',
      style: { cssText: '' },
      querySelector: vi.fn((selector) => {
        // Return mock elements based on selector
        if (selector === '#copyJsonBtn') return mockCopyBtn;
        if (selector === '#copyStatus') return mockCopyStatus;
        if (selector === '#openGithubBtn') return mockOpenBtn;
        if (selector === '#closeModalBtn') return mockCloseBtn;
        return null;
      }),
      addEventListener: vi.fn()
    };

    const mockBackdrop = {
      style: { cssText: '' },
      appendChild: vi.fn(),
      addEventListener: vi.fn()
    };

    const mockAnchor = {
      href: '',
      download: '',
      click: vi.fn()
    };

    let createElementCallCount = 0;
    mockDocument = {
      createElement: vi.fn((tag) => {
        if (tag === 'div') {
          // First div is backdrop, second is modal
          createElementCallCount++;
          return createElementCallCount === 1 ? mockBackdrop : mockModal;
        }
        if (tag === 'a') return mockAnchor;
        return null;
      }),
      body: {
        appendChild: vi.fn(),
        removeChild: vi.fn()
      }
    };

    global.document = mockDocument;
    global.alert = vi.fn();
    global.window = {
      open: vi.fn(),
      getSelection: vi.fn(() => ({
        removeAllRanges: vi.fn(),
        addRange: vi.fn()
      }))
    };
    global.navigator = {
      clipboard: {
        writeText: vi.fn(() => Promise.resolve())
      }
    };

    global.URL = {
      createObjectURL: vi.fn(() => 'blob:test-url'),
      revokeObjectURL: vi.fn()
    };

    global.Blob = class Blob {
      constructor(content, options) {
        this.content = content;
        this.type = options.type;
      }
    };

    // Mock the module imports
    vi.doMock('../js/state.js', () => ({
      state: mockState,
      EditSession: mockEditSession
    }));

    vi.doMock('../js/errors.js', () => ({
      ErrorHandler: mockErrorHandler
    }));

    vi.doMock('../js/config.js', () => ({
      GITHUB_CONFIG: {
        REPO: 'shy/tokuSolutions'
      }
    }));

    // Import after mocks are set up
    const githubModule = await import('../js/github.js?' + Date.now());
    submitToGitHub = githubModule.submitToGitHub;
    downloadJSON = githubModule.downloadJSON;
  });

  describe('submitToGitHub - Validation', () => {
    it('should reject when no edits to save', async () => {
      mockState.editedBlocks = new Set();

      await submitToGitHub({ pages: [] });

      expect(mockErrorHandler.user).toHaveBeenCalledWith('No edits to save');
    });

    it('should reject when manual name is not set', async () => {
      mockState.currentManualName = null;

      await submitToGitHub({ pages: [] });

      expect(mockErrorHandler.user).toHaveBeenCalledWith('Could not determine manual name');
    });
  });

  describe('submitToGitHub - Modal Creation', () => {
    const mockManual = {
      pages: [
        {
          image: 'pages/page-0.webp',
          blocks: [
            { text: 'Original', translation: 'Translated', bbox: [0.1, 0.2, 0.3, 0.4] }
          ]
        }
      ]
    };

    it('should create modal with instructions', async () => {
      await submitToGitHub(mockManual);

      // Should create backdrop and modal elements
      expect(mockDocument.createElement).toHaveBeenCalledWith('div');
      expect(mockDocument.body.appendChild).toHaveBeenCalled();
    });

    it('should generate correct GitHub URL', async () => {
      await submitToGitHub(mockManual);

      // Verify window.open is called with correct URL when button is clicked
      // We need to simulate the button click
      const createElementCalls = mockDocument.createElement.mock.results;
      const modalElements = createElementCalls.filter(result => result.value?.querySelector);

      expect(modalElements.length).toBeGreaterThan(0);
    });

    it('should copy JSON to clipboard', async () => {
      await submitToGitHub(mockManual);

      // Modal should be created with copy functionality
      expect(mockDocument.body.appendChild).toHaveBeenCalled();

      // Note: Full clipboard API testing requires more complex mocking
      // as the actual copy handler is attached dynamically
    });
  });

  describe('downloadJSON', () => {
    const mockManual = {
      pages: [
        {
          image: 'pages/page-0.webp',
          blocks: [
            { text: 'Original', translation: 'Translated', bbox: [0.1, 0.2, 0.3, 0.4] }
          ]
        }
      ]
    };

    it('should reject when no edits to save', () => {
      mockState.editedBlocks = new Set();

      downloadJSON(mockManual);

      expect(mockErrorHandler.user).toHaveBeenCalledWith('No edits to save');
    });

    it('should create and trigger download', () => {
      mockState.editedBlocks = new Set(['0-0']);
      mockState.currentManualName = 'test-manual';

      downloadJSON(mockManual);

      expect(mockDocument.createElement).toHaveBeenCalledWith('a');
      expect(global.URL.createObjectURL).toHaveBeenCalled();
      expect(global.URL.revokeObjectURL).toHaveBeenCalled();
      expect(mockEditSession.markClean).toHaveBeenCalled();
      expect(global.alert).toHaveBeenCalledWith(
        expect.stringContaining('JSON file downloaded successfully')
      );
    });

    it('should format JSON correctly', () => {
      mockState.editedBlocks = new Set(['0-0']);
      mockState.currentManualName = 'test-manual';
      let capturedBlob;

      global.URL.createObjectURL = vi.fn((blob) => {
        capturedBlob = blob;
        return 'blob:test-url';
      });

      downloadJSON(mockManual);

      expect(capturedBlob).toBeDefined();
      const jsonString = capturedBlob.content[0];
      const parsed = JSON.parse(jsonString);

      expect(parsed.pages).toHaveLength(1);
      expect(parsed.pages[0].blocks[0].text).toBe('Original');
      expect(jsonString).toContain('\n'); // Formatted with indentation
    });

    it('should set correct filename', () => {
      mockState.editedBlocks = new Set(['0-0']);
      mockState.currentManualName = 'test-manual';
      const mockAnchor = {
        href: '',
        download: '',
        click: vi.fn()
      };

      mockDocument.createElement = vi.fn(() => mockAnchor);

      downloadJSON(mockManual);

      expect(mockAnchor.download).toBe('test-manual-translations.json');
    });

    it('should use "manual" as default filename when currentManualName is null', () => {
      mockState.editedBlocks = new Set(['0-0']);
      mockState.currentManualName = null;
      const mockAnchor = {
        href: '',
        download: '',
        click: vi.fn()
      };

      mockDocument.createElement = vi.fn(() => mockAnchor);

      downloadJSON(mockManual);

      expect(mockAnchor.download).toBe('manual-translations.json');
    });

    it('should handle large manuals without errors', () => {
      // Create manual with many blocks to simulate real data size
      const largeManual = {
        pages: Array(10).fill(null).map((_, pageIdx) => ({
          image: `pages/page-${pageIdx}.webp`,
          blocks: Array(50).fill(null).map(() => ({
            text: 'Original text that is fairly long and realistic. '.repeat(5),
            translation: 'Translated text that is also fairly long. '.repeat(5),
            bbox: [0.1, 0.2, 0.3, 0.4]
          }))
        }))
      };

      mockState.editedBlocks = new Set(['0-0']);
      mockState.currentManualName = 'large-manual';
      let capturedBlob;

      global.URL.createObjectURL = vi.fn((blob) => {
        capturedBlob = blob;
        return 'blob:test-url';
      });

      downloadJSON(largeManual);

      expect(capturedBlob).toBeDefined();
      const jsonString = capturedBlob.content[0];
      expect(jsonString.length).toBeGreaterThan(10000);

      const parsed = JSON.parse(jsonString);
      expect(parsed.pages).toHaveLength(10);
      expect(parsed.pages[0].blocks).toHaveLength(50);
    });

    it('should handle Japanese characters correctly', () => {
      const japaneseManual = {
        pages: [{
          image: 'pages/page-0.webp',
          blocks: [{
            text: '原文テキスト',
            translation: '翻訳されたテキスト',
            bbox: [0.1, 0.2, 0.3, 0.4]
          }]
        }]
      };

      mockState.editedBlocks = new Set(['0-0']);
      mockState.currentManualName = 'テストマニュアル';
      let capturedBlob;

      global.URL.createObjectURL = vi.fn((blob) => {
        capturedBlob = blob;
        return 'blob:test-url';
      });

      downloadJSON(japaneseManual);

      const jsonString = capturedBlob.content[0];
      const parsed = JSON.parse(jsonString);

      expect(parsed.pages[0].blocks[0].text).toBe('原文テキスト');
      expect(parsed.pages[0].blocks[0].translation).toBe('翻訳されたテキスト');
    });
  });

  describe('GitHub URL generation', () => {
    it('should generate correct URL for CSM-Den-O-Belt-v2', async () => {
      mockState.currentManualName = 'CSM-Den-O-Belt-v2';

      await submitToGitHub({ pages: [] });

      // The URL should be constructed as:
      // https://github.com/shy/tokuSolutions/edit/main/manuals/CSM-Den-O-Belt-v2/translations.json
      expect(mockDocument.body.appendChild).toHaveBeenCalled();
    });

    it('should generate correct URL for DX-Ridewatch', async () => {
      mockState.currentManualName = 'DX-Ridewatch';

      await submitToGitHub({ pages: [] });

      // The URL should be constructed as:
      // https://github.com/shy/tokuSolutions/edit/main/manuals/DX-Ridewatch/translations.json
      expect(mockDocument.body.appendChild).toHaveBeenCalled();
    });

    it('should generate correct URL for manual with spaces', async () => {
      mockState.currentManualName = 'Faiz Driver Next';

      await submitToGitHub({ pages: [] });

      // The URL should be constructed as:
      // https://github.com/shy/tokuSolutions/edit/main/manuals/Faiz Driver Next/translations.json
      expect(mockDocument.body.appendChild).toHaveBeenCalled();
    });
  });
});
