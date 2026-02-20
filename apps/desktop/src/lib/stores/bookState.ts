import { writable } from 'svelte/store';
import type { ScriptJson } from '$lib/types';

export interface ChapterStatus {
  path: string;
  title: string;
  parsed: boolean;
  complete: boolean;
  audio: boolean;
  scriptPath?: string;
}

const DEFAULT_SCRIPT_ROOT: string | null = null;
const DEFAULT_AUDIO_ROOT: string | null = null;
const DEFAULT_VOICE_SAMPLES_ROOT: string | null = null;

const scriptRootStore = writable<string | null>(DEFAULT_SCRIPT_ROOT);

export const scriptRoot = scriptRootStore;
// Backward compatibility: existing code references bookRoot for script assets
export const bookRoot = scriptRootStore;
// Store for the FileSystemDirectoryHandle (browser-based file access)
export const bookRootHandle = writable<FileSystemDirectoryHandle | null>(null);
// Store for the absolute path of the book root (for backend API)
export const bookRootAbsolutePath = writable<string | null>(null);
export const audioRoot = writable<string | null>(DEFAULT_AUDIO_ROOT);
export const voiceSamplesRoot = writable<string | null>(DEFAULT_VOICE_SAMPLES_ROOT);
export const chapters = writable<ChapterStatus[]>([]);
export const currentChapter = writable<ChapterStatus | null>(null);
export const currentScript = writable<ScriptJson | null>(null);

// Constants for localStorage
const LAST_CHAPTER_KEY = 'openbook_last_chapter_title';

// Persist last chapter to localStorage when it changes
// HMR-safe subscription: unsubscribe old listener on module reload
let _unsubPersist: (() => void) | null = null;
if (typeof window !== 'undefined') {
  _unsubPersist = currentChapter.subscribe((chapter) => {
    if (chapter) {
      localStorage.setItem(LAST_CHAPTER_KEY, chapter.title);
    }
  });
}

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    _unsubPersist?.();
  });
}

// Get last viewed chapter title
export function getLastChapterTitle(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(LAST_CHAPTER_KEY);
}

// Auto-select chapter: last viewed or first available
export function autoSelectChapter(chapterList: ChapterStatus[]): void {
  if (chapterList.length === 0) {
    currentChapter.set(null);
    return;
  }

  const lastTitle = getLastChapterTitle();

  // Try to find and select the last viewed chapter
  if (lastTitle) {
    const lastChapter = chapterList.find(ch => ch.title === lastTitle);
    if (lastChapter) {
      currentChapter.set(lastChapter);
      return;
    }
  }

  // Fallback: select first chapter
  currentChapter.set(chapterList[0]);
}


