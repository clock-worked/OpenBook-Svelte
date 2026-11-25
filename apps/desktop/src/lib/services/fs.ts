// Book file access: one transport over the File System Access handle and
// the sidecar, the history-recording write path, and the v3 character
// folder primitives with their read caches. Which folder is the book lives in
// workspaceRoot.ts; what the files are lives in bookFormat.ts.
import type {
  Character,
  DialogueJson,
  CharactersJson,
} from '$lib/types';
import { history } from './history';
import {
  clearRawCharacterFileDocs,
  mapCharacterFile,
  recordRawCharacterFileDoc,
} from './characterFileMapping';
import { API_ENDPOINTS, apiPostJson, apiPostVoid } from './apiClient';
import type {
  ApplyDialogueStatsRequest,
  DeleteFileRequest,
  ListFilesRequest,
  ListFilesResponse,
  ReadTextRequest,
  ReadTextResponse,
  SaveFileRequest,
} from './apiContracts';

let rootDirHandle: FileSystemDirectoryHandle | null = null;

// ADR 0012: read cache for central character data. One read per book
// session: invalidated by every character-data write and by root changes.
let centralCharactersCache: { root: string; value: CharactersJson | null } | null = null;
// The book's name→id map, derived from the central character data and
// invalidated with it (the identity caches live and die together).
let nameToIdMapCache: { root: string; value: Map<string, string> } | null = null;
let characterCacheGeneration = 0;

export function invalidateCentralCharactersCache(): void {
  centralCharactersCache = null;
  nameToIdMapCache = null;
  characterCacheGeneration += 1;
}

export function getRootDirHandle(): FileSystemDirectoryHandle | null {
  return rootDirHandle;
}

export function getRootDirInfo(): { name: string; hasHandle: boolean } {
  return {
    name: rootDirHandle?.name || 'none',
    hasHandle: !!rootDirHandle
  };
}

export function setRootDirHandle(handle: FileSystemDirectoryHandle | null): void {
  rootDirHandle = handle;
  invalidateCentralCharactersCache();
}

async function getFileHandle(dirHandle: FileSystemDirectoryHandle, path: string[], create = false): Promise<FileSystemFileHandle | null> {
  if (path.length === 0) return null;
  let currentHandle: FileSystemDirectoryHandle | FileSystemFileHandle = dirHandle;
  try {
    for (let i = 0; i < path.length; i++) {
      const part = path[i];
      if (i < path.length - 1) {
        if (currentHandle.kind !== 'directory') return null;
        currentHandle = await currentHandle.getDirectoryHandle(part, { create });
      } else {
        if (currentHandle.kind !== 'directory') return null;
        return await currentHandle.getFileHandle(part, { create });
      }
    }
  } catch (e) {
    // Missing files are expected in many read/check flows (e.g. optional manifests)
    // and should not be treated as errors.
    if (!create && e instanceof DOMException && e.name === 'NotFoundError') {
      return null;
    }
    console.error(`[getFileHandle] Error accessing path ${path.join('/')}:`, e);
    return null;
  }
  return null;
}

async function getDirectoryHandle(dirHandle: FileSystemDirectoryHandle, path: string[], create = false): Promise<FileSystemDirectoryHandle | null> {
  let currentHandle = dirHandle;
  for (const part of path) {
    currentHandle = await currentHandle.getDirectoryHandle(part, { create });
  }
  return currentHandle;
}

// === Transport seam ===
//
// Every file read/write/list/delete in this module goes through a
// BookFsTransport. There are two real adapters:
//
//   - `fsaTransport`  — the File System Access API (rootDirHandle)
//   - `httpTransport` — the local FastAPI sidecar (apiClient)
//
// While a directory handle is attached, `activeTransport()` returns a
// composite that tries the handle first and falls back to the sidecar.
// The "which storage are we talking to" decision lives here and nowhere
// else: the public primitives (and their callers) never see it.
//
// The transport never infers what a write *means* from its path. The
// caller names the write kind; the sidecar adapter maps it to an endpoint.

