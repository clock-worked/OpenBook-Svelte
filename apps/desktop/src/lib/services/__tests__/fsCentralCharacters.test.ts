import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeCharacterFile } from './helpers/fixtures';

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

import { apiPostJson, apiPostVoid } from '../apiClient';
import {
  applyDialogueStatsDelta,
  invalidateCentralCharactersCache,
  readCentralCharacters,
  setRootDirHandle,
  writeCentralCharacters,
  writeDialogue,
} from '../fs';

describe('readCentralCharacters', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // ADR 0012: the central-characters read cache is module state; without
    // this reset the tests reuse root 'Book' and would see stale hits.
    invalidateCentralCharactersCache();
  });

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
  // The handle and the read cache are module state; the reset below clears
  // both (setRootDirHandle also invalidates the cache), so order is free.
  beforeEach(() => {
    vi.resetAllMocks();
    setRootDirHandle(null);
    invalidateCentralCharactersCache();
  });

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

describe('central characters read cache (ADR 0012)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setRootDirHandle(null);
    invalidateCentralCharactersCache();
  });

  it('serves a second read from the cache without extra requests', async () => {
    vi.mocked(apiPostJson)
      .mockResolvedValueOnce({ files: ['Alice.json'] })
      .mockResolvedValueOnce({ content: JSON.stringify(makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' })) });

    const first = await readCentralCharacters('Book');
    const second = await readCentralCharacters('Book');

    expect(second).toEqual(first);
    // list + one character file, exactly once for the whole session read.
    expect(apiPostJson).toHaveBeenCalledTimes(2);
  });

  it('re-reads when the book root changes', async () => {
    const legacy = { formatVersion: '2.0', characters: [{ id: 'alice', name: 'Alice' }] };
    vi.mocked(apiPostJson)
      .mockResolvedValueOnce({ files: [] })
      .mockResolvedValueOnce({ content: JSON.stringify(legacy) })
      .mockResolvedValueOnce({ files: [] })
      .mockResolvedValueOnce({ content: null });

    expect(await readCentralCharacters('Book A')).toEqual(legacy);
    expect(await readCentralCharacters('Book B')).toBeNull();
    expect(apiPostJson).toHaveBeenCalledTimes(4);
  });

  it('invalidates after a central characters write', async () => {
    const legacy = { formatVersion: '2.0', characters: [{ id: 'alice', name: 'Alice' }] };
    vi.mocked(apiPostJson)
      .mockResolvedValueOnce({ files: [] })
      .mockResolvedValueOnce({ content: JSON.stringify(legacy) });
    await readCentralCharacters('Book');
    expect(apiPostJson).toHaveBeenCalledTimes(2);

    vi.mocked(apiPostVoid).mockResolvedValue(undefined);
    expect(await writeCentralCharacters('Book', legacy as never)).toBe(true);
    expect(apiPostVoid).toHaveBeenCalledWith('/api/save', {
      file_path: 'characters.json',
      content: legacy,
    });

    // The write invalidated the cache: the next read goes back to the
    // network instead of serving the pre-write value.
    vi.mocked(apiPostJson)
      .mockResolvedValueOnce({ files: [] })
      .mockResolvedValueOnce({ content: JSON.stringify(legacy) });
    await readCentralCharacters('Book');
    expect(apiPostJson).toHaveBeenCalledTimes(4);
  });
});

describe('dialogue save endpoints (ADR 0012)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setRootDirHandle(null);
    invalidateCentralCharactersCache();
  });

  it('routes dialogue saves to the combined save-dialogue endpoint', async () => {
    const dialogue = { formatVersion: '2.0', lines: [{ characterId: 'alice', text: 'hi' }] };
    vi.mocked(apiPostVoid).mockResolvedValue(undefined);

    expect(await writeDialogue('Ch 1/dialogue.json', dialogue as never)).toBe(true);

    // One round trip: the combined endpoint applies the stats delta itself,
    // so there is no separate delta call in sidecar mode.
    expect(apiPostVoid).toHaveBeenCalledTimes(1);
    expect(apiPostVoid).toHaveBeenCalledWith('/api/save-dialogue', {
      file_path: 'Ch 1/dialogue.json',
      content: dialogue,
    });
  });

  it('routes non-dialogue saves to the plain save endpoint', async () => {
    vi.mocked(apiPostVoid).mockResolvedValue(undefined);

    expect(await writeCentralCharacters('Book', { formatVersion: '2.0', characters: [] } as never)).toBe(true);

    expect(apiPostVoid).toHaveBeenCalledTimes(1);
    expect(apiPostVoid).toHaveBeenCalledWith('/api/save', {
      file_path: 'characters.json',
      content: { formatVersion: '2.0', characters: [] },
    });
  });

  it('fires a fire-and-forget stats delta after a direct FSA dialogue write', async () => {
    const written: string[] = [];
    const root = {
      kind: 'directory',
      name: 'Book',
      getDirectoryHandle: async () => ({
        kind: 'directory',
        getFileHandle: async () => ({
          createWritable: async () => ({
            write: async (contents: string) => { written.push(contents); },
            close: async () => { /* written to disk */ },
          }),
        }),
      }),
      getFileHandle: async () => {
        throw new DOMException('not found', 'NotFoundError');
      },
    };
    setRootDirHandle(root as unknown as FileSystemDirectoryHandle);
    vi.mocked(apiPostVoid).mockResolvedValue(undefined);

    const dialogue = { formatVersion: '2.0', lines: [{ characterId: 'alice', text: 'hi' }] };
    expect(await writeDialogue('Ch 1/dialogue.json', dialogue as never)).toBe(true);

    // The file went straight to disk through the handle...
    expect(written).toHaveLength(1);
    // ...and the sidecar only receives the fire-and-forget delta call.
    expect(apiPostVoid).toHaveBeenCalledTimes(1);
    expect(apiPostVoid).toHaveBeenCalledWith('/api/apply-dialogue-stats', {
      file_path: 'Ch 1/dialogue.json',
    });
  });

  it('warns without throwing when the stats delta fails', async () => {
    vi.mocked(apiPostVoid).mockRejectedValue(new Error('backend down'));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => { /* swallow */ });

    expect(await applyDialogueStatsDelta('Ch 1/dialogue.json')).toBe(false);
    expect(apiPostVoid).toHaveBeenCalledWith('/api/apply-dialogue-stats', {
      file_path: 'Ch 1/dialogue.json',
    });

    warn.mockRestore();
  });
});