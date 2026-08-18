import { writable, get } from 'svelte/store';
import type { CharacterConfig, CharactersJson, Character } from '$lib/types';
import { currentScript, bookRoot, currentChapter } from '$lib/stores/bookState';
import { readCentralCharacters, getCharactersPath } from '$lib/services/fs';
import { loadChapterCharactersData } from '$lib/services/chapterCharacterRepository';
import { defaultColors } from '$lib/theme/colors';
import { bookCharacters } from '$lib/stores/bookCharacters';
import { normalizeCharacterGender } from '$lib/services/characterGender';

export const characters = writable<CharactersJson>({ formatVersion: '2.0', characters: [] });

let _chapterExtrasByName = new Map<string, CharacterConfig>();
let _currentChapterRef: any = null;
let _currentRoot: string | null = null;
let _chapterLoadToken = 0;

function normalizeCharacterKey(name: string): string {
  return String(name || '').trim().toLowerCase();
}

function mergeNamesWithExtras(names: string[]): string[] {
  const merged = new Set(names);
  for (const name of _chapterExtrasByName.keys()) merged.add(name);
  return Array.from(merged).sort();
}

function buildDerivedCharacters(names: string[]): CharactersJson {
  const global = get(bookCharacters);
  const byName = new Map((global?.characters ?? []).map((c) => [c.name, c] as const));
  const derived: CharactersJson = {
    formatVersion: '2.0',
    characters: names.map((name) => {
      const globalChar = byName.get(name);
      const chapterChar = _chapterExtrasByName.get(name);
      const base: CharacterConfig = {
        name,
        gender: normalizeCharacterGender(globalChar?.gender ?? chapterChar?.gender ?? 'Unknown'),
        color: globalChar?.color ?? chapterChar?.color ?? null,
        voice: globalChar?.voice ?? chapterChar?.voice ?? null,
        provider: globalChar?.provider ?? chapterChar?.provider ?? null,
        voiceId: globalChar?.voiceId ?? chapterChar?.voiceId ?? null,
        voiceMeta: globalChar?.voiceMeta ?? chapterChar?.voiceMeta ?? null,
        manifestStats: globalChar?.manifestStats ?? chapterChar?.manifestStats ?? null,
        count: globalChar?.count ?? chapterChar?.count ?? 0,
      };
      return base;
    }) as Character[]
  };
  return derived;
}

function updateDerivedFromScript(scr: any): void {
  if (!scr) {
    if (_lastDerivedNames.length > 0) {
      characters.set({ formatVersion: '2.0', characters: [] });
      _lastDerivedNames = [];
    }
    return;
  }

  const globalChars = get(bookCharacters);
  const aliasToCanonical = new Map<string, string>();
  const nameToCanonical = new Map<string, string>();
  const idToCanonical = new Map<string, string>();
  for (const character of globalChars.characters) {
    const canonicalKey = normalizeCharacterKey(character.name);
    if (canonicalKey) nameToCanonical.set(canonicalKey, character.name);
    const idKey = normalizeCharacterKey(character.id);
    if (idKey) idToCanonical.set(idKey, character.name);
    const aliases = Array.isArray(character.aliases) ? character.aliases : [];
    for (const alias of aliases) {
      const key = normalizeCharacterKey(alias);
      if (key) aliasToCanonical.set(key, character.name);
    }
  }

  const namesFromScript = Array.from(new Set(scr.lines
    .map((l: any) => (l.chosenSpeaker ?? '').trim())
    .filter((n: string) => n.length > 0)
    .map((n: string) => {
      const key = normalizeCharacterKey(n);
      return idToCanonical.get(key) || aliasToCanonical.get(key) || nameToCanonical.get(key) || n;
    })
  )).sort();

  const names = mergeNamesWithExtras(namesFromScript);
  if (names.length === _lastDerivedNames.length && names.every((n, i) => n === _lastDerivedNames[i])) return;
  _lastDerivedNames = names;

  characters.set(buildDerivedCharacters(names));
}

async function loadChapterExtras(ch: any, root: string | null): Promise<void> {
  const token = ++_chapterLoadToken;
  if (!ch || !root) {
    _chapterExtrasByName = new Map();
    updateDerivedFromScript(get(currentScript));
    return;
  }

  try {
    const path = getCharactersPath(root, ch.title);
    const data = await loadChapterCharactersData(root, path);
    if (token !== _chapterLoadToken) return;
    const next = new Map<string, CharacterConfig>();
    if (data?.characters) {
      for (const c of data.characters) {
        if (c?.name) next.set(c.name, c as CharacterConfig);
      }
    }
    _chapterExtrasByName = next;
  } catch {
    if (token !== _chapterLoadToken) return;
    _chapterExtrasByName = new Map();
  }

  updateDerivedFromScript(get(currentScript));
}