/**
 * What a write is, as named by its caller (never inferred from the path):
 *   - `file`         — a plain file write (`/api/save` on the sidecar)
 *   - `dialogueSave` — a chapter dialogue save, named by the chapter
 *                      persistence owner. On the sidecar it goes to the
 *                      combined `/api/save-dialogue` endpoint, which also
 *                      applies the character-stats delta (ADR 0012).
 * The File System Access adapter writes both kinds identically.
 */
export type WriteKind = 'file' | 'dialogueSave';

/**
 * Where a write landed: `handle` (File System Access — the sidecar never
 * saw it) or `sidecar`. Reported so a caller can run its own follow-up step
 * (e.g. the chapter save's stats step) without inferring the session mode.
 */
export type WriteRoute = 'handle' | 'sidecar';

/** A successful write's outcome; `unchanged` = content identical on disk, nothing written. */
export type WriteOutcome = WriteRoute | 'unchanged';

interface BookFsTransport {
  /**
   * Read a file as raw text (path relative to the book root).
   * Returns null when the file is absent or unreadable through this
   * transport (the composite then falls back to the sidecar).
   *
   * `optional` is forwarded to the sidecar only when it is a boolean:
   * `true` marks a missing file as an expected outcome, `false` keeps
   * the strict read, and `undefined` omits the field entirely (plain
   * text reads).
   */
  readText(path: string, options: { optional?: boolean }): Promise<string | null>;
  /**
   * Write a file, creating parent directories on demand. The handle
   * adapter writes the string verbatim; the sidecar adapter ships the
   * parsed JSON value, so `contents` must be JSON text. `kind` selects
   * the sidecar endpoint (see `WriteKind`).
   * Returns where the write landed, or null when it did not complete
   * through this transport (the composite then falls back to the sidecar).
   */
  writeText(path: string, contents: string, kind: WriteKind): Promise<WriteRoute | null>;
  /**
   * List the entry names of a directory. Returns null when the
   * directory is unreachable through this transport (the composite
   * then falls back to the sidecar); a directory that exists but is
   * empty yields `[]`. The handle adapter treats a missing directory
   * as a normal pre-migration state (`[]`, no fallback).
   *
   * With `jsonOnly`, the handle adapter returns only non-hidden
   * `*.json` files (the sidecar already returns a file listing).
   */
  listEntries(dirPath: string, options: { jsonOnly?: boolean }): Promise<string[] | null>;
  /**
   * Delete a file (path relative to the book root). Returns true on
   * success, including when the target was already absent (idempotent
   * delete).
   */
  deleteEntry(path: string): Promise<boolean>;
}

function splitRelPath(path: string): string[] {
  return path.split('/').filter((p) => p);
}

