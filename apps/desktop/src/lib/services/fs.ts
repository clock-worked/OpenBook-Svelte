// Browser File System Access API implementation
import type { ChapterStatus } from '$lib/stores/bookState';
import { get } from 'svelte/store';
import { bookRootPathOverride } from '$lib/stores/settings';
import type {
  Character,
  DialogueJson,
  CharactersJson,
  VoicesJson,
  // Legacy v1.0 types
  ScriptJson,
  LineItem
} from '$lib/types';
import { storeProjectHandle } from './persistence';
import { history } from './history';
import {
  clearRawCharacterFileDocs,
  mapCharacterFile,
  recordRawCharacterFileDoc,
} from './characterFileMapping';
import { API_ENDPOINTS, apiPostJson, apiPostVoid, apiRequestJson, toApiClientError } from './apiClient';
import type {
  DeleteFileRequest,
  ListChaptersResponse,
  ListFilesRequest,
  ListFilesResponse,
  PickBookDirectoryResponse,
  ReadTextRequest,
  ReadTextResponse,
  SaveFileRequest,
  SetAudioRootRequest,
  SetBookRootRequest,
  UpdateCharacterStatsResponse,
} from './apiContracts';

let rootDirHandle: FileSystemDirectoryHandle | null = null;

export function requiresBackendDirectoryPicker(): boolean {
  return /\bCode\/\d/i.test(navigator.userAgent);
}

export async function selectBookDirectoryPath(): Promise<string | null> {
  const result = await apiPostJson<PickBookDirectoryResponse>(API_ENDPOINTS.pickBookDirectory);
  return result.path || null;
}

/**
 * Timeout wrapper for promises to prevent indefinite hanging
 */
function withTimeout<T>(promise: Promise<T>, timeoutMs: number, errorMsg: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(errorMsg)), timeoutMs)
    )
  ]);
}

export async function selectBookDirectory(): Promise<FileSystemDirectoryHandle | null> {
  try {
    // Check if the API is available
    if (!window.showDirectoryPicker) {
      throw new Error('File System Access API is not supported in this browser');
    }

    // Let Chromium choose its last-used location. Some browser policies reject
    // an unavailable well-known `startIn` directory with AbortError.
    const pickerPromise = window.showDirectoryPicker({
      id: 'openbook-book-directory',
      mode: 'readwrite'
    });

    rootDirHandle = await withTimeout(
      pickerPromise,
      30000,
      'File picker timed out. Please try again.'
    );

    // Persistence is optional for opening the project. Browsers can reject
    // serializing a handle even though the selected handle remains usable.
    if (rootDirHandle) {
      try {
        await storeProjectHandle(rootDirHandle);
      } catch (error) {
        console.warn('Selected directory will not be remembered after reload:', error);
      }
    }

    return rootDirHandle;
  } catch (err) {
    // User cancelled the picker - this is normal, not an error
    if (err instanceof Error && err.name === 'AbortError') {
      console.info('Directory picker closed or rejected by the browser', {
        message: err.message,
      });
      return null;
    }

    // Security error - user denied permission
    if (err instanceof Error && err.name === 'SecurityError') {
      console.error('Security error: Permission denied to access file system');
      throw new Error('Permission denied. Please allow file system access.');
    }

    // Actual error - log it with details
    console.error('Error selecting directory:', err);
    throw err; // Re-throw so caller can handle it
  }
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
}

/**
 * Attempts to get the absolute path from a FileSystemDirectoryHandle.
 * This is a workaround since the API doesn't expose paths directly.
 * In some browsers, we can access it through internal properties.
 */
export async function getAbsolutePathFromHandle(handle: FileSystemDirectoryHandle): Promise<string | null> {
  try {
    const handleAny = handle as any;

    // Method 1: Try to access the path property directly (works in some Chromium browsers)
    if (handleAny.path) {
      return handleAny.path;
    }

    // Method 2: Try to access through the handle's internal structure
    // Some browsers expose it through __proto__ or other internal properties
    if (handleAny.__proto__?.path) {
      return handleAny.__proto__.path;
    }

    // Method 3: Try to get it through the handle's query method (if available)
    // This is not standard but may exist in some implementations
    if (typeof handleAny.query === 'function') {
      try {
        const result = await handleAny.query({ path: true });
        if (result?.path) {
          return result.path;
        }
      } catch (e) {
        // Method not available, continue
      }
    }

    // Method 4: Try to use the handle to get a file and extract path from it
    // This is experimental and may not work
    try {
      const entries = handle.entries();
      const firstEntry = await entries.next();
      if (!firstEntry.done) {
        const [name, entryHandle] = firstEntry.value;
        if (entryHandle.kind === 'file') {
          const fileAny = entryHandle as any;
          if (fileAny.path) {
            // Extract directory path from file path
            const filePath = fileAny.path;
            const dirPath = filePath.substring(0, filePath.lastIndexOf(name));
            if (dirPath) {
              return dirPath.replace(/\/$/, '').replace(/\\$/, ''); // Remove trailing slash
            }
          }
        }
      }
    } catch (e) {
      // Method not available, continue
    }

    // If we can't get it directly, return null
    // The caller should handle this case with a fallback
    return null;
  } catch (e) {
    console.warn('Could not extract absolute path from handle:', e);
    return null;
  }
}

