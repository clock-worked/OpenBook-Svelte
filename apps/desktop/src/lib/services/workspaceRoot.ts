// The workspace root: which folder is the open book, and how the app and
// the sidecar agree on it. Picking a folder (File System Access or the
// backend picker), resolving its absolute path, syncing the book and audio
// roots to the sidecar, and listing the book's chapters.
//
// File access inside the book is fs.ts (the transport, which this module
// attaches the picked handle to); what the files are is bookFormat.ts.

import { get } from 'svelte/store';
import type { ChapterStatus } from '$lib/stores/bookState';
import { bookRootPathOverride } from '$lib/stores/settings';
import type { DialogueJson } from '$lib/types';
import { storeProjectHandle } from './persistence';
import { getRootDirHandle, setRootDirHandle } from './fs';
import { API_ENDPOINTS, apiPostJson, apiPostVoid, apiRequestJson, toApiClientError } from './apiClient';
import type {
  ListChaptersResponse,
  PickBookDirectoryResponse,
  SetAudioRootRequest,
  SetBookRootRequest,
  SetBookRootResponse,
  UpdateCharacterStatsResponse,
} from './apiContracts';

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

    const handle = await withTimeout(
      pickerPromise,
      30000,
      'File picker timed out. Please try again.'
    );
    setRootDirHandle(handle);

    // Persistence is optional for opening the project. Browsers can reject
    // serializing a handle even though the selected handle remains usable.
    if (handle) {
      try {
        await storeProjectHandle(handle);
      } catch (error) {
        console.warn('Selected directory will not be remembered after reload:', error);
      }
    }

    return handle;
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
    const result = await apiPostJson<SetBookRootResponse, SetBookRootRequest>(API_ENDPOINTS.setBookRoot, payload);
    if (result?.stats_rebuilt === false) {
      console.warn('[workspaceRoot] Book root set, but the backend could not build character stats; they will be rebuilt on the next dialogue save.');
    }
    return true;
  } catch (error) {
    const apiError = toApiClientError(error);
    console.warn(`[workspaceRoot] Error setting backend book root (${apiError.type}):`, apiError.message);
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
    console.warn(`[workspaceRoot] Error setting backend audio root (${apiError.type}):`, apiError.message);
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
    console.warn(`[workspaceRoot] Error listing chapters from backend (${apiError.type}):`, apiError.message);
    return [];
  }
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