const fsaTransport: BookFsTransport = {
  async readText(path) {
    if (!rootDirHandle) return null;
    try {
      const fileHandle = await getFileHandle(rootDirHandle, splitRelPath(path));
      if (!fileHandle) return null;
      const file = await fileHandle.getFile();
      return await file.text();
    } catch (error) {
      // Missing files are expected in many read flows; the composite
      // falls through to the sidecar below.
      console.warn(`[fs] File System API read failed for ${path}; trying backend`, error);
      return null;
    }
  },
  async writeText(path, contents) {
    if (!rootDirHandle) return null;
    try {
      const fileHandle = await getFileHandle(rootDirHandle, splitRelPath(path), true);
      if (!fileHandle) return null;
      const writable = await fileHandle.createWritable();
      await writable.write(contents);
      await writable.close();
      return 'handle';
    } catch (error) {
      console.warn(`[fs] File System API write failed for ${path}; trying backend`, error);
      return null;
    }
  },
  async listEntries(dirPath, { jsonOnly }) {
    if (!rootDirHandle) return null;
    try {
      const dirHandle = await rootDirHandle.getDirectoryHandle(dirPath, { create: false });
      const entries: string[] = [];
      for await (const entry of dirHandle.values()) {
        if (entry.kind !== 'file' || entry.name.startsWith('.')) continue;
        if (jsonOnly && !entry.name.toLowerCase().endsWith('.json')) continue;
        entries.push(entry.name);
      }
      return entries;
    } catch (error) {
      // A missing folder is a normal pre-migration state.
      if (error instanceof DOMException && error.name === 'NotFoundError') {
        return [];
      }
      console.warn(`[fs] File System API listing failed for ${dirPath}; trying backend`, error);
      return null;
    }
  },
  async deleteEntry(path) {
    if (!rootDirHandle) return false;
    const pathParts = splitRelPath(path);
    if (pathParts.length === 0) return false;
    const fileName = pathParts.pop() as string;
    try {
      const parentDirHandle = await getDirectoryHandle(rootDirHandle, pathParts, false);
      await parentDirHandle.removeEntry(fileName);
      return true;
    } catch (error) {
      // A missing file (or parent directory) is a normal delete outcome.
      if (error instanceof DOMException && error.name === 'NotFoundError') {
        return true;
      }
      console.warn(`[fs] File System API delete failed for ${path}; trying backend`, error);
      return false;
    }
  },
};

const httpTransport: BookFsTransport = {
  async readText(path, { optional }) {
    const request: ReadTextRequest = { file_path: path };
    if (optional !== undefined) request.optional = optional;
    try {
      const response = await apiPostJson<ReadTextResponse, ReadTextRequest>(API_ENDPOINTS.readText, request);
      return response.content ?? null;
    } catch (error) {
      // A failure on an *optional* read is an expected empty result (e.g. a
      // v3 chapter with no roster file), not an error worth a red line.
      if (!optional) console.error(`Failed to read text from ${path}:`, error);
      return null;
    }
  },
  async writeText(path, contents, kind) {
    try {
      // ADR 0012: a dialogue save goes to the combined endpoint, which also
      // applies the incremental character-stats delta — one round trip.
      const endpoint = kind === 'dialogueSave' ? API_ENDPOINTS.saveDialogue : API_ENDPOINTS.save;
      await apiPostVoid<SaveFileRequest>(endpoint, {
        file_path: path,
        content: JSON.parse(contents) as unknown,
      });
      return 'sidecar';
    } catch (error) {
      console.error(`Failed to write to ${path}`, error);
      return null;
    }
  },
  async listEntries(dirPath) {
    try {
      const response = await apiPostJson<ListFilesResponse, ListFilesRequest>(API_ENDPOINTS.listFiles, {
        file_path: dirPath,
      });
      return Array.isArray(response.files) ? response.files : [];
    } catch (error) {
      console.error('Failed to list files from backend', error);
      return [];
    }
  },
  async deleteEntry(path) {
    try {
      await apiPostVoid<DeleteFileRequest>(API_ENDPOINTS.deleteFile, {
        file_path: path,
      });
      return true;
    } catch (error) {
      console.error(`Failed to delete ${path}`, error);
      return false;
    }
  },
};

/**
 * Handle-first composite: try the File System Access API, then the
 * sidecar, whenever the handle cannot reach the target (missing file,
 * transient FSA failure, ...). The per-primitive try-FSA-then-HTTP
 * dance that used to be copy-pasted into every primitive lives here,
 * once.
 */
function handleOverSidecar(): BookFsTransport {
  return {
    readText: (path, options) =>
      fsaTransport.readText(path, options).then(
        (text) => text ?? httpTransport.readText(path, options),
      ),
    writeText: (path, contents, kind) =>
      fsaTransport.writeText(path, contents, kind).then(
        (route) => route ?? httpTransport.writeText(path, contents, kind),
      ),
    listEntries: (dirPath, options) =>
      fsaTransport.listEntries(dirPath, options).then(
        (entries) => entries ?? httpTransport.listEntries(dirPath, options),
      ),
    deleteEntry: (path) =>
      fsaTransport.deleteEntry(path).then(
        (ok) => ok || httpTransport.deleteEntry(path),
      ),
  };
}