// Derive chapter characters from the already-loaded currentScript (no redundant file read).
// Only update when the set of character names actually changes.
let _lastDerivedNames: string[] = [];
let _unsubChapter = currentScript.subscribe((scr) => {
  updateDerivedFromScript(scr);
});

let _unsubBookChars = bookCharacters.subscribe(() => {
  updateDerivedFromScript(get(currentScript));
});

let _unsubChapterMeta = currentChapter.subscribe((ch) => {
  _currentChapterRef = ch;
  loadChapterExtras(_currentChapterRef, _currentRoot);
});

let _unsubRoot = bookRoot.subscribe((root) => {
  _currentRoot = root;
  loadChapterExtras(_currentChapterRef, _currentRoot);
});

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    _unsubChapter();
    _unsubChapterMeta();
    _unsubRoot();
    _unsubBookChars();
  });
}

export async function refreshChapterCharactersFromFile(): Promise<void> {
  await loadChapterExtras(_currentChapterRef, _currentRoot);
}

export function hashToIndex(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  return Math.abs(h) % defaultColors.length;
}

export function colorForCharacter(name: string | null | undefined, charsJson?: CharactersJson | null): string {
  const key = name && name.trim() ? name : 'Unknown';

  // Special case for Narrator (case-insensitive)
  if (key.toLowerCase() === 'narrator') {
    // Check book-level first for consistency
    const bookChars = get(bookCharacters);
    const bookMatched = bookChars?.characters?.find(ch => ch.name.toLowerCase() === 'narrator');
    if (bookMatched && bookMatched.color) return bookMatched.color;

    const chars = charsJson ?? get(characters);
    const matched = chars?.characters?.find(ch => ch.name.toLowerCase() === 'narrator');
    if (matched && matched.color) return matched.color;
    return '#ffffff'; // Default white for Narrator
  }

  // Always check book-level store first (single source of truth)
  const bookChars = get(bookCharacters);
  const bookMatched = bookChars?.characters?.find(ch => ch.name === key);
  if (bookMatched && bookMatched.color) return bookMatched.color;

  // Fall back to chapter-level if book doesn't have it
  const chars = charsJson ?? get(characters);
  const matched = chars?.characters?.find(ch => ch.name === key);
  if (matched && matched.color) return matched.color;

  // Return hash-based default (will be persisted on first use)
  return defaultColors[hashToIndex(key)];
}

export function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace('#', '');
  const bigint = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  const r = (bigint >> 16) & 255;
  const g = (bigint >> 8) & 255;
  const b = bigint & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Convert rgba with alpha to opaque hex (as it appears over white background)
export function rgbaToOpaqueHex(hex: string, alpha: number): string {
  const h = hex.replace('#', '');
  const bigint = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  const r = (bigint >> 16) & 255;
  const g = (bigint >> 8) & 255;
  const b = bigint & 255;

  // Blend with white background
  const blendedR = Math.round(r * alpha + 255 * (1 - alpha));
  const blendedG = Math.round(g * alpha + 255 * (1 - alpha));
  const blendedB = Math.round(b * alpha + 255 * (1 - alpha));

  return '#' + [blendedR, blendedG, blendedB]
    .map(v => v.toString(16).padStart(2, '0'))
    .join('');
}

