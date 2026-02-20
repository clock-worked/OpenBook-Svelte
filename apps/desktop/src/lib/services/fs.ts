// Browser File System Access API implementation
import type { ChapterStatus } from '$lib/stores/bookState';
import { get } from 'svelte/store';
import { bookRootPathOverride } from '$lib/stores/settings';
import type {
  DialogueJson,
  CharactersJson,
  VoicesJson,
  // Legacy v1.0 types
  ScriptJson,
  LineItem
} from '$lib/types';
import { storeProjectHandle } from './persistence';

let rootDirHandle: FileSystemDirectoryHandle | null = null;
const API_BASE_URL = 'http://127.0.0.1:8010';

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

    // Request directory with specific options for better stability
    // Wrapped in timeout to prevent hanging (30 seconds should be plenty)
    const pickerPromise = window.showDirectoryPicker({
      mode: 'readwrite',
      startIn: 'documents' // Start in documents folder by default
    });

    rootDirHandle = await withTimeout(
      pickerPromise,
      30000,
      'File picker timed out. Please try again.'
    );

    // Store the handle for persistence
    if (rootDirHandle) {
      await storeProjectHandle(rootDirHandle);
    }

    return rootDirHandle;
  } catch (err) {
    // User cancelled the picker - this is normal, not an error
    if (err instanceof Error && err.name === 'AbortError') {
      console.log('Directory picker cancelled by user');
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

export function setRootDirHandle(handle: FileSystemDirectoryHandle): void {
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
    const response = await fetch(`${API_BASE_URL}/api/set-book-root`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root_path: rootPath }),
    });
    if (!response.ok) {
      console.warn('[fs] Failed to set backend book root:', await response.text());
      return false;
    }
    return true;
  } catch (error) {
    console.warn('[fs] Error setting backend book root:', error);
    return false;
  }
}

