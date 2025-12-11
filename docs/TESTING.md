# Testing Setup

Unit testing infrastructure for the TokuSolutions web viewer.

## Framework

**Vitest** - Fast, modern test runner with native ES modules support
- Jest-compatible API (familiar syntax)
- Built-in coverage reporting
- Watch mode with hot reload
- Visual UI for debugging tests

## Quick Start

```bash
# Install dependencies (first time only)
npm install

# Run tests once
npm test -- --run

# Run tests in watch mode (auto-rerun on changes)
npm test

# Run tests with visual UI
npm run test:ui

# Generate coverage report
npm run test:coverage -- --run
```

## Test Files

```
web/tests/
├── README.md           # Test documentation
├── config.test.js      # Configuration validation (9 tests)
├── state.test.js       # State management (13 tests)
└── utils.test.js       # Utility functions (26 tests)
```

## Coverage

Current coverage: **48 tests passing**

| Module | Coverage | Status |
|--------|----------|--------|
| config.js | 100% | ✅ Fully tested |
| utils.js | 100% | ✅ Fully tested |
| state.js | 98.63% | ✅ Fully tested |
| editor.js | 0% | ⏳ Future work |
| renderer.js | 0% | ⏳ Future work |
| navigation.js | 0% | ⏳ Future work |
| github.js | 0% | ⏳ Future work |
| errors.js | 0% | ⏳ Future work |
| image-loader.js | 0% | ⏳ Future work |

## CI/CD Integration

Tests run automatically on GitHub Actions:
- **Trigger**: Push to main or PR (when web/ files change)
- **Runs**: All tests + coverage generation
- **Reports**: Uploaded to Codecov (optional)
- **Workflow**: [.github/workflows/test.yml](../.github/workflows/test.yml)

## What's Tested

### Pure Functions (100% coverage)
- `debounce()` - Function execution delay
- `throttle()` - Rate limiting
- `sanitizeText()` - XSS prevention
- `validateBbox()` - Data validation
- `clampBbox()` - Boundary enforcement

### State Management (98.63% coverage)
- `EditSession.start()` - Session initialization
- `EditSession.markDirty()` - Track unsaved changes
- `EditSession.markClean()` - Clear unsaved flag
- `EditSession.clear()` - Reset session
- `EditSession.canNavigateAway()` - Prevent data loss

### Configuration (100% coverage)
- BBOX_DEFAULTS validation
- BBOX_CONSTRAINTS validation
- UI_TIMINGS validation
- GITHUB_CONFIG validation

## Future Test Additions

To increase coverage, add tests for:

1. **DOM Manipulation** (renderer.js)
   - Requires jsdom/happy-dom with full document setup
   - Test element creation, event listeners, rendering

2. **User Interactions** (editor.js)
   - Mock DOM events (click, input, focus)
   - Test edit mode toggle, block creation/deletion

3. **Navigation** (navigation.js)
   - Mock window.location and history API
   - Test route changes, hash navigation

4. **API Calls** (github.js)
   - Mock fetch and Octokit
   - Test OAuth flow, PR creation, fork workflow

5. **Error Handling** (errors.js)
   - Mock console.error and alert
   - Test error formatting and user messages

## Local Development

### Watch Mode
```bash
npm test
# Press 'a' to run all tests
# Press 'f' to run only failed tests
# Press 'q' to quit
```

### Coverage Reports
HTML coverage report is generated at `coverage/index.html`:
```bash
npm run test:coverage -- --run
open coverage/index.html
```

### Visual UI
```bash
npm run test:ui
# Opens browser with visual test interface
```

## Writing New Tests

Example test structure:
```javascript
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { myFunction } from '../js/myModule.js';

describe('myFunction', () => {
  beforeEach(() => {
    // Reset state before each test
  });

  it('should do the expected thing', () => {
    expect(myFunction('input')).toBe('expected output');
  });

  it('should handle edge cases', () => {
    expect(myFunction(null)).toBe(null);
  });
});
```

### Mocking Browser APIs
```javascript
import { vi } from 'vitest';

// Mock window.confirm
const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
// ... test code ...
confirmSpy.mockRestore();

// Mock timers
vi.useFakeTimers();
vi.advanceTimersByTime(100);
vi.useRealTimers();
```

## Continuous Improvement

Goals:
- [ ] Increase coverage to 80% overall
- [ ] Add DOM manipulation tests
- [ ] Add integration tests for full workflows
- [ ] Add E2E tests with Playwright (future)

## Resources

- [Vitest Documentation](https://vitest.dev/)
- [Test Files](../web/tests/)
- [GitHub Actions Workflow](../.github/workflows/test.yml)