// Convert opaque color back to what it would be with alpha over white
export function opaqueHexToRgba(opaqueHex: string, targetAlpha: number): string {
  const h = opaqueHex.replace('#', '');
  const bigint = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  const blendedR = (bigint >> 16) & 255;
  const blendedG = (bigint >> 8) & 255;
  const blendedB = bigint & 255;

  // Reverse the blending: blended = original * alpha + 255 * (1 - alpha)
  // original = (blended - 255 * (1 - alpha)) / alpha
  const originalR = Math.round((blendedR - 255 * (1 - targetAlpha)) / targetAlpha);
  const originalG = Math.round((blendedG - 255 * (1 - targetAlpha)) / targetAlpha);
  const originalB = Math.round((blendedB - 255 * (1 - targetAlpha)) / targetAlpha);

  // Clamp to valid range
  const clampedR = Math.max(0, Math.min(255, originalR));
  const clampedG = Math.max(0, Math.min(255, originalG));
  const clampedB = Math.max(0, Math.min(255, originalB));

  return '#' + [clampedR, clampedG, clampedB]
    .map(v => v.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Get character ID from character name by reading characters.json
 * Returns null if character not found - NO FALLBACK CONVERSION
 * Name should ONLY be used for display, ID for data operations
 */
export async function getCharacterIdByName(characterName: string): Promise<string | null> {
  const root = get(bookRoot);
  if (!root) {
    console.warn('[characters] Book root not set, cannot get character ID for:', characterName);
    return null;
  }

  try {
    const charactersData = await readCentralCharacters(root);
    if (!charactersData || !charactersData.characters) {
      console.warn('[characters] No characters.json found, cannot get character ID for:', characterName);
      return null;
    }

    // Find character by name (case-insensitive)
    const nameLower = characterName.toLowerCase();
    const char = charactersData.characters.find((c: any) =>
      c.name?.toLowerCase() === nameLower
    );

    if (char && char.id) {
      return char.id;
    }

    // NO FALLBACK - return null if not found
    console.warn('[characters] Character not found in characters.json:', characterName);
    return null;
  } catch (err) {
    console.error('[characters] Error reading characters.json:', err);
    return null;
  }
}

/**
 * Get character name from character ID by reading characters.json
 * Falls back to the ID if not found
 */
export async function getCharacterNameById(characterId: string): Promise<string | null> {
  const root = get(bookRoot);
  if (!root) {
    console.warn('[characters] Book root not set, using characterId as-is:', characterId);
    return characterId;
  }

  try {
    const charactersData = await readCentralCharacters(root);
    if (!charactersData || !charactersData.characters) {
      console.warn('[characters] No characters.json found, using characterId as-is:', characterId);
      return characterId;
    }

    // Find character by ID (case-insensitive)
    const idLower = characterId.toLowerCase();
    const char = charactersData.characters.find((c: any) =>
      c.id?.toLowerCase() === idLower
    );

    if (char && char.name) {
      return char.name;
    }

    // Fallback: capitalize first letter if not found
    console.warn('[characters] Character ID not found in characters.json:', characterId, '- using capitalized version');
    return characterId.charAt(0).toUpperCase() + characterId.slice(1);
  } catch (err) {
    console.error('[characters] Error reading characters.json:', err);
    // Fallback: capitalize first letter
    return characterId.charAt(0).toUpperCase() + characterId.slice(1);
  }
}

/**
 * Get character names from character IDs using a provided Character array.
 * This is a synchronous helper that works with an in-memory array.
 * Falls back to ID if character not found.
 */
export function getCharacterNamesByIds(characterIds: string[], charactersArray: Character[]): string[] {
  const idToName = new Map<string, string>();

  // Build a map from the characters array
  for (const char of charactersArray) {
    if (char.id && char.name) {
      idToName.set(char.id.toLowerCase(), char.name);
    }
  }

  const uniqueNames = new Set<string>();

  for (const id of characterIds) {
    const name = idToName.get(id.toLowerCase()) || id;
    uniqueNames.add(name);
  }

  return Array.from(uniqueNames).sort();
}

/**
 * Get character name from character ID using a provided Character array.
 * This is a synchronous helper that works with an in-memory array.
 * Falls back to ID if character not found.
 */
export function getCharacterNameByIdSync(characterId: string, charactersArray: Character[]): string {
  const char = charactersArray.find(c => c.id?.toLowerCase() === characterId.toLowerCase());
  return char?.name || characterId;
}

/**
 * Build a name-to-ID map from a Character array.
 * Returns a Map<string, string> where keys are character names (lowercase) and values are IDs.
 */
export function buildNameToIdMap(charactersArray: Character[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const char of charactersArray) {
    if (char.id && char.name) {
      map.set(char.name.toLowerCase(), char.id);
    }
  }
  for (const char of charactersArray) {
    if (!char.id || !Array.isArray(char.aliases)) continue;
    for (const alias of char.aliases) {
      const key = String(alias || '').toLowerCase().trim();
      if (!key || map.has(key)) continue;
      map.set(key, char.id);
    }
  }
  return map;
}

/**
 * Build an ID-to-name map from a Character array.
 * Returns a Map<string, string> where keys are character IDs (lowercase) and values are names.
 */
export function buildIdToNameMap(charactersArray: Character[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const char of charactersArray) {
    if (char.id && char.name) {
      map.set(char.id.toLowerCase(), char.name);
    }
  }
  return map;
}

