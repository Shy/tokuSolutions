# Web App Tests

Unit tests for the TokuSolutions web viewer using Vitest.

## Running Tests

```bash
# Run tests once
npm test

# Run tests in watch mode (auto-rerun on file changes)
npm test -- --watch

# Run tests with UI
npm run test:ui

# Generate coverage report
npm run test:coverage
```

## Test Structure

```
web/tests/
├── config.test.js    # Configuration validation tests
├── state.test.js     # State management and EditSession tests
├── utils.test.js     # Pure utility function tests
└── github.test.js    # GitHub OAuth and PR submission tests
```

## Coverage

Tests currently cover:
- ✅ Utility functions (debounce, throttle, sanitize, bbox validation/clamping)
- ✅ State management (EditSession lifecycle)
- ✅ Configuration validation
- ✅ GitHub integration (hybrid copy+paste workflow, modal creation, JSON download)

Not yet covered (future work):
- DOM manipulation (renderer.js, editor.js)
- Navigation and routing
- Error handling UI

## CI/CD

Tests run automatically on:
- Push to main branch (for web/ changes)
- Pull requests
- See [.github/workflows/test.yml](../../.github/workflows/test.yml)

## Writing Tests

Follow existing patterns:
- Use `describe` for grouping related tests
- Use descriptive test names
- Mock browser APIs (confirm, alert, etc.) when needed
- Reset state in `beforeEach` hooks

Example:
```javascript
import { describe, it, expect, beforeEach } from 'vitest';
import { myFunction } from '../js/myModule.js';

describe('myFunction', () => {
  it('should do the thing', () => {
    expect(myFunction('input')).toBe('expected');
  });
});
```
