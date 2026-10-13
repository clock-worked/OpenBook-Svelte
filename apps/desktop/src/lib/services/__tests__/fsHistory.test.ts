import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { makeCharacterFile } from './helpers/fixtures';

const backend = vi.hoisted(() => ({
  files: new Map<string, string>(),
  failRead: false,
  failWrite: false,
}));

vi.mock('../apiClient', () => ({
  API_ENDPOINTS: { save: 'save', readText: 'read', deleteFile: 'delete', listFiles: 'list' },
  apiPostJson: vi.fn(async (endpoint: string, payload: { file_path: string }) => {
    if (backend.failRead) throw new Error('Backend unavailable');
    if (endpoint === 'list') {
      return { files: [...backend.files.keys()]
        .filter((path) => path.startsWith('characters/') && !path.slice(11).includes('/'))
        .map((path) => path.slice(11)) };
    }
    return { content: backend.files.get(payload.file_path) ?? null };
  }),
  apiPostVoid: vi.fn(async (endpoint: string, payload: { file_path: string; content?: unknown }) => {
    if (backend.failWrite) throw new Error('Disk full');
    if (endpoint === 'delete') backend.files.delete(payload.file_path);
    else backend.files.set(payload.file_path, JSON.stringify(payload.content, null, 2));
  }),
  apiRequestJson: vi.fn(),
  toApiClientError: vi.fn(),
}));
vi.mock('../persistence', () => ({ storeProjectHandle: vi.fn() }));
vi.mock('$lib/stores/settings', async () => {
  const { writable } = await import('svelte/store');
  return { bookRootPathOverride: writable(null) };
});

import { apiPostVoid } from '../apiClient';
import { history } from '../history';
import { captureEditableFiles, deleteFile, listCharacterFiles, setRootDirHandle, writeCharacterFile, writeEditableJson } from '../fs';
import { mergeCentralCharacters, renameCentralCharacter } from '../characterMutationService';

function seed(path: string, content: unknown) {
  backend.files.set(path, JSON.stringify(content, null, 2));
}
function read(path: string) {
  const content = backend.files.get(path);
  return content === undefined ? null : JSON.parse(content);
}

beforeEach(() => {
  backend.files.clear();
  backend.failRead = false;
  backend.failWrite = false;
  history.activate();
  history.clear();
  vi.mocked(apiPostVoid).mockClear();
});

