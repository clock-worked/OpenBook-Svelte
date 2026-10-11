import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeCharacterFile } from './helpers/fixtures';

vi.mock('../apiClient', () => ({
  API_ENDPOINTS: { listFiles: '/api/list-files', readText: '/api/read-text' },
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
import { readCentralCharacters } from '../fs';

describe('readCentralCharacters', () => {
  beforeEach(() => vi.resetAllMocks());

  it('uses v3 records without requesting the retired root file', async () => {
    vi.mocked(apiPostJson)
      .mockResolvedValueOnce({ files: ['Alice.json'] })
      .mockResolvedValueOnce({ content: JSON.stringify(makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' })) });

    const result = await readCentralCharacters('Book');

    expect(result?.formatVersion).toBe('3.0');
    expect(result?.characters[0].name).toBe('Alice');
    expect(apiPostJson).not.toHaveBeenCalledWith('/api/read-text', expect.objectContaining({ file_path: 'characters.json' }));
  });

  it('reads legacy characters optionally when the folder is absent', async () => {
    const legacy = { formatVersion: '2.0', characters: [{ id: 'alice', name: 'Alice' }] };
    vi.mocked(apiPostJson)
      .mockResolvedValueOnce({ files: [] })
      .mockResolvedValueOnce({ content: JSON.stringify(legacy) });

    expect(await readCentralCharacters('Book')).toEqual(legacy);
    expect(apiPostJson).toHaveBeenLastCalledWith('/api/read-text', { file_path: 'characters.json', optional: true });
  });

  it('returns null when neither storage format exists', async () => {
    vi.mocked(apiPostJson)
      .mockResolvedValueOnce({ files: [] })
      .mockResolvedValueOnce({ content: null });

    expect(await readCentralCharacters('Book')).toBeNull();
    expect(apiPostJson).toHaveBeenLastCalledWith('/api/read-text', { file_path: 'characters.json', optional: true });
  });
});