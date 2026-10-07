import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Vitest infra for the v3 character service-layer matrix
// (docs/character_details_test_plan.md §Strategy, ruling R6):
// node environment, NO jsdom — pure-TS service layer only.
export default defineConfig({
  resolve: {
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
