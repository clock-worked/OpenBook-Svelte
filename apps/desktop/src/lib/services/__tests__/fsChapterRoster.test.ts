// ============================================================================
// Issue #2 regression: a missing chapter roster file `<Ch>/<Ch>.characters.json`
// reads as an empty roster, not an error. Uses the REAL fs.ts over a mocked
// apiClient, so the sidecar's 404 for the missing file is what is exercised.
// ============================================================================

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  API_ENDPOINTS: {
    listFiles: '/api/list-files',
    readText: '/api/read-text',
    save: '/api/save',
    saveDialogue: '/api/save-dialogue',
    applyDialogueStats: '/api/apply-dialogue-stats',
  },
  apiPostJson: vi.fn(),
  apiPostVoid: vi.fn(),
  apiRequestJson: vi.fn(),
  toApiClientError: vi.fn(),
}));
vi.mock('../persistence', () => ({ storeProjectHandle: vi.fn() }));
vi.mock('$lib/stores/settings', async () => {
  const { writable } = await import('svelte/store');
  return { bookRootPathOverride: writable(null) };
});

import { apiPostJson } from '../apiClient';
import { getCharactersPath } from '../fs';
import { loadChapterCharactersData } from '../chapterCharacterRepository';

const CHAPTER = '47-Interlude-Rats';
const ROSTER = `${CHAPTER}/${CHAPTER}.characters.json`;

describe('missing chapter roster (issue #2)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('loadChapterCharactersData reads the roster optionally and resolves to null without an error', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    // The sidecar's answer to a missing file read without `optional`.
    vi.mocked(apiPostJson).mockRejectedValue(new Error(`File not found: ${ROSTER}`));

    const data = await loadChapterCharactersData('Book', getCharactersPath(CHAPTER));

    expect(data).toBeNull();
    expect(apiPostJson).toHaveBeenCalledWith('/api/read-text', { file_path: ROSTER, optional: true });
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
