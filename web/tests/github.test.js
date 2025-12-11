import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

describe('GitHub Integration Tests', () => {
  let mockState;
  let mockEditSession;
  let mockErrorHandler;
  let mockOctokit;
  let submitToGitHub;
  let downloadJSON;

  beforeEach(async () => {
    // Reset modules
    vi.resetModules();

    // Mock timers for OAuth polling
    vi.useFakeTimers();

    // Mock dependencies
    mockState = {
      editedBlocks: new Set(['0-0'])
    };

    mockEditSession = {
      markClean: vi.fn()
    };

    mockErrorHandler = {
      user: vi.fn(),
      github: vi.fn((error, operation) => {
        // Simulate the real ErrorHandler.github behavior for 401 errors
        if (error.status === 401) {
          global.localStorage.removeItem('github_token');
        }
        return false;
      })
    };

    // Mock Octokit
    mockOctokit = {
      rest: {
        users: {
          getAuthenticated: vi.fn()
        },
        repos: {
          createFork: vi.fn(),
          get: vi.fn(),
          getContent: vi.fn(),
          createOrUpdateFileContents: vi.fn()
        },
        git: {
          getRef: vi.fn(),
          createRef: vi.fn()
        },
        pulls: {
          create: vi.fn()
        }
      }
    };

    // Mock globals
    global.Octokit = {
      Octokit: vi.fn(() => mockOctokit)
    };

    global.localStorage = {
      getItem: vi.fn(),
      setItem: vi.fn(),
      removeItem: vi.fn()
    };

    global.alert = vi.fn();
    global.confirm = vi.fn();
    global.fetch = vi.fn();
    global.window = { open: vi.fn() };

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
        CLIENT_ID: 'test-client-id',
        REPO: 'shy/tokuSolutions'
      },
      UI_TIMINGS: {
        FORK_WAIT_TIME: 10 // Short wait for tests
      }
    }));

    // Import after mocks are set up
    const githubModule = await import('../js/github.js?' + Date.now());
    submitToGitHub = githubModule.submitToGitHub;
    downloadJSON = githubModule.downloadJSON;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  describe('submitToGitHub - Validation', () => {
    it('should reject when no edits to save', async () => {
      mockState.editedBlocks = new Set();

      await submitToGitHub({ meta: { name: 'test' }, pages: [] });

      expect(mockErrorHandler.user).toHaveBeenCalledWith('No edits to save');
    });
  });

  describe('submitToGitHub - OAuth Flow', () => {
    const mockManual = {
      meta: { name: 'test-manual', source: 'Test.pdf' },
      pages: []
    };

    it('should initiate OAuth when no token exists', async () => {
      global.localStorage.getItem.mockReturnValue(null);
      global.confirm.mockReturnValue(true);

      // Mock device code request
      global.fetch.mockResolvedValueOnce({
        json: () => Promise.resolve({
          device_code: 'device123',
          user_code: 'USER-CODE',
          verification_uri: 'https://github.com/login/device',
          expires_in: 900,
          interval: 5
        })
      });

      // Mock token polling - return token immediately to avoid timeout
      global.fetch.mockResolvedValueOnce({
        json: () => Promise.resolve({
          access_token: 'gho_test_token'
        })
      });

      // Mock all subsequent GitHub API calls for the resumed submitToGitHub
      mockOctokit.rest.users.getAuthenticated.mockResolvedValue({
        data: { login: 'testuser' }
      });
      mockOctokit.rest.repos.createFork.mockResolvedValue({});
      mockOctokit.rest.repos.get.mockResolvedValue({
        data: { default_branch: 'main' }
      });
      mockOctokit.rest.git.getRef.mockResolvedValue({
        data: { object: { sha: 'abc123' } }
      });
      mockOctokit.rest.git.createRef.mockResolvedValue({});
      mockOctokit.rest.repos.getContent.mockResolvedValue({
        data: { sha: 'file-sha' }
      });
      mockOctokit.rest.repos.createOrUpdateFileContents.mockResolvedValue({});
      mockOctokit.rest.pulls.create.mockResolvedValue({
        data: { number: 1, html_url: 'https://github.com/pr/1' }
      });

      // Start the OAuth flow
      const submitPromise = submitToGitHub(mockManual);

      // Advance timers to skip the polling interval wait (5000ms)
      await vi.advanceTimersByTimeAsync(5000);

      await submitPromise;

      // Should save pending edit
      expect(global.localStorage.setItem).toHaveBeenCalledWith(
        'pending_edit',
        expect.stringContaining('test-manual')
      );

      // Should open browser for auth
      expect(global.window.open).toHaveBeenCalledWith(
        'https://github.com/login/device',
        '_blank'
      );

      // Should store token and resume submission
      expect(global.localStorage.setItem).toHaveBeenCalledWith(
        'github_token',
        'gho_test_token'
      );
    });

    it('should cancel OAuth when user declines', async () => {
      global.localStorage.getItem.mockReturnValue(null);
      global.confirm.mockReturnValue(false);

      global.fetch.mockResolvedValueOnce({
        json: () => Promise.resolve({
          device_code: 'device123',
          user_code: 'USER-CODE',
          verification_uri: 'https://github.com/login/device',
          expires_in: 900,
          interval: 5
        })
      });

      await submitToGitHub(mockManual);

      expect(global.window.open).not.toHaveBeenCalled();
      expect(global.alert).toHaveBeenCalledWith(
        expect.stringContaining('cancelled')
      );
    });
  });

  describe('submitToGitHub - Fork and PR Creation', () => {
    const mockManual = {
      meta: { name: 'test-manual', source: 'Test.pdf' },
      pages: [
        {
          image: 'pages/page-0.webp',
          blocks: [
            { text: 'Original', translation: 'Translated', bbox: [0.1, 0.2, 0.3, 0.4] }
          ]
        }
      ]
    };

    beforeEach(() => {
      global.localStorage.getItem.mockReturnValue('gho_test_token');
    });

    it('should create fork, branch, commit, and PR', async () => {
      // Mock all GitHub API calls
      mockOctokit.rest.users.getAuthenticated.mockResolvedValue({
        data: { login: 'testuser' }
      });

      mockOctokit.rest.repos.createFork.mockResolvedValue({
        data: { name: 'tokuSolutions' }
      });

      mockOctokit.rest.repos.get.mockResolvedValue({
        data: { default_branch: 'main' }
      });

      mockOctokit.rest.git.getRef.mockResolvedValue({
        data: { object: { sha: 'abc123' } }
      });

      mockOctokit.rest.git.createRef.mockResolvedValue({
        data: { ref: 'refs/heads/edit-branch' }
      });

      mockOctokit.rest.repos.getContent.mockResolvedValue({
        data: { sha: 'file-sha-456' }
      });

      mockOctokit.rest.repos.createOrUpdateFileContents.mockResolvedValue({
        data: { commit: { sha: 'commit-sha' } }
      });

      mockOctokit.rest.pulls.create.mockResolvedValue({
        data: {
          number: 42,
          html_url: 'https://github.com/shy/tokuSolutions/pull/42'
        }
      });

      const submitPromise = submitToGitHub(mockManual);

      // Advance timers for fork wait time (10ms in config)
      await vi.advanceTimersByTimeAsync(10);

      const result = await submitPromise;

      expect(result).toBe(true);

      // Verify workflow steps
      expect(mockOctokit.rest.users.getAuthenticated).toHaveBeenCalled();
      expect(mockOctokit.rest.repos.createFork).toHaveBeenCalled();
      expect(mockOctokit.rest.git.createRef).toHaveBeenCalled();

      // Verify file update
      expect(mockOctokit.rest.repos.createOrUpdateFileContents).toHaveBeenCalledWith(
        expect.objectContaining({
          path: 'manuals/test-manual/translations.json',
          message: 'Update translations for Test.pdf',
          branch: expect.stringContaining('edit-test-manual')
        })
      );

      // Verify PR creation
      expect(mockOctokit.rest.pulls.create).toHaveBeenCalledWith(
        expect.objectContaining({
          owner: 'shy',
          repo: 'tokuSolutions',
          title: 'Translation edits for Test.pdf',
          base: 'main',
          head: expect.stringContaining('testuser:edit-test-manual')
        })
      );

      // Verify cleanup
      expect(mockState.editedBlocks.size).toBe(0);
      expect(mockEditSession.markClean).toHaveBeenCalled();
      expect(global.alert).toHaveBeenCalledWith(
        expect.stringContaining('Pull request created successfully')
      );
    });

    it('should handle fork already exists gracefully', async () => {
      mockOctokit.rest.users.getAuthenticated.mockResolvedValue({
        data: { login: 'testuser' }
      });

      // Fork fails
      mockOctokit.rest.repos.createFork.mockRejectedValue(
        new Error('Fork already exists')
      );

      // But continues with rest of workflow
      mockOctokit.rest.repos.get.mockResolvedValue({
        data: { default_branch: 'main' }
      });

      mockOctokit.rest.git.getRef.mockResolvedValue({
        data: { object: { sha: 'abc123' } }
      });

      mockOctokit.rest.git.createRef.mockResolvedValue({});
      mockOctokit.rest.repos.getContent.mockResolvedValue({
        data: { sha: 'file-sha' }
      });
      mockOctokit.rest.repos.createOrUpdateFileContents.mockResolvedValue({});
      mockOctokit.rest.pulls.create.mockResolvedValue({
        data: { number: 42, html_url: 'https://github.com/test/pr/42' }
      });

      const submitPromise = submitToGitHub(mockManual);

      // Advance timers for fork wait time (10ms in config)
      await vi.advanceTimersByTimeAsync(10);

      const result = await submitPromise;

      expect(result).toBe(true);
      expect(mockOctokit.rest.pulls.create).toHaveBeenCalled();
    });

    it('should handle authentication errors', async () => {
      const authError = new Error('Unauthorized');
      authError.status = 401;

      mockOctokit.rest.users.getAuthenticated.mockRejectedValue(authError);

      const result = await submitToGitHub(mockManual);

      expect(result).toBe(false);
      expect(mockErrorHandler.github).toHaveBeenCalled();
      expect(global.localStorage.removeItem).toHaveBeenCalledWith('github_token');
    });

    it('should generate correct file content', async () => {
      mockOctokit.rest.users.getAuthenticated.mockResolvedValue({
        data: { login: 'testuser' }
      });
      mockOctokit.rest.repos.createFork.mockResolvedValue({});
      mockOctokit.rest.repos.get.mockResolvedValue({
        data: { default_branch: 'main' }
      });
      mockOctokit.rest.git.getRef.mockResolvedValue({
        data: { object: { sha: 'abc' } }
      });
      mockOctokit.rest.git.createRef.mockResolvedValue({});
      mockOctokit.rest.repos.getContent.mockResolvedValue({
        data: { sha: 'file-sha' }
      });
      mockOctokit.rest.repos.createOrUpdateFileContents.mockResolvedValue({});
      mockOctokit.rest.pulls.create.mockResolvedValue({
        data: { number: 1, html_url: 'https://github.com/pr/1' }
      });

      const submitPromise = submitToGitHub(mockManual);

      // Advance timers for fork wait time (10ms in config)
      await vi.advanceTimersByTimeAsync(10);

      await submitPromise;

      const call = mockOctokit.rest.repos.createOrUpdateFileContents.mock.calls[0][0];
      const content = Buffer.from(call.content, 'base64').toString('utf-8');
      const parsed = JSON.parse(content);

      expect(parsed.meta.name).toBe('test-manual');
      expect(parsed.pages).toHaveLength(1);
      expect(parsed.pages[0].blocks[0].text).toBe('Original');
    });

    it('should handle large manuals without stack overflow', async () => {
      // Create manual with many blocks to simulate real data size
      const largeManual = {
        meta: { name: 'large-manual', source: 'Large.pdf' },
        pages: Array(10).fill(null).map((_, pageIdx) => ({
          image: `pages/page-${pageIdx}.webp`,
          blocks: Array(50).fill(null).map((_, blockIdx) => ({
            text: 'Original text that is fairly long and realistic. '.repeat(5),
            translation: 'Translated text that is also fairly long. '.repeat(5),
            bbox: [0.1, 0.2, 0.3, 0.4]
          }))
        }))
      };

      mockOctokit.rest.users.getAuthenticated.mockResolvedValue({
        data: { login: 'testuser' }
      });
      mockOctokit.rest.repos.createFork.mockResolvedValue({});
      mockOctokit.rest.repos.get.mockResolvedValue({
        data: { default_branch: 'main' }
      });
      mockOctokit.rest.git.getRef.mockResolvedValue({
        data: { object: { sha: 'abc' } }
      });
      mockOctokit.rest.git.createRef.mockResolvedValue({});
      mockOctokit.rest.repos.getContent.mockResolvedValue({
        data: { sha: 'file-sha' }
      });
      mockOctokit.rest.repos.createOrUpdateFileContents.mockResolvedValue({});
      mockOctokit.rest.pulls.create.mockResolvedValue({
        data: { number: 1, html_url: 'https://github.com/pr/1' }
      });

      mockState.editedBlocks = new Set(['0-0']);
      const submitPromise = submitToGitHub(largeManual);

      await vi.advanceTimersByTimeAsync(10);
      const result = await submitPromise;

      expect(result).toBe(true);

      // Verify base64 encoding succeeded for large content
      const call = mockOctokit.rest.repos.createOrUpdateFileContents.mock.calls[0][0];
      expect(call.content).toBeDefined();
      expect(call.content.length).toBeGreaterThan(1000);

      // Verify content is valid base64 and decodes correctly
      const content = Buffer.from(call.content, 'base64').toString('utf-8');
      const parsed = JSON.parse(content);
      expect(parsed.pages).toHaveLength(10);
      expect(parsed.pages[0].blocks).toHaveLength(50);
    });

    it('should handle Japanese characters in manual names', async () => {
      const japaneseManual = {
        meta: { name: 'テストマニュアル', source: '仮面ライダー.pdf' },
        pages: [{
          image: 'pages/page-0.webp',
          blocks: [{
            text: '原文テキスト',
            translation: '翻訳されたテキスト',
            bbox: [0.1, 0.2, 0.3, 0.4]
          }]
        }]
      };

      mockOctokit.rest.users.getAuthenticated.mockResolvedValue({
        data: { login: 'testuser' }
      });
      mockOctokit.rest.repos.createFork.mockResolvedValue({});
      mockOctokit.rest.repos.get.mockResolvedValue({
        data: { default_branch: 'main' }
      });
      mockOctokit.rest.git.getRef.mockResolvedValue({
        data: { object: { sha: 'abc' } }
      });
      mockOctokit.rest.git.createRef.mockResolvedValue({});
      mockOctokit.rest.repos.getContent.mockResolvedValue({
        data: { sha: 'file-sha' }
      });
      mockOctokit.rest.repos.createOrUpdateFileContents.mockResolvedValue({});
      mockOctokit.rest.pulls.create.mockResolvedValue({
        data: { number: 1, html_url: 'https://github.com/pr/1' }
      });

      mockState.editedBlocks = new Set(['0-0']);
      const submitPromise = submitToGitHub(japaneseManual);

      await vi.advanceTimersByTimeAsync(10);
      const result = await submitPromise;

      expect(result).toBe(true);

      // Verify UTF-8 characters encoded and decoded correctly
      const call = mockOctokit.rest.repos.createOrUpdateFileContents.mock.calls[0][0];
      const content = Buffer.from(call.content, 'base64').toString('utf-8');
      const parsed = JSON.parse(content);

      expect(parsed.meta.name).toBe('テストマニュアル');
      expect(parsed.meta.source).toBe('仮面ライダー.pdf');
      expect(parsed.pages[0].blocks[0].text).toBe('原文テキスト');
      expect(parsed.pages[0].blocks[0].translation).toBe('翻訳されたテキスト');
    });
  });

  describe('downloadJSON', () => {
    const mockManual = {
      meta: { name: 'test-manual', source: 'Test.pdf' },
      pages: []
    };

    beforeEach(() => {
      // Mock DOM APIs
      const mockAnchor = {
        href: '',
        download: '',
        click: vi.fn()
      };

      global.document = {
        createElement: vi.fn(() => mockAnchor),
        body: {
          appendChild: vi.fn(),
          removeChild: vi.fn()
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
    });

    it('should reject when no edits to save', () => {
      mockState.editedBlocks = new Set();

      downloadJSON(mockManual);

      expect(mockErrorHandler.user).toHaveBeenCalledWith('No edits to save');
    });

    it('should create and trigger download', () => {
      mockState.editedBlocks = new Set(['0-0']);

      downloadJSON(mockManual);

      expect(global.document.createElement).toHaveBeenCalledWith('a');
      expect(global.URL.createObjectURL).toHaveBeenCalled();
      expect(global.URL.revokeObjectURL).toHaveBeenCalled();
      expect(mockEditSession.markClean).toHaveBeenCalled();
      expect(global.alert).toHaveBeenCalledWith(
        expect.stringContaining('Downloaded test-manual-edited.json')
      );
    });

    it('should format JSON correctly', () => {
      mockState.editedBlocks = new Set(['0-0']);
      let capturedBlob;

      global.URL.createObjectURL = vi.fn((blob) => {
        capturedBlob = blob;
        return 'blob:test-url';
      });

      downloadJSON(mockManual);

      expect(capturedBlob).toBeDefined();
      const jsonString = capturedBlob.content[0];
      const parsed = JSON.parse(jsonString);

      expect(parsed.meta.name).toBe('test-manual');
      expect(jsonString).toContain('\n'); // Formatted with indentation
    });

    it('should set correct filename', () => {
      mockState.editedBlocks = new Set(['0-0']);
      const mockAnchor = {
        href: '',
        download: '',
        click: vi.fn()
      };

      global.document.createElement = vi.fn(() => mockAnchor);

      downloadJSON(mockManual);

      expect(mockAnchor.download).toBe('test-manual-edited.json');
    });
  });
});
