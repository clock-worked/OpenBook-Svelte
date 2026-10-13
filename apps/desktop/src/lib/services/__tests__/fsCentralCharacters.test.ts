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
import { readCentralCharacters, setRootDirHandle } from '../fs';

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

describe('readCentralCharacters (transport seam)', () => {
  // Run after the handle-less tests above on purpose: setRootDirHandle
  // attaches module state that no exported API can clear, so these tests
  // must not go first.
  beforeEach(() => vi.resetAllMocks());

  function fakeFileHandle(name: string, json: unknown) {
    return {
      kind: 'file',
      name,
      getFile: async () => ({ text: async () => JSON.stringify(json) }),
    };
  }

  it('serves the folder from the FSA handle without touching the sidecar', async () => {
    const aliceFile = makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' });
    const charactersDir = {
      kind: 'directory',
      values: async function* () {
        yield fakeFileHandle('Alice.json', aliceFile);
        yield { kind: 'file', name: 'notes.txt' };
        yield { kind: 'directory', name: 'drafts' };
      },
      getFileHandle: async (name: string) => {
        if (name === 'Alice.json') return fakeFileHandle(name, aliceFile);
        throw new DOMException('not found', 'NotFoundError');
      },
    };
    const root = {
      kind: 'directory',
      name: 'Book',
      getDirectoryHandle: async (name: string) => {
        if (name === 'characters') return charactersDir;
        throw new DOMException('not found', 'NotFoundError');
      },
      getFileHandle: async () => {
        throw new DOMException('not found', 'NotFoundError');
      },
    };
    setRootDirHandle(root as unknown as FileSystemDirectoryHandle);

    const result = await readCentralCharacters('Book');

    expect(result?.formatVersion).toBe('3.0');
    expect(result?.characters.map((c) => c.name)).toEqual(['Alice']);
    expect(apiPostJson).not.toHaveBeenCalled();
  });

  it('treats a missing characters folder as empty, then falls back to the sidecar for the legacy file', async () => {
    const notFound = () => Promise.reject(new DOMException('not found', 'NotFoundError'));
    const root = {
      kind: 'directory',
      name: 'Book',
      getDirectoryHandle: notFound,
      getFileHandle: notFound,
    };
    setRootDirHandle(root as unknown as FileSystemDirectoryHandle);
    vi.mocked(apiPostJson).mockResolvedValueOnce({ content: null });

    expect(await readCentralCharacters('Book')).toBeNull();
    expect(apiPostJson).toHaveBeenCalledTimes(1);
    expect(apiPostJson).toHaveBeenCalledWith('/api/read-text', { file_path: 'characters.json', optional: true });
  });
});