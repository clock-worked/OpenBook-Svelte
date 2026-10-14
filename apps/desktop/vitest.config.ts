import { fileURLToPath } from 'node:url';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

// Vitest infra for the v3 character service-layer matrix
// (docs/character_details_test_plan.md §Strategy, ruling R6):
// node environment by default — pure-TS service layer only.
// Component tests opt into jsdom per-file via `// @vitest-environment jsdom`.
export default defineConfig({
  plugins: [svelte({ hot: false })],
  resolve: {
    // 'browser' makes the bare `svelte` import resolve to the client build
    // (index-client) instead of the server build — required for mount() in
    // jsdom component tests.
    conditions: ['browser'],
    alias: {
      $lib: fileURLToPath(new URL('./src/lib', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    // Only the co-located v3 matrix; the pre-existing node:test files
    // (src/lib/services/*.test.ts) are outside __tests__/ and stay out of scope.
    include: ['src/**/__tests__/**/*.test.ts'],
  },
});
