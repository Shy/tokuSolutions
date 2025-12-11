import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'happy-dom',
    globals: false,
    include: ['web/tests/**/*.test.js'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['web/js/**/*.js'],
      exclude: ['web/tests/**']
    }
  },
  resolve: {
    alias: {
      '@': '/web/js'
    }
  }
});