describe('file-backed edit history', () => {
  it('restores text edits, joins, splits and review flags in saved dialogue', async () => {
    const original = { lines: [{ id: 1, text: 'One.' }, { id: 2, text: 'Two.' }], reviewed: false };
    seed('Chapter/dialogue.json', original);
    const edited = { lines: [{ id: 1, text: 'One. Two.' }], reviewed: true };
    expect(await writeEditableJson('Chapter/dialogue.json', edited)).toBe(true);
    await history.undo();
    expect(read('Chapter/dialogue.json')).toEqual(original);
    await history.redo();
    expect(read('Chapter/dialogue.json')).toEqual(edited);
  });

  it('restores character creation and deletion on disk', async () => {
    const alice = makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' });
    await writeCharacterFile('Book', 'Alice.json', alice);
    await history.undo();
    expect(read('characters/Alice.json')).toBeNull();
    await history.redo();
    expect(read('characters/Alice.json')).toEqual(alice);
    await deleteFile('Book', 'characters/Alice.json');
    await history.undo();
    expect(read('characters/Alice.json')).toEqual(alice);
  });

  it('undoes and redoes a real character rename as a single action', async () => {
    const alice = makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' });
    seed('characters/Alice.json', alice);
    expect((await renameCentralCharacter('Book', { fileName: 'Alice.json', data: alice }, 'Alicia')).ok).toBe(true);
    expect(read('characters/Alice.json')).toBeNull();
    expect(read('characters/Alicia.json').title).toBe('Alicia');
    await history.undo();
    expect(read('characters/Alice.json')).toEqual(alice);
    expect(read('characters/Alicia.json')).toBeNull();
    expect(get(history).canUndo).toBe(false);
    await history.redo();
    expect(read('characters/Alicia.json').title).toBe('Alicia');
    expect(read('characters/Alice.json')).toBeNull();
  });

  it('restores every file affected by a real character merge', async () => {
    const source = makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' });
    const target = makeCharacterFile({ guid: 'CC03DD04', title: 'Bob' });
    seed('characters/Alice.json', source);
    seed('characters/Bob.json', target);
    seed('Chapter/dialogue.json', { formatVersion: '3.2', lines: [{ id: 1, characterId: source.guid, text: 'Hello' }] });
    seed('voices.json', { formatVersion: '2.0', voices: [], assignments: [{ characterId: source.guid, voiceId: 'voice' }] });
    const before = Object.fromEntries(backend.files);
    const result = await mergeCentralCharacters('Book', [{ title: 'Chapter' }],
      { fileName: 'Alice.json', data: source }, { fileName: 'Bob.json', data: target });
    expect(result.ok).toBe(true);
    expect(read('Chapter/dialogue.json').lines[0].characterId).toBe(target.guid);
    const after = Object.fromEntries(backend.files);
    await history.undo();
    expect(Object.fromEntries(backend.files)).toEqual(before);
    expect(get(history).canUndo).toBe(false);
    await history.redo();
    expect(Object.fromEntries(backend.files)).toEqual(after);
  });

  it('does not record automatic stats updates or timestamp-only rewrites', async () => {
    const alice = makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' });
    seed('characters/Alice.json', alice);
    await writeCharacterFile('Book', 'Alice.json', { ...alice, updatedAt: 'new' });
    expect(get(history).canUndo).toBe(false);
    await writeCharacterFile('Book', 'Alice.json', { ...alice, stats: { totalLines: 5, chapterCount: 1 } }, false);
    expect(get(history).canUndo).toBe(false);
  });

  it('tolerates derived stats changes but protects external editable data', async () => {
    const alice = makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' });
    seed('characters/Alice.json', alice);
    await writeCharacterFile('Book', 'Alice.json', { ...alice, notes: 'edited' });
    seed('characters/Alice.json', { ...read('characters/Alice.json'), updatedAt: 'external stats update', stats: { totalLines: 9, chapterCount: 2 } });
    await history.undo();
    expect(read('characters/Alice.json').notes).toBe(alice.notes);
    await history.redo();
    seed('characters/Alice.json', { ...read('characters/Alice.json'), notes: 'external edit' });
    await history.undo();
    expect(read('characters/Alice.json').notes).toBe('external edit');
    expect(get(history).error).toContain('changed outside this history');
  });

  it('does not write when a safe pre-image cannot be read', async () => {
    seed('settings.json', { important: true });
    backend.failRead = true;
    expect(await writeEditableJson('settings.json', { important: false })).toBe(false);
    expect(apiPostVoid).not.toHaveBeenCalled();
    expect(read('settings.json')).toEqual({ important: true });
    expect(get(history).canUndo).toBe(false);
    expect(get(history).error).toContain('Backend unavailable');
  });

  it('does not record failed saves', async () => {
    seed('settings.json', { value: 'before' });
    backend.failWrite = true;
    expect(await writeEditableJson('settings.json', { value: 'after' })).toBe(false);
    expect(get(history).canUndo).toBe(false);
    expect(read('settings.json')).toEqual({ value: 'before' });
  });

  it('refuses to overwrite an unreadable JSON pre-image', async () => {
    backend.files.set('settings.json', '{broken');
    expect(await writeEditableJson('settings.json', { value: 'after' })).toBe(false);
    expect(apiPostVoid).not.toHaveBeenCalled();
    expect(backend.files.get('settings.json')).toBe('{broken');
    expect(get(history).canUndo).toBe(false);
  });

  it('restores backend review flags and learned character descriptors together', async () => {
    const alice = makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' });
    seed('characters/Alice.json', alice);
    seed('Chapter/dialogue.json', { reviewed: false, lines: [] });
    const original = Object.fromEntries(backend.files);
    await captureEditableFiles('Mark chapter reviewed',
      ['Chapter/dialogue.json', 'characters/Alice.json'], async () => {
        seed('Chapter/dialogue.json', { reviewed: true, lines: [] });
        seed('characters/Alice.json', { ...alice, descriptors: ['the archer'] });
      });
    await history.undo();
    expect(Object.fromEntries(backend.files)).toEqual(original);
    expect(get(history).canUndo).toBe(false);
    await history.redo();
    expect(read('Chapter/dialogue.json').reviewed).toBe(true);
    expect(read('characters/Alice.json').descriptors).toEqual(['the archer']);
  });

  it('requires a reliable folder listing when capturing backend edits', async () => {
    backend.failRead = true;
    await expect(listCharacterFiles('Book', true)).rejects.toThrow('Backend unavailable');
  });

  it('supports browser directory handles without using backend storage', async () => {
    let contents: string | null = JSON.stringify({ text: 'before' });
    const file: FileSystemFileHandle = {
      kind: 'file', name: 'dialogue.json',
      isSameEntry: async (other) => other === file,
      queryPermission: async () => 'granted',
      requestPermission: async () => 'granted',
      getFile: async () => new File([contents ?? ''], 'dialogue.json'),
      createWritable: async () => Object.assign(new WritableStream(), {
        write: async (value: BufferSource | Blob | string | WriteParams) => {
          if (typeof value !== 'string') throw new Error('Expected a JSON string');
          contents = value;
        },
        close: async () => {},
        seek: async () => {},
        truncate: async () => {},
      }),
    };
    const directory: FileSystemDirectoryHandle = {
      kind: 'directory', name: 'Book',
      isSameEntry: async (other) => other === directory,
      queryPermission: async () => 'granted',
      requestPermission: async () => 'granted',
      getFileHandle: async () => file,
      getDirectoryHandle: async () => { throw new DOMException('Missing directory', 'NotFoundError'); },
      removeEntry: async () => { contents = null; },
      resolve: async (other) => other === file ? ['dialogue.json'] : null,
      values: async function* () { yield file; },
      keys: async function* () { yield file.name; },
      entries: async function* () { yield [file.name, file]; },
    };
    setRootDirHandle(directory);
    try {
      await writeEditableJson('dialogue.json', { text: 'after' });
      expect(JSON.parse(contents!)).toEqual({ text: 'after' });
      await history.undo();
      expect(JSON.parse(contents!)).toEqual({ text: 'before' });
      await history.redo();
      expect(JSON.parse(contents!)).toEqual({ text: 'after' });
      expect(apiPostVoid).not.toHaveBeenCalled();
    } finally {
      setRootDirHandle(null);
    }
  });
});