export async function setBackendAudioRoot(audioRoot: string): Promise<boolean> {
  if (!audioRoot || !isLikelyAbsolutePath(audioRoot)) return false;

  try {
    const response = await fetch(`${API_BASE_URL}/api/set-audio-root`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio_root: audioRoot }),
    });
    if (!response.ok) {
      console.warn('[fs] Failed to set backend audio root:', await response.text());
      return false;
    }
    return true;
  } catch (error) {
    console.warn('[fs] Error setting backend audio root:', error);
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
    const response = await fetch(`${API_BASE_URL}/api/list-chapters`);
    if (!response.ok) {
      console.warn('[fs] Failed to list chapters from backend:', response.statusText);
      return [];
    }

    const payload = await response.json();
    const backendChapters = Array.isArray(payload?.chapters) ? payload.chapters : [];
    return backendChapters.map((ch: any) => {
      const title = String(ch?.name || '').replace(/\.txt$/i, '');
      return {
        path: String(ch?.path || ''),
        title,
        parsed: !!ch?.parsed,
        complete: false,
        audio: false,
        scriptPath: ch?.scriptPath,
      } as ChapterStatus;
    });
  } catch (error) {
    console.warn('[fs] Error listing chapters from backend:', error);
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

      // Try v2.0 dialogue.json first
      let dialogueFileHandle = await chapterDirHandle.getFileHandle('dialogue.json', { create: false }).catch(() => null);
      if (dialogueFileHandle) {
        parsed = true;
        scriptPath = `${title}/dialogue.json`;
      }

      chapters.push({
        path: `${title}/chapter.txt`,
        title: title,
        parsed: parsed,
        complete: false, // This will need to be updated based on metadata file
        audio: false, // This will need to be updated
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

export function getChapterDir(root: string, title: string): string {
  // When using File System Access API, paths are relative to rootDirHandle
  // So we don't include the root directory name in the path
  return title;
}

// === v2.0 Paths ===

export function getDialoguePath(root: string, title: string): string {
  return `${getChapterDir(root, title)}/dialogue.json`;
}

export function getCentralCharactersPath(root: string): string {
  // Paths are relative to rootDirHandle, not including root directory name
  return `characters.json`;
}

export function getVoicesPath(root: string): string {
  // Paths are relative to rootDirHandle, not including root directory name
  return `voices.json`;
}

export function getSettingsPath(root: string): string {
  // Paths are relative to rootDirHandle, not including root directory name
  return `settings.json`;
}

// === Legacy v1.0 Paths (deprecated) ===

/** @deprecated Use getDialoguePath instead */
export function getScriptPath(root: string, title: string): string {
  return `${getChapterDir(root, title)}/${title}.script.json`;
}

/** @deprecated Chapter-level characters are deprecated in v2.0 */
export function getCharactersPath(root: string, title: string): string {
  return `${getChapterDir(root, title)}/${title}.characters.json`;
}

/** @deprecated Use getCentralCharactersPath instead */
export function getBookCharactersPath(root: string): string {
  // When using File System Access API, paths are relative to rootDirHandle
  // So we just return the filename, not a full path
  return `characters.json`;
}

async function readFileAsJson<T>(path: string): Promise<T | null> {
  if (!rootDirHandle) return null;
  try {
    const pathParts = path.split('/').filter(p => p);
    const fileHandle = await getFileHandle(rootDirHandle, pathParts);
    if (!fileHandle) return null;
    const file = await fileHandle.getFile();
    const contents = await file.text();
    return JSON.parse(contents) as T;
  } catch (e) {
    console.error(`Failed to read or parse JSON from ${path}`, e);
    return null;
  }
}

export async function readJsonRelative<T>(path: string): Promise<T | null> {
  return readFileAsJson<T>(path);
}

async function writeFile(path: string, contents: string): Promise<boolean> {
  if (!rootDirHandle) return false;
  try {
    const pathParts = path.split('/').filter(p => p);
    const fileHandle = await getFileHandle(rootDirHandle, pathParts, true);
    if (!fileHandle) return false;
    const writable = await fileHandle.createWritable();
    await writable.write(contents);
    await writable.close();
    return true;
  } catch (e) {
    console.error(`Failed to write to ${path}`, e);
    return false;
  }
}

// === v2.0 Read/Write Functions ===

export async function readDialogue(path: string): Promise<DialogueJson | null> {
  return readFileAsJson<DialogueJson>(path);
}

export async function writeDialogue(path: string, data: DialogueJson): Promise<boolean> {
  return writeFile(path, JSON.stringify(data, null, 2));
}

export async function readVoices(root: string): Promise<VoicesJson | null> {
  // When using FileSystemDirectoryHandle, rootDirHandle is already at the book directory level
  return readFileAsJson<VoicesJson>('voices.json');
}

export async function writeVoices(root: string, data: VoicesJson): Promise<boolean> {
  // When using FileSystemDirectoryHandle, rootDirHandle is already at the book directory level
  return writeFile('voices.json', JSON.stringify(data, null, 2));
}

export async function readCentralCharacters(root: string): Promise<CharactersJson | null> {
  // When using FileSystemDirectoryHandle, rootDirHandle is already at the book directory level
  // so we just need "characters.json", not "${root}/characters.json"
  return readFileAsJson<CharactersJson>('characters.json');
}

export async function writeCentralCharacters(root: string, data: CharactersJson): Promise<boolean> {
  // When using FileSystemDirectoryHandle, rootDirHandle is already at the book directory level
  return writeFile('characters.json', JSON.stringify(data, null, 2));
}

export async function readSettings(root: string): Promise<any | null> {
  return readFileAsJson<any>('settings.json');
}

export async function writeSettings(root: string, data: any): Promise<boolean> {
  return writeFile('settings.json', JSON.stringify(data, null, 2));
}

// Legacy: Read old speaker_blocklist.json format
export async function readSpeakerBlocklist(root: string): Promise<any | null> {
  return readFileAsJson<any>('speaker_blocklist.json');
}

// === Legacy v1.0 Functions (for backward compatibility) ===

/** @deprecated Use readDialogue instead */
export async function readScript(path: string): Promise<ScriptJson | null> {
  return readFileAsJson<ScriptJson>(path);
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
    const response = await fetch(`${API_BASE_URL}/api/update-character-stats`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });

    if (!response.ok) {
      console.warn('[triggerStatsUpdate] Failed to update character stats:', response.statusText);
    } else {
      const result = await response.json();
      console.log('[triggerStatsUpdate] Character stats updated:', result.stats);
    }
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

/** @deprecated Use readCentralCharacters or readVoices instead */
export async function readCharacters(path: string): Promise<CharactersJson | null> {
  return readFileAsJson<CharactersJson>(path);
}

/** @deprecated Use writeCentralCharacters instead */
export async function writeCharacters(path: string, data: CharactersJson): Promise<boolean> {
  return writeFile(path, JSON.stringify(data, null, 2));
}

export async function readTextFile(path: string): Promise<string | null> {
  if (!rootDirHandle) {
    console.error('[readTextFile] No rootDirHandle available');
    return null;
  }
  try {
    const pathParts = path.split('/').filter(p => p);
    console.log('[readTextFile] Reading file at path:', path, 'split into parts:', pathParts);
    const fileHandle = await getFileHandle(rootDirHandle, pathParts);
    if (!fileHandle) {
      console.error('[readTextFile] Could not get file handle for path:', path);
      return null;
    }
    const file = await fileHandle.getFile();
    const text = await file.text();
    console.log('[readTextFile] Successfully read', text.length, 'characters from', path);
    return text;
  } catch (e) {
    console.error(`[readTextFile] Failed to read text from ${path}:`, e);
    return null;
  }
}

/**
 * Read dialogue for a chapter, trying v2.0 format first, falling back to v1.0
 */
export async function readDialogueForChapter(chapterTitle: string): Promise<DialogueJson | ScriptJson | null> {
  if (!rootDirHandle) return null;

  async function tryReadJsonFromChapterFile(
    chapterDirHandle: FileSystemDirectoryHandle,
    fileName: string,
  ): Promise<DialogueJson | ScriptJson | null> {
    try {
      const fileHandle = await chapterDirHandle.getFileHandle(fileName, { create: false });
      const file = await fileHandle.getFile();
      const contents = await file.text();
      return JSON.parse(contents) as DialogueJson | ScriptJson;
    } catch {
      return null;
    }
  }

  try {
    const chapterDirHandle = await rootDirHandle.getDirectoryHandle(chapterTitle, { create: false });

    // Try v2.0 dialogue.json first
    const dialogueParsed = await tryReadJsonFromChapterFile(chapterDirHandle, 'dialogue.json');
    if (
      dialogueParsed &&
      typeof dialogueParsed === 'object' &&
      'formatVersion' in dialogueParsed &&
      (dialogueParsed.formatVersion === '2.0' || dialogueParsed.formatVersion === '3.0')
    ) {
      return dialogueParsed as DialogueJson;
    }

    // Fall back to v1.0 script.json
    const scriptParsed = await tryReadJsonFromChapterFile(chapterDirHandle, `${chapterTitle}.script.json`);
    if (scriptParsed) {
      return scriptParsed as ScriptJson;
    }

    // Retry dialogue.json as legacy/non-versioned JSON
    if (dialogueParsed) {
      return dialogueParsed as ScriptJson;
    }

    // Compatibility fallback used in some migrated chapters
    const dialogueOldParsed = await tryReadJsonFromChapterFile(chapterDirHandle, 'dialogue-old.json');
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