function activeTransport(): BookFsTransport {
  return rootDirHandle ? handleOverSidecar() : httpTransport;
}

export async function readFileAsJson<T>(path: string, logError = true): Promise<T | null> {
  const text = await activeTransport().readText(path, { optional: !logError });
  if (text == null) return null;
  try {
    return JSON.parse(text) as T;
  } catch (error) {
    if (logError) console.error(`Failed to read or parse JSON from ${path}`, error);
    return null;
  }
}

export async function readOptionalJsonRelative<T>(path: string): Promise<T | null> {
  return readFileAsJson<T>(path, false);
}

async function writeFileDirect(path: string, contents: string): Promise<boolean> {
  return (await activeTransport().writeText(path, contents, 'file')) !== null;
}

async function readHistoryFile(path: string): Promise<string | null> {
  const validate = (contents: unknown): string | null => {
    if (contents === null) return null;
    if (typeof contents !== 'string') throw new Error(`Invalid file snapshot response for ${path}`);
    JSON.parse(contents);
    return contents;
  };
  if (rootDirHandle) {
    try {
      const parts = path.split('/').filter(Boolean);
      const name = parts.pop();
      if (!name) throw new Error('Missing file name');
      const directory = await getDirectoryHandle(rootDirHandle, parts);
      if (!directory) throw new Error(`Cannot access ${path}`);
      return validate(await (await (await directory.getFileHandle(name)).getFile()).text());
    } catch (error) {
      if (error instanceof DOMException && error.name === 'NotFoundError') return null;
      throw error;
    }
  }
  const response = await apiPostJson<ReadTextResponse, ReadTextRequest>(API_ENDPOINTS.readText, {
    file_path: path, optional: true,
  });
  return validate(response.content);
}

function sameEditableJson(left: string | null, right: string | null): boolean {
  if (left === null || right === null) return left === right;
  const canonical = (text: string) => JSON.stringify(JSON.parse(text), (key, value) => {
    // These fields are rebuilt by chapter/manifest scans, not edited by the user.
    if (['stats', 'manifestStats', 'updatedAt', 'firstAppearance'].includes(key)) return undefined;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));
    }
    return value;
  });
  return canonical(left) === canonical(right);
}

export async function writeEditableJson(path: string, content: unknown, recordHistory = true): Promise<boolean> {
  return writeFile(path, JSON.stringify(content, null, 2), recordHistory);
}

export async function readEditableJson<T>(path: string): Promise<T | null> {
  const contents = await readHistoryFile(path);
  return contents === null ? null : JSON.parse(contents) as T;
}

export async function captureEditableFiles<T>(
  label: string,
  paths: string[],
  operation: () => Promise<T>,
): Promise<T> {
  const capture = (index: number): Promise<T> => {
    const path = paths[index];
    if (path === undefined) return operation();
    return history.capture(label, path, () => readHistoryFile(path), async (value) => {
      const success = value === null
        ? await deleteFileDirect('', path)
        : await writeFileDirect(path, value);
      if (!success) throw new Error(`Could not restore ${path}`);
    }, () => capture(index + 1), sameEditableJson);
  };
  return capture(0);
}

async function writeFile(
  path: string,
  contents: string,
  recordHistory = true,
  kind: WriteKind = 'file',
): Promise<boolean> {
  return (await writeFileRouted(path, contents, recordHistory, kind)) !== null;
}