export async function resolveBookRootPath(handle: FileSystemDirectoryHandle | null): Promise<string | null> {
  if (handle) {
    const absolutePath = await getAbsolutePathFromHandle(handle);
    if (absolutePath) return absolutePath;
  }
  return get(bookRootPathOverride) || null;
}

export function isLikelyAbsolutePath(value: string): boolean {
  return /^[a-zA-Z]:\\/.test(value) || value.startsWith('\\\\') || value.startsWith('/');
}

export async function setBackendBookRoot(rootPath: string): Promise<boolean> {
  if (!rootPath || !isLikelyAbsolutePath(rootPath)) return false;

  try {
    const payload: SetBookRootRequest = { root_path: rootPath };
    await apiPostVoid<SetBookRootRequest>(API_ENDPOINTS.setBookRoot, payload);
    return true;
  } catch (error) {
    const apiError = toApiClientError(error);
    console.warn(`[fs] Error setting backend book root (${apiError.type}):`, apiError.message);
    return false;
  }
}

export async function setBackendAudioRoot(audioRoot: string): Promise<boolean> {
  if (!audioRoot || !isLikelyAbsolutePath(audioRoot)) return false;

  try {
    const payload: SetAudioRootRequest = { audio_root: audioRoot };
    await apiPostVoid<SetAudioRootRequest>(API_ENDPOINTS.setAudioRoot, payload);
    return true;
  } catch (error) {
    const apiError = toApiClientError(error);
    console.warn(`[fs] Error setting backend audio root (${apiError.type}):`, apiError.message);
    return false;
  }
}

export async function syncBackendRoots(opts: {
  handle?: FileSystemDirectoryHandle | null;
  bookRootPath?: string | null;
  audioRootPath?: string | null;
}): Promise<{ resolvedBookRoot: string | null; bookRootSynced: boolean; audioRootSynced: boolean }> {
  const resolvedBookRoot = opts.bookRootPath ?? await resolveBookRootPath(opts.handle ?? null);
  const bookRootSynced = resolvedBookRoot ? await setBackendBookRoot(resolvedBookRoot) : false;

  const hasExplicitAudioRoot = typeof opts.audioRootPath === 'string' && opts.audioRootPath.length > 0;
  const candidateAudioRoot = hasExplicitAudioRoot ? opts.audioRootPath : resolvedBookRoot;
  const audioRootSynced = candidateAudioRoot ? await setBackendAudioRoot(candidateAudioRoot) : false;

  return {
    resolvedBookRoot,
    bookRootSynced,
    audioRootSynced,
  };
}

export async function syncBookRootFromHandle(opts: {
  handle: FileSystemDirectoryHandle;
  audioRootPath?: string | null;
}): Promise<{ resolvedBookRoot: string | null; bookRootSynced: boolean; audioRootSynced: boolean }> {
  const resolvedBookRoot = await resolveBookRootPath(opts.handle);
  if (!resolvedBookRoot) {
    return {
      resolvedBookRoot: null,
      bookRootSynced: false,
      audioRootSynced: false,
    };
  }

  return syncBackendRoots({
    bookRootPath: resolvedBookRoot,
    audioRootPath: opts.audioRootPath ?? null,
  });
}

