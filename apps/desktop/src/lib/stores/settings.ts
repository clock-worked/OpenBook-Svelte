import { writable } from 'svelte/store';
import type { AudiobookSettings, ParserHints } from '$lib/types';

export type AppTheme = 'light' | 'dark';

function createStoredWritable<T>(key: string, defaultValue: T) {
  const { subscribe, set, update } = writable<T>(defaultValue);

  if (typeof window !== 'undefined') {
    const storedValue = localStorage.getItem(key);
    if (storedValue) {
      try {
        set(JSON.parse(storedValue));
      } catch {
        localStorage.removeItem(key);
      }
    }
  }

  const setStored = (value: T) => {
    if (typeof window !== 'undefined') {
      localStorage.setItem(key, JSON.stringify(value));
    }
    set(value);
  };

  return {
    subscribe,
    set: setStored,
    update: (updater: (value: T) => T) => {
      update((current) => {
        const next = updater(current);
        if (typeof window !== 'undefined') {
          localStorage.setItem(key, JSON.stringify(next));
        }
        return next;
      });
    },
  };
}

export const pythonBin = writable<string>('python');
export const estimatedCostPerChar = writable<number>(0.0);
export const defaultNarrator = writable<string>('Narrator');

export const audiobookSettings = writable<AudiobookSettings>({
  narrators: [],
  allowMultipleNarrators: false,
  narratorSameAsProtagonist: true,
  protagonist: null,
  mainCharacters: [],
  sideCharacters: [],
  defaultAccent: 'en-GB',
  speakingRate: 1.0,
});

export const parserHints = writable<ParserHints>({
  protagonistName: '',
  protagonistNames: [],
  povMode: 'first_person',
  learnVerbs: false,
  heuristics: {
    protagonistFirstPersonTag: true,
    narratorIdentity: true,
    coreference: true,
    explicitTags: true,
    tagContinuation: true,
    contiguousDialogue: true,
    carryAcrossShortNarration: true,
    suggestAlternatives: true,
    firstPersonOverride: true,
    narratorFallback: true,
    vocativeGuard: true,
  },
  manualBlockList: ['he', 'she', 'as'],
  attribution: {
    unknownThreshold: 0.62,
  },
});

// Optional absolute path for backend operations when File System Access API cannot provide one.
export const bookRootPathOverride = createStoredWritable<string | null>('bookRootPathOverride', null);
export const appTheme = createStoredWritable<AppTheme>('appTheme', 'light');

