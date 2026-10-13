import { writable, get } from 'svelte/store';
import { history } from '$lib/services/history';
import type { AudiobookSettings, ParserHints } from '$lib/types';

export type AppTheme = 'light' | 'dark';

function createStoredWritable<T>(key: string, defaultValue: T, historyLabel?: string) {
  const store = writable<T>(defaultValue);
  const { subscribe, set } = store;

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

  const setValue = (value: T) => {
    if (!historyLabel) {
      setStored(value);
      return;
    }
    void history.write(historyLabel, key, JSON.stringify(value),
      async () => JSON.stringify(get(store)),
      async (text) => {
        if (text === null) throw new Error(`Missing setting: ${key}`);
        setStored(JSON.parse(text) as T);
      },
    ).catch((error) => history.reportError(error));
  };

  return {
    subscribe,
    set: setValue,
    update: (updater: (value: T) => T) => setValue(updater(get(store))),
  };
}

export const pythonBin = writable<string>('python');
export const estimatedCostPerChar = writable<number>(0.0);
export const defaultNarrator = writable<string>('Narrator');

export const defaultAudiobookSettings: AudiobookSettings = {
  narrators: [],
  allowMultipleNarrators: false,
  narratorSameAsProtagonist: true,
  protagonist: null,
  mainCharacters: [],
  sideCharacters: [],
  defaultAccent: 'en-GB',
  speakingRate: 1.0,
};

export const defaultParserHints: ParserHints = {
  parserBackend: 'legacy',
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
};

export const audiobookSettings = writable<AudiobookSettings>(structuredClone(defaultAudiobookSettings));
export const parserHints = writable<ParserHints>(structuredClone(defaultParserHints));

// Optional absolute path for backend operations when File System Access API cannot provide one.
export const bookRootPathOverride = createStoredWritable<string | null>('bookRootPathOverride', null);
export const appTheme = createStoredWritable<AppTheme>('appTheme', 'light', 'Change theme');