export async function listChaptersFromBackend(): Promise<ChapterStatus[]> {
  try {
    const payload = await apiRequestJson<ListChaptersResponse>(API_ENDPOINTS.listChapters);
    const backendChapters = Array.isArray(payload?.chapters) ? payload.chapters : [];
    return backendChapters.map((ch) => {
      const title = String(ch?.name || '').replace(/\.txt$/i, '');
      return {
        path: String(ch?.path || ''),
        title,
        parsed: !!ch?.parsed,
        complete: false,
        audio: !!ch?.audio,
        reviewed: !!ch?.reviewed,
        scriptPath: ch?.scriptPath,
      } as ChapterStatus;
    });
  } catch (error) {
    const apiError = toApiClientError(error);
    console.warn(`[fs] Error listing chapters from backend (${apiError.type}):`, apiError.message);
    return [];
  }
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

async function chapterHasAudio(chapterDirHandle: FileSystemDirectoryHandle): Promise<boolean> {
  const audioDirHandle = await chapterDirHandle.getDirectoryHandle('audio_lines', { create: false }).catch(() => null);
  if (!audioDirHandle) return false;

  const audioExtensions = ['.wav', '.mp3', '.m4a', '.flac', '.ogg'];

  for await (const characterEntry of audioDirHandle.values()) {
    if (characterEntry.kind !== 'directory' || characterEntry.name.startsWith('.')) continue;
    const characterDirHandle = characterEntry as FileSystemDirectoryHandle;

    const manifestHandle = await characterDirHandle.getFileHandle('manifest.json', { create: false }).catch(() => null);
    if (manifestHandle) {
      try {
        const manifestFile = await manifestHandle.getFile();
        const manifestText = await manifestFile.text();
        const manifest = JSON.parse(manifestText) as {
          clips?: unknown[];
          metadata?: { totalClips?: number };
        };

        if (Array.isArray(manifest.clips) && manifest.clips.length > 0) return true;
        if (typeof manifest.metadata?.totalClips === 'number' && manifest.metadata.totalClips > 0) return true;
      } catch {
        return true;
      }
    }

    for await (const clipEntry of characterDirHandle.values()) {
      if (clipEntry.kind !== 'file') continue;
      const lowerName = clipEntry.name.toLowerCase();
      if (audioExtensions.some((ext) => lowerName.endsWith(ext))) {
        return true;
      }
    }
  }

  return false;
}

async function readJsonFromFileHandle<T>(fileHandle: FileSystemFileHandle | null): Promise<T | null> {
  if (!fileHandle) return null;

  try {
    const file = await fileHandle.getFile();
    return JSON.parse(await file.text()) as T;
  } catch {
    return null;
  }
}

async function chapterHasCompleteAudio(chapterDirHandle: FileSystemDirectoryHandle): Promise<boolean> {
  const dialogueHandle = await chapterDirHandle.getFileHandle('dialogue.json', { create: false }).catch(() => null);
  const dialogue = await readJsonFromFileHandle<DialogueJson>(dialogueHandle);
  const lines = Array.isArray(dialogue?.lines) ? dialogue.lines : [];
  if (lines.length === 0) return false;

  const audioDirHandle = await chapterDirHandle.getDirectoryHandle('audio_lines', { create: false }).catch(() => null);
  if (!audioDirHandle) return false;

  const expectedLineIdsByCharacter = new Map<string, Set<number>>();
  for (const line of lines) {
    const characterId = typeof line?.characterId === 'string' && line.characterId.trim()
      ? line.characterId.trim()
      : 'narrator';
    const lineId = typeof line?.id === 'number' ? line.id : Number(line?.id);
    if (!Number.isFinite(lineId)) continue;

    let lineIds = expectedLineIdsByCharacter.get(characterId);
    if (!lineIds) {
      lineIds = new Set<number>();
      expectedLineIdsByCharacter.set(characterId, lineIds);
    }
    lineIds.add(lineId);
  }

  if (expectedLineIdsByCharacter.size === 0) return false;

  for await (const characterEntry of audioDirHandle.values()) {
    if (characterEntry.kind !== 'directory' || characterEntry.name.startsWith('.')) continue;

    const characterDirHandle = characterEntry as FileSystemDirectoryHandle;
    const manifestHandle = await characterDirHandle.getFileHandle('manifest.json', { create: false }).catch(() => null);
    const manifest = await readJsonFromFileHandle<{
      characterId?: unknown;
      characterName?: unknown;
      clips?: Array<{ id?: unknown; chapter?: unknown }>;
    }>(manifestHandle);

    if (!manifest || !Array.isArray(manifest.clips)) continue;

    const manifestKeys = [manifest.characterId, manifest.characterName, characterEntry.name]
      .map((value) => String(value ?? '').trim())
      .filter(Boolean);

    let expectedLineIds: Set<number> | null = null;
    for (const key of manifestKeys) {
      expectedLineIds = expectedLineIdsByCharacter.get(key) || null;
      if (expectedLineIds) break;
    }
    if (!expectedLineIds || expectedLineIds.size === 0) continue;

    const presentLineIds = new Set<number>();
    for (const clip of manifest.clips) {
      const clipChapter = String(clip?.chapter ?? '').trim();
      if (clipChapter && clipChapter !== chapterDirHandle.name) continue;

      const clipId = typeof clip?.id === 'number' ? clip.id : Number(clip?.id);
      if (Number.isFinite(clipId)) {
        presentLineIds.add(clipId);
      }
    }

    if (presentLineIds.size === 0) continue;

    for (const lineId of expectedLineIds) {
      if (presentLineIds.has(lineId)) {
        expectedLineIds.delete(lineId);
      }
    }

    if (expectedLineIds.size === 0) {
      for (const key of manifestKeys) {
        if (expectedLineIdsByCharacter.get(key) === expectedLineIds) {
          expectedLineIdsByCharacter.delete(key);
        }
      }
    }
  }

  for (const remainingLineIds of expectedLineIdsByCharacter.values()) {
    if (remainingLineIds.size > 0) return false;
  }

  return true;
}

export async function scanChapters(root: FileSystemDirectoryHandle): Promise<ChapterStatus[]> {
  const chapters: ChapterStatus[] = [];
  for await (const entry of root.values()) {
    // Look for directories that contain chapter.txt
    if (entry.kind === 'directory' && !entry.name.startsWith('.')) {
      const chapterDirHandle = entry as FileSystemDirectoryHandle;
      const title = entry.name;

      // Check if directory contains chapter.txt file
      let chapterTxtHandle: FileSystemFileHandle | null = null;
      try {
        chapterTxtHandle = await chapterDirHandle.getFileHandle('chapter.txt', { create: false });
      } catch {
        // No chapter.txt found, skip this directory
        continue;
      }

      let parsed = false;
      let scriptPath: string | undefined = undefined;
      let complete = false;
      let reviewed = false;

      // Try v2.0 dialogue.json first
      let dialogueFileHandle = await chapterDirHandle.getFileHandle('dialogue.json', { create: false }).catch(() => null);
      if (dialogueFileHandle) {
        parsed = true;
        scriptPath = `${title}/dialogue.json`;
        const dialogue = await readJsonFromFileHandle<DialogueJson>(dialogueFileHandle);
        reviewed = !!dialogue?.reviewed;
        complete = await chapterHasCompleteAudio(chapterDirHandle);
      }

      const audio = await chapterHasAudio(chapterDirHandle);

      chapters.push({
        path: `${title}/chapter.txt`,
        title: title,
        parsed: parsed,
        complete,
        audio,
        reviewed,
        scriptPath: scriptPath,
      });
    }
  }

  // Sort chapters naturally by title (handles numeric prefixes correctly)
  chapters.sort((a, b) => {
    return a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: 'base' });
  });

  return chapters;
}