/** writeFile, reporting where the write landed (null = failed). */
async function writeFileRouted(
  path: string,
  contents: string,
  recordHistory: boolean,
  kind: WriteKind,
): Promise<WriteOutcome | null> {
  if (!recordHistory || path.startsWith('_backups/')) return activeTransport().writeText(path, contents, kind);
  // history.write skips a byte-identical write; the apply below reports the
  // route when it does write.
  let outcome: WriteOutcome = 'unchanged';
  try {
    await history.write(
      path === 'settings.json' ? 'Change book settings'
        : path === 'voices.json' ? 'Change voices'
        : path.startsWith('characters/') ? 'Edit character'
        : 'Edit chapter',
      path,
      contents,
      () => readHistoryFile(path),
      async (value) => {
        // Undo/redo of a write re-uses the kind its caller named.
        if (value === null) {
          if (!await deleteFileDirect('', path)) throw new Error(`Could not restore or save ${path}`);
          return;
        }
        const route = await activeTransport().writeText(path, value, kind);
        if (!route) throw new Error(`Could not restore or save ${path}`);
        outcome = route;
      },
      sameEditableJson,
    );
    return outcome;
  } catch (error) {
    history.reportError(error);
    return null;
  }
}

// === Chapter dialogue save ===

/**
 * The chapter dialogue save write (kind `dialogueSave`), for the chapter
 * persistence owner. Records history. On the sidecar it goes to
 * `/api/save-dialogue`, which applies the stats delta itself; the outcome
 * says where the write landed (null = failed) so the owner can run its
 * explicit stats step.
 */
export async function writeDialogueSave(path: string, data: DialogueJson): Promise<WriteOutcome | null> {
  return writeFileRouted(path, JSON.stringify(data, null, 2), true, 'dialogueSave');
}

/**
 * ADR 0012: ask the backend to apply the incremental character-stats delta
 * for one saved dialogue file. Fire-and-forget by contract: callers must not
 * await it, and a failure only warns — stats are eventually consistent.
 */
export async function applyDialogueStatsDelta(relPath: string): Promise<boolean> {
  try {
    await apiPostVoid<ApplyDialogueStatsRequest>(API_ENDPOINTS.applyDialogueStats, { file_path: relPath });
    return true;
  } catch (error) {
    console.warn(`[fs] Failed to apply dialogue stats delta for ${relPath}:`, error);
    return false;
  }
}

export async function readCentralCharacters(root: string): Promise<CharactersJson | null> {
  if (centralCharactersCache && centralCharactersCache.root === root) {
    return centralCharactersCache.value;
  }
  const folder = await readCharacterFolder(root);
  const value = folder ?? await readFileAsJson<CharactersJson>('characters.json', false);
  centralCharactersCache = { root, value };
  return value;
}

/**
 * The book's lowercase name/alias → character id map, built from
 * `readCentralCharacters` and cached with it: every character-data write and
 * every root change invalidates both. A failed read yields an empty map and
 * is not cached.
 */
export async function ensureCharacterNameToIdMapCached(root: string | null): Promise<Map<string, string>> {
  if (!root) return new Map();
  if (nameToIdMapCache && nameToIdMapCache.root === root) return nameToIdMapCache.value;
  const generation = characterCacheGeneration;
  try {
    const central = await readCentralCharacters(root);
    // Dynamic import: the characters store imports this module.
    const { buildNameToIdMap } = await import('$lib/stores/characters');
    const value = central?.characters ? buildNameToIdMap(central.characters) : new Map<string, string>();
    // An invalidation while this read was in flight wins: don't cache.
    if (generation === characterCacheGeneration) nameToIdMapCache = { root, value };
    return value;
  } catch (error) {
    console.warn('[fs] Failed to load characters for the name-to-ID map:', error);
    return new Map();
  }
}

export async function writeCentralCharacters(root: string, data: CharactersJson, recordHistory = true): Promise<boolean> {
  // When using FileSystemDirectoryHandle, rootDirHandle is already at the book directory level
  const success = await writeFile('characters.json', JSON.stringify(data, null, 2), recordHistory);
  if (success) invalidateCentralCharactersCache();
  return success;
}

// === v3.0 Character Folder Primitives ===

