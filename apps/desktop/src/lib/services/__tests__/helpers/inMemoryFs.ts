// ============================================================================
// In-memory stand-in for $lib/services/fs (backend-path case) for the v3
// character service-layer matrix (docs/character_details_test_plan.md).
//
// Mirrors the dual-path primitives' backend behavior:
//   - read missing  -> null
//   - write         -> auto-creates parent dirs (implicit in the map)
//   - delete missing-> true (idempotent)
//   - list missing dir -> []
//
// Paths are book-root-relative ('characters.json', 'Chapter1/dialogue.json',
// 'characters/Alice.json'). JSON files are stored as their serialized text so
// byte-identity assertions (diffTree) are meaningful.
//
// Install in a test file with:
//   vi.mock('$lib/services/fs', async () => (await import('./helpers/inMemoryFs')).inMemoryFs);
// and drive it via the exported test controls (__seed / __snapshot / ...).
// ============================================================================

import type { Character } from '$lib/types';
import {
  clearRawCharacterFileDocs,
  mapCharacterFile,
  recordRawCharacterFileDoc,
} from '$lib/services/characterFileMapping';

const files = new Map<string, string>();

function normalizePath(path: string): string {
  return String(path ?? '')
    .replace(/\\/g, '/')
    .replace(/\/{2,}/g, '/')
    .replace(/^\/+/, '');
}

function readJson(path: string): any | null {
  const text = files.get(normalizePath(path));
  if (text == null) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function writeJson(path: string, data: unknown): boolean {
  files.set(normalizePath(path), JSON.stringify(data, null, 2));
  return true;
}

export const inMemoryFs = {
  // === Path helpers (pure; mirror fs.ts) ===
  getBookCharactersPath: (_root: string) => 'characters.json',
  getCentralCharactersPath: (_root: string) => 'characters.json',
  getCharactersPath: (_root: string, title: string) => `${title}/${title}.characters.json`,
  getDialoguePath: (_root: string, title: string) => `${title}/dialogue.json`,
  getScriptPath: (_root: string, title: string) => `${title}/${title}.script.json`,
  getVoicesPath: (_root: string) => 'voices.json',
  getRootDirInfo: () => ({ name: 'none', hasHandle: false }),

  // === v3.0 character folder primitives ===
  listCharacterFiles: async (_root: string): Promise<string[]> => {
    const names: string[] = [];
    for (const key of files.keys()) {
      if (!key.startsWith('characters/')) continue;
      const base = key.slice('characters/'.length);
      if (base.includes('/') || base.startsWith('.') || !base.toLowerCase().endsWith('.json')) continue;
      names.push(base);
    }
    return names.sort();
  },
  readCharacterFile: async (_root: string, fileName: string) => readJson(`characters/${fileName}`),
  writeCharacterFile: async (_root: string, fileName: string, content: unknown) =>
    writeJson(`characters/${fileName}`, content),
  // Folder read with the REAL mapping + raw-document cache (characterFileMapping
  // is not fs-mocked), driving the in-memory list/read primitives — mirrors the
  // fs.ts readCharacterFolder composition.
  readCharacterFolder: async (root: string) => {
    const fileNames = await inMemoryFs.listCharacterFiles(root);
    if (fileNames.length === 0) return null;
    clearRawCharacterFileDocs();
    const characters: Character[] = [];
    for (const fileName of fileNames) {
      const raw = await readJson(`characters/${fileName}`);
      if (!raw) continue;
      const character = mapCharacterFile(raw);
      if (!character) continue;
      recordRawCharacterFileDoc(character.guid, raw as Record<string, unknown>);
      characters.push(character);
    }
    if (characters.length === 0) return null;
    characters.sort((left, right) => left.name.localeCompare(right.name));
    return { formatVersion: '3.0', characters };
  },
  // Deleting a missing file is a success (idempotent).
  deleteFile: async (_root: string, relPath: string) => {
    files.delete(normalizePath(relPath));
    return true;
  },

  // === Legacy JSON read/write (the names the services import) ===
  readCharacters: async (path: string) => readJson(path),
  readOptionalCharacters: async (path: string) => readJson(path),
  readCentralCharacters: async (_root: string) => readJson('characters.json'),
  readDialogue: async (path: string) => readJson(path),
  readOptionalDialogue: async (path: string) => readJson(path),
  readScript: async (path: string) => readJson(path),
  readOptionalScript: async (path: string) => readJson(path),
  readVoices: async (_root: string) => readJson('voices.json'),
  writeCharacters: async (path: string, data: unknown) => writeJson(path, data),
  writeCentralCharacters: async (_root: string, data: unknown) => writeJson('characters.json', data),
  writeDialogue: async (path: string, data: unknown) => writeJson(path, data),
  writeScript: async (path: string, data: unknown) => writeJson(path, data),
  writeVoices: async (_root: string, data: unknown) => writeJson('voices.json', data),

  // === Text read (used by the migration's readInventoried / rescan) ===
  readTextFile: async (path: string) => files.get(normalizePath(path)) ?? null,

  // === Test controls ===
  __reset: () => {
    files.clear();
  },
  __seed: (entries: Record<string, unknown>) => {
    for (const [path, value] of Object.entries(entries)) {
      files.set(normalizePath(path), typeof value === 'string' ? value : JSON.stringify(value, null, 2));
    }
  },
  __snapshot: (): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const [key, value] of files) out[key] = value;
    return out;
  },
  __keys: (): string[] => [...files.keys()].sort(),
  __has: (path: string) => files.has(normalizePath(path)),
  __readText: (path: string) => files.get(normalizePath(path)) ?? null,
  __readJson: (path: string) => readJson(path),
};

export type InMemoryFs = typeof inMemoryFs;