export function getChapterDir(title: string): string {
  // Paths are relative to the book root (rootDirHandle / backend book root),
  // so they never include the root directory name.
  return title;
}

// === v2.0 Paths ===

export function getDialoguePath(title: string): string {
  return `${getChapterDir(title)}/dialogue.json`;
}

export function getCentralCharactersPath(): string {
  // Paths are relative to rootDirHandle, not including root directory name
  return `characters.json`;
}

export function getVoicesPath(): string {
  // Paths are relative to rootDirHandle, not including root directory name
  return `voices.json`;
}

export function getSettingsPath(): string {
  // Paths are relative to rootDirHandle, not including root directory name
  return `settings.json`;
}

// === Legacy v1.0 Paths (deprecated) ===

/** @deprecated Use getDialoguePath instead */
export function getScriptPath(title: string): string {
  return `${getChapterDir(title)}/${title}.script.json`;
}

/** @deprecated Chapter-level characters are deprecated in v2.0 */
export function getCharactersPath(title: string): string {
  return `${getChapterDir(title)}/${title}.characters.json`;
}

/** @deprecated Use getCentralCharactersPath instead */
export function getBookCharactersPath(): string {
  // When using File System Access API, paths are relative to rootDirHandle
  // So we just return the filename, not a full path
  return `characters.json`;
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
   * parsed JSON value, so `contents` must be JSON text.
   * Returns false when the write did not complete through this
   * transport (the composite then falls back to the sidecar).
   */
  writeText(path: string, contents: string): Promise<boolean>;
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
    if (!rootDirHandle) return false;
    try {
      const fileHandle = await getFileHandle(rootDirHandle, splitRelPath(path), true);
      if (!fileHandle) return false;
      const writable = await fileHandle.createWritable();
      await writable.write(contents);
      await writable.close();
      return true;
    } catch (error) {
      console.warn(`[fs] File System API write failed for ${path}; trying backend`, error);
      return false;
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
  async writeText(path, contents) {
    try {
      await apiPostVoid<SaveFileRequest>(API_ENDPOINTS.save, {
        file_path: path,
        content: JSON.parse(contents) as unknown,
      });
      return true;
    } catch (error) {
      console.error(`Failed to write to ${path}`, error);
      return false;
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
    writeText: (path, contents) =>
      fsaTransport.writeText(path, contents).then(
        (ok) => ok || httpTransport.writeText(path, contents),
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
  return activeTransport().writeText(path, contents);
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

async function writeFile(path: string, contents: string, recordHistory = true): Promise<boolean> {
  if (!recordHistory || path.startsWith('_backups/')) return writeFileDirect(path, contents);
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
        const success = value === null
          ? await deleteFileDirect('', path)
          : await writeFileDirect(path, value);
        if (!success) throw new Error(`Could not restore or save ${path}`);
      },
      sameEditableJson,
    );
    return true;
  } catch (error) {
    history.reportError(error);
    return false;
  }
}

// === v2.0 Read/Write Functions ===

export async function readDialogue(path: string): Promise<DialogueJson | null> {
  return readFileAsJson<DialogueJson>(path);
}

export async function readOptionalDialogue(path: string): Promise<DialogueJson | null> {
  return readFileAsJson<DialogueJson>(path, false);
}

export async function writeDialogue(path: string, data: DialogueJson, recordHistory = true): Promise<boolean> {
  return writeFile(path, JSON.stringify(data, null, 2), recordHistory);
}

export async function readVoices(root: string): Promise<VoicesJson | null> {
  // When using FileSystemDirectoryHandle, rootDirHandle is already at the book directory level
  return readFileAsJson<VoicesJson>('voices.json');
}

export async function writeVoices(root: string, data: VoicesJson, recordHistory = true): Promise<boolean> {
  // When using FileSystemDirectoryHandle, rootDirHandle is already at the book directory level
  return writeFile('voices.json', JSON.stringify(data, null, 2), recordHistory);
}

export async function readCentralCharacters(root: string): Promise<CharactersJson | null> {
  const folder = await readCharacterFolder(root);
  if (folder) return folder;
  return readFileAsJson<CharactersJson>('characters.json', false);
}

export async function writeCentralCharacters(root: string, data: CharactersJson, recordHistory = true): Promise<boolean> {
  // When using FileSystemDirectoryHandle, rootDirHandle is already at the book directory level
  return writeFile('characters.json', JSON.stringify(data, null, 2), recordHistory);
}

export async function readSettings(root: string): Promise<any | null> {
  return readFileAsJson<any>('settings.json');
}

export async function writeSettings(root: string, data: any, recordHistory = true): Promise<boolean> {
  return writeFile('settings.json', JSON.stringify(data, null, 2), recordHistory);
}

// Legacy: Read old speaker_blocklist.json format
export async function readSpeakerBlocklist(root: string): Promise<any | null> {
  return readFileAsJson<any>('speaker_blocklist.json');
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
  return writeFile(`characters/${fileName}`, JSON.stringify(content, null, 2), recordHistory);
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

// === Legacy v1.0 Functions (for backward compatibility) ===

export async function deleteFile(root: string, relPath: string, recordHistory = true): Promise<boolean> {
  if (!recordHistory) return deleteFileDirect(root, relPath);
  try {
    await history.write('Delete character', relPath, null, () => readHistoryFile(relPath), async (value) => {
      const success = value === null
        ? await deleteFileDirect(root, relPath)
        : await writeFileDirect(relPath, value);
      if (!success) throw new Error(`Could not restore or delete ${relPath}`);
    }, sameEditableJson);
    return true;
  } catch (error) {
    history.reportError(error);
    return false;
  }
}

/** @deprecated Use readDialogue instead */
export async function readScript(path: string): Promise<ScriptJson | null> {
  return readFileAsJson<ScriptJson>(path);
}

export async function readOptionalScript(path: string): Promise<ScriptJson | null> {
  return readFileAsJson<ScriptJson>(path, false);
}

/**
 * Trigger backend to update character stats from dialogue files.
 * Debounced: multiple calls within 500ms collapse into one.
 */
let _statsUpdateInFlight = false;
export async function triggerStatsUpdate(): Promise<void> {
  if (_statsUpdateInFlight) return; // Already in progress, skip
  _statsUpdateInFlight = true;
  try {
    // First, ensure backend knows the book root path
    const rootHandle = getRootDirHandle();
    if (!rootHandle) {
      console.warn('[triggerStatsUpdate] No root handle available');
      return;
    }

    // Try to get absolute path for backend
    const resolvedPath = await resolveBookRootPath(rootHandle);
    if (!resolvedPath) {
      console.warn('[triggerStatsUpdate] Could not resolve book root path for backend');
      return;
    }

    const didSync = await setBackendBookRoot(resolvedPath);
    if (!didSync) {
      console.warn('[triggerStatsUpdate] Could not set book root on backend');
      return;
    }

    // Now trigger the stats update
    const result = await apiPostJson<UpdateCharacterStatsResponse>(API_ENDPOINTS.updateCharacterStats);
    console.log('[triggerStatsUpdate] Character stats updated:', result.stats);
  } catch (error) {
    console.warn('[triggerStatsUpdate] Could not update character stats:', error);
    // Non-blocking - don't fail the save if stats update fails
  } finally {
    _statsUpdateInFlight = false;
  }
}

/** @deprecated Use writeDialogue instead */
export async function writeScript(path: string, data: ScriptJson): Promise<boolean> {
  const success = await writeFile(path, JSON.stringify(data, null, 2));

  // If we saved any dialogue/script file, update character stats
  // This covers both v1.0 (script.json) and v2.0 (dialogue.json) formats
  if (success && (path.includes('dialogue.json') || path.includes('.script.json'))) {
    // Don't await - update in background
    triggerStatsUpdate().catch(err => console.warn('Stats update failed:', err));
  }

  return success;
}

export async function readOptionalCharacters(path: string): Promise<CharactersJson | null> {
  return readFileAsJson<CharactersJson>(path, false);
}

/** @deprecated Use writeCentralCharacters instead */
export async function writeCharacters(path: string, data: CharactersJson, recordHistory = true): Promise<boolean> {
  return writeFile(path, JSON.stringify(data, null, 2), recordHistory);
}

export async function readTextFile(path: string): Promise<string | null> {
  // Plain text read: no `optional` flag is forwarded to the sidecar.
  return activeTransport().readText(path, {});
}

/**
 * Read dialogue for a chapter, trying v2.0 format first, falling back to v1.0
 */
export async function readDialogueForChapter(chapterTitle: string): Promise<DialogueJson | ScriptJson | null> {
  try {
    const dialogueParsed = await readFileAsJson<DialogueJson | ScriptJson>(`${chapterTitle}/dialogue.json`);
    if (
      dialogueParsed &&
      typeof dialogueParsed === 'object' &&
      'formatVersion' in dialogueParsed &&
      (
        dialogueParsed.formatVersion === '2.0'
        || dialogueParsed.formatVersion === '3.0'
        || dialogueParsed.formatVersion === '3.1'
        || dialogueParsed.formatVersion === '3.2'
      )
    ) {
      return dialogueParsed as DialogueJson;
    }

    // Fall back to v1.0 script.json
    const scriptParsed = await readFileAsJson<ScriptJson>(`${chapterTitle}/${chapterTitle}.script.json`);
    if (scriptParsed) {
      return scriptParsed as ScriptJson;
    }

    // Retry dialogue.json as legacy/non-versioned JSON
    if (dialogueParsed) {
      return dialogueParsed as ScriptJson;
    }

    // Compatibility fallback used in some migrated chapters
    const dialogueOldParsed = await readFileAsJson<ScriptJson>(`${chapterTitle}/dialogue-old.json`);
    if (dialogueOldParsed) {
      return dialogueOldParsed as ScriptJson;
    }

    return null;
  } catch (e) {
    return null;
  }
}

/** @deprecated Use readDialogueForChapter instead */
export async function readScriptForChapter(chapterTitle: string): Promise<ScriptJson | null> {
  if (!rootDirHandle) return null;
  try {
    const chapterDirHandle = await rootDirHandle.getDirectoryHandle(chapterTitle, { create: false });
    const scriptFileHandle = await chapterDirHandle.getFileHandle(`${chapterTitle}.script.json`, { create: false });
    const file = await scriptFileHandle.getFile();
    const contents = await file.text();
    return JSON.parse(contents) as ScriptJson;
  } catch (e) {
    return null;
  }
}