/**
 * List the names of `*.json` files in the book's `characters/` folder.
 * Returns [] when the folder does not exist (book not migrated to v3 yet).
 */
export async function listCharacterFiles(root: string, strict = false): Promise<string[]> {
  if (strict) {
    // History-capture path: needs a reliable listing. The transport swallows
    // sidecar failures (a missing folder is a normal state), so strict goes
    // straight to the sidecar and lets errors propagate.
    const response = await apiPostJson<ListFilesResponse, ListFilesRequest>(API_ENDPOINTS.listFiles, {
      file_path: 'characters',
    });
    if (!Array.isArray(response.files) || !response.files.every((file) => typeof file === 'string')) {
      throw new Error('Invalid character folder listing; cannot safely record this edit.');
    }
    return response.files;
  }

  // A missing characters/ folder is a normal pre-migration state: the
  // transport reports it as [] (no fallback needed).
  const files = await activeTransport().listEntries('characters', { jsonOnly: true });
  return files ?? [];
}

/**
 * Read a single character file from the book's `characters/` folder.
 * Returns null when the file does not exist.
 *
 * `silent` suppresses the error logging for the missing-file case: callers in
 * the mutation/store path treat "no file yet" as a normal outcome (e.g. a
 * pre-migration book, or a character that was never file-backed), and the
 * noisy console.error would fire on every such operation.
 */
export async function readCharacterFile(
  root: string,
  fileName: string,
  silent = false,
): Promise<any | null> {
  return readFileAsJson<any>(`characters/${fileName}`, !silent);
}

/**
 * Write a single character file to the book's `characters/` folder.
 * The `characters/` directory is created on demand by writeFile.
 */
export async function writeCharacterFile(root: string, fileName: string, content: unknown, recordHistory = true): Promise<boolean> {
  const success = await writeFile(`characters/${fileName}`, JSON.stringify(content, null, 2), recordHistory);
  if (success) invalidateCentralCharactersCache();
  return success;
}

// === v3.0 Character Folder Read Path ===

/**
 * Read the v3 `characters/` folder: list the files, map each through
 * `mapCharacterFile` (characterFileMapping.ts), and refresh the raw-document
 * cache. Returns null when the folder is absent/empty or yields no valid
 * records.
 */
export async function readCharacterFolder(root: string): Promise<CharactersJson | null> {
  const fileNames = await listCharacterFiles(root);
  if (fileNames.length === 0) return null;

  clearRawCharacterFileDocs();
  const characters: Character[] = [];
  for (const fileName of fileNames) {
    const raw = await readCharacterFile(root, fileName);
    if (!raw) continue;
    const character = mapCharacterFile(raw);
    if (!character) continue;
    recordRawCharacterFileDoc(character.guid, raw as Record<string, unknown>);
    characters.push(character);
  }

  if (characters.length === 0) return null;
  characters.sort((left, right) => left.name.localeCompare(right.name));
  return { formatVersion: '3.0', characters };
}

/**
 * Delete a file at a path relative to the book root.
 * Deleting a file that does not exist is a success (idempotent).
 */
async function deleteFileDirect(root: string, relPath: string): Promise<boolean> {
  return activeTransport().deleteEntry(relPath);
}

export async function deleteFile(root: string, relPath: string, recordHistory = true): Promise<boolean> {
  if (!recordHistory) return deleteFileDirect(root, relPath);
  try {
    await history.write('Delete character', relPath, null, () => readHistoryFile(relPath), async (value) => {
      const success = value === null
        ? await deleteFileDirect(root, relPath)
        : await writeFileDirect(relPath, value);
      if (!success) throw new Error(`Could not restore or delete ${relPath}`);
    }, sameEditableJson);
    invalidateCentralCharactersCache();
    return true;
  } catch (error) {
    history.reportError(error);
    return false;
  }
}

export async function readTextFile(path: string): Promise<string | null> {
  // Plain text read: no `optional` flag is forwarded to the sidecar.
  return activeTransport().readText(path, {});
}
