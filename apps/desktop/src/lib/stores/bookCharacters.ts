import { writable, get } from 'svelte/store';
import type { CharacterManifestStats, CharacterVoiceMeta, CharactersJson, TtsProvider, Character, Gender } from '$lib/types';
import { audioRoot, bookRoot, chapters } from '$lib/stores/bookState';
import { getCharactersPath, readCharacters, writeCharacters, readCentralCharacters } from '$lib/services/fs';
import { loadCharacterManifestSummary } from '$lib/services/manifests';
import {
  loadBookCharactersData,
  persistBookCharactersData,
} from '$lib/services/bookCharacterRepository';
import {
  addCentralCharacterAlias,
  detachCentralCharacterAlias,
  mergeCentralCharacters,
  renameCentralCharacter,
  setCentralCharacterPrimaryName,
} from '$lib/services/characterMutationService';
import {
  resolveCanonicalCharacterName,
} from '$lib/services/characterDomain';
import { normalizeCharacterGender } from '$lib/services/characterGender';
import { unassignVoicesFromCharacters } from '$lib/stores/speakers';

export const bookCharacters = writable<CharactersJson>({ formatVersion: '2.0', characters: [] });

let _loadBookCharTimer: ReturnType<typeof setTimeout> | null = null;
function loadBookCharacters() {
  // Debounce: bookRoot and chapters both subscribe and can fire together
  if (_loadBookCharTimer) clearTimeout(_loadBookCharTimer);
  _loadBookCharTimer = setTimeout(() => {
    _loadBookCharTimer = null;
    _loadBookCharactersImpl();
  }, 150);
}

async function _loadBookCharactersImpl() {
  const root = get(bookRoot);
  if (!root) {
    bookCharacters.set({ formatVersion: '2.0', characters: [] });
    return;
  }

  try {
    const chapterList = get(chapters).map((chapter: any) => ({
      title: chapter.title,
      parsed: chapter.parsed,
      scriptPath: chapter.scriptPath,
    }));
    const loaded = await loadBookCharactersData(root, chapterList);
    bookCharacters.set(loaded);
  } catch (err) {
    console.error('Error loading book characters:', err);
    bookCharacters.set({ formatVersion: '2.0', characters: [] });
  }
}

// HMR-safe subscriptions: unsubscribe old listeners on module reload
let _unsub1 = bookRoot.subscribe(() => { loadBookCharacters(); });
let _unsub2 = chapters.subscribe(() => { loadBookCharacters(); });

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    _unsub1();
    _unsub2();
    if (_loadBookCharTimer) clearTimeout(_loadBookCharTimer);
  });
}

export async function forceRefreshBookCharacters(): Promise<void> {
  await _loadBookCharactersImpl();
}

export async function persistBookCharacters(): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;
  await persistBookCharactersData(root, get(bookCharacters));
}

function createEmptyBookCharacter(name: string): Character {
  return {
    name,
    gender: 'Unknown',
    aliases: [],
    color: null,
    notes: '',
    stats: {
      totalLines: 0,
      chapterCount: 0,
    },
    voice: null,
    provider: null,
    voiceId: null,
    voiceMeta: null,
    manifestStats: null,
    count: 0,
    firstAppearance: null,
    chapterCount: 0,
  } as Character;
}

async function upsertBookCharacter(name: string, updates: Partial<Character>): Promise<void> {
  const data = get(bookCharacters);
  const canonicalName = resolveCanonicalCharacterName(data, name) ?? name;
  const exists = data.characters.some((character) => character.name === canonicalName);
  const normalizedUpdates: Partial<Character> = {
    ...updates,
    ...(Object.prototype.hasOwnProperty.call(updates, 'gender')
      ? { gender: normalizeCharacterGender(updates.gender) }
      : {}),
  };

  const next: CharactersJson = exists
    ? {
      formatVersion: data.formatVersion,
      characters: data.characters.map((character) =>
        character.name === canonicalName ? { ...character, ...normalizedUpdates } : character,
      ),
    }
    : {
      formatVersion: data.formatVersion,
      characters: [
        ...data.characters,
        {
          ...createEmptyBookCharacter(canonicalName),
          ...normalizedUpdates,
        } as Character,
      ],
    };

  bookCharacters.set(next);
  await persistBookCharacters();
}

export async function setBookCharacterVoice(name: string, voice: string | null): Promise<void> {
  await upsertBookCharacter(name, { voice });
}

export async function setBookCharacterColor(name: string, color: string | null): Promise<void> {
  await upsertBookCharacter(name, { color });
}

export async function setBookCharacterGender(name: string, gender: Gender): Promise<void> {
  await upsertBookCharacter(name, { gender: normalizeCharacterGender(gender) });
}

export async function setBookCharacterProvider(name: string, provider: TtsProvider | null): Promise<void> {
  await upsertBookCharacter(name, { provider });
}

export async function setBookCharacterVoiceId(name: string, voiceId: string | null): Promise<void> {
  await upsertBookCharacter(name, { voiceId });
}

export async function setBookCharacterVoiceMeta(name: string, voiceMeta: CharacterVoiceMeta | null): Promise<void> {
  await upsertBookCharacter(name, { voiceMeta });
}

export async function setBookCharacterManifestStats(name: string, manifestStats: CharacterManifestStats | null): Promise<void> {
  await upsertBookCharacter(name, { manifestStats });
}

interface RefreshManifestOptions {
  totalLines?: number;
  force?: boolean;
}

export async function refreshCharacterManifestStats(
  name: string,
  options?: RefreshManifestOptions,
): Promise<CharacterManifestStats | null> {
  const root = get(audioRoot);
  if (!root) return null;
  const summary = await loadCharacterManifestSummary(root, name, {
    totalLines: options?.totalLines,
    force: options?.force,
  });
  await setBookCharacterManifestStats(name, summary.stats);
  return summary.stats;
}

function getChapterRemapTargets() {
  return get(chapters).map((chapter: any) => ({
    title: chapter.title,
    scriptPath: chapter.scriptPath,
  }));
}

export async function renameBookCharacter(oldName: string, newName: string): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  const changed = await renameCentralCharacter(root, getChapterRemapTargets(), oldName, newName);
  if (changed) {
    await forceRefreshBookCharacters();
  }
}

export async function removeBookCharacter(name: string): Promise<void> {
  await removeBookCharacters([name]);
}

export async function removeBookCharacters(names: string[]): Promise<number> {
  const data = get(bookCharacters);
  const canonicalNames = new Set(
    names
      .map((name) => resolveCanonicalCharacterName(data, name) ?? name)
      .filter((name): name is string => typeof name === 'string' && name.length > 0),
  );
  if (canonicalNames.size === 0) return 0;

  const removedCharacters = data.characters.filter((character) => canonicalNames.has(character.name));
  if (removedCharacters.length === 0) return 0;

  const removedCharacterIds = removedCharacters
    .map((character) => character.id)
    .filter((characterId): characterId is string => typeof characterId === 'string' && characterId.length > 0);

  const next: CharactersJson = {
    formatVersion: data.formatVersion,
    characters: data.characters.filter((character) => !canonicalNames.has(character.name)),
  };

  bookCharacters.set(next);
  await persistBookCharacters();

  if (removedCharacterIds.length > 0) {
    await unassignVoicesFromCharacters(removedCharacterIds);
  }

  return removedCharacters.length;
}

export async function mergeBookCharacters(sourceName: string, targetName: string): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  const changed = await mergeCentralCharacters(root, getChapterRemapTargets(), sourceName, targetName);
  if (changed) {
    await forceRefreshBookCharacters();
  }
}

export async function setBookCharacterPrimaryName(currentName: string, newPrimaryName: string): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  const changed = await setCentralCharacterPrimaryName(root, currentName, newPrimaryName);
  if (changed) {
    await forceRefreshBookCharacters();
  }
}

export async function detachBookCharacterAlias(characterName: string, aliasName: string): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  const changed = await detachCentralCharacterAlias(root, getChapterRemapTargets(), characterName, aliasName);
  if (changed) {
    await forceRefreshBookCharacters();
  }
}

export async function addBookCharacterAlias(characterName: string, aliasName: string): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  const changed = await addCentralCharacterAlias(root, characterName, aliasName);
  if (changed) {
    await forceRefreshBookCharacters();
  }
}

/**
 * Synchronize all chapter-level character colors with book-level colors.
 * This ensures the book-level is the single source of truth.
 * Useful for migrating existing books to the centralized color system.
 */
export async function syncAllChapterColorsFromBook(): Promise<{ updated: number; errors: string[] }> {
  const root = get(bookRoot);
  if (!root) return { updated: 0, errors: ['No book root'] };

  const bookChars = get(bookCharacters);
  const bookColorMap = new Map(bookChars.characters.map(c => [c.name, c]));

  const chapterList = get(chapters);
  let updated = 0;
  const errors: string[] = [];

  for (const ch of chapterList) {
    try {
      const charsPath = getCharactersPath(root, ch.title);
      const chapterChars = await readCharacters(charsPath);

      if (chapterChars && chapterChars.characters) {
        // Update chapter characters with book-level colors
        const synced: CharactersJson = {
          formatVersion: chapterChars.formatVersion || '2.0',
          characters: chapterChars.characters.map(c => {
            const bookChar = bookColorMap.get(c.name);
            return {
              ...c,
              gender: normalizeCharacterGender(bookChar?.gender ?? c.gender ?? 'Unknown'),
              color: bookChar?.color ?? c.color ?? null,
              voice: bookChar?.voice ?? c.voice ?? null,
              provider: bookChar?.provider ?? c.provider ?? null,
              voiceId: bookChar?.voiceId ?? c.voiceId ?? null,
              voiceMeta: bookChar?.voiceMeta ?? c.voiceMeta ?? null,
            };
          })
        };

        await writeCharacters(charsPath, synced);
        updated++;
      }
    } catch (e) {
      errors.push(`${ch.title}: ${String(e)}`);
    }
  }

  return { updated, errors };
}

/**
 * Ensure all characters in the book have colors assigned.
 * Characters without colors will get hash-based default colors persisted.
 * This makes colors consistent across all chapters.
 */
export async function ensureAllCharactersHaveColors(): Promise<{ updated: number }> {
  const data = get(bookCharacters);
  let updated = 0;

  // Import hashToIndex and defaultColors at runtime to avoid circular deps
  const { hashToIndex } = await import('$lib/stores/characters');
  const { defaultColors } = await import('$lib/theme/colors');

  const next: CharactersJson = {
    formatVersion: data.formatVersion,
    characters: data.characters.map(c => {
      if (!c.color) {
        updated++;
        return { ...c, color: defaultColors[hashToIndex(c.name)] };
      }
      return c;
    })
  };

  if (updated > 0) {
    bookCharacters.set(next);
    await persistBookCharacters();
  }

  return { updated };
}

/**
 * Sync voice assignments from voices.json to populate character voice metadata
 * This reads the assignments from voices.json and updates character properties
 */
export async function syncAssignmentsFromVoicesJson(): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  try {
    // Import voices store functions
    const { voices: voicesStore, getCharacterAssignment, getAssignedVoice } = await import('$lib/stores/speakers');
    const voicesData = get(voicesStore);

    if (!voicesData || voicesData.assignments.length === 0) {
      console.log('[bookCharacters] No assignments to sync');
      return;
    }

    // Read the characters.json to get character IDs
    const charactersData = await readCentralCharacters(root);
    if (!charactersData) {
      console.log('[bookCharacters] No characters.json found');
      return;
    }

    const data = get(bookCharacters);
    let updated = false;

    // Create a map from character name to character ID (from characters.json)
    const nameToIdMap = new Map<string, string>();
    charactersData.characters.forEach((char: any) => {
      nameToIdMap.set(char.name, char.id);
    });

    const next: CharactersJson = {
      formatVersion: data.formatVersion,
      characters: data.characters.map(c => {
        const characterId = nameToIdMap.get(c.name);
        if (!characterId) return c;

        const assignment = getCharacterAssignment(characterId);
        if (!assignment) return c;

        const assignedVoice = getAssignedVoice(characterId);
        if (!assignedVoice) return c;

        updated = true;

        // Update character with voice metadata from assignment
        return {
          ...c,
          provider: assignedVoice.provider,
          voiceId: assignedVoice.providerVoiceId,
          voiceMeta: {
            name: assignedVoice.displayName,
            previewUrl: assignedVoice.previewUrl,
            fetchedAt: new Date().toISOString(),
            provider: assignedVoice.provider
          }
        };
      })
    };

    if (updated) {
      bookCharacters.set(next);
      console.log('[bookCharacters] Synced voice assignments from voices.json');
    }
  } catch (err) {
    console.error('[bookCharacters] Error syncing assignments:', err);
  }
}

/**
 * Get character ID from character name by reading characters.json
 */
async function getCharacterIdByName(name: string): Promise<string | null> {
  const root = get(bookRoot);
  if (!root) return null;

  try {
    const charactersData = await readCentralCharacters(root);
    if (!charactersData) return null;

    const char = charactersData.characters.find((c: any) => c.name === name);
    return char?.id || null;
  } catch (err) {
    console.error('[bookCharacters] Error getting character ID:', err);
    return null;
  }
}

/**
 * Update setBookCharacterProvider to also update voices.json assignments
 */
export async function setBookCharacterProviderAndAssign(
  name: string,
  provider: TtsProvider | null,
  voiceId: string | null
): Promise<void> {
  // Update character in bookCharacters
  await setBookCharacterProvider(name, provider);

  if (voiceId) {
    await setBookCharacterVoiceId(name, voiceId);
  }

  // Also update voices.json assignment
  if (provider && voiceId) {
    const characterId = await getCharacterIdByName(name);
    if (characterId) {
      const { assignVoiceToCharacter } = await import('$lib/stores/speakers');
      const { voices: voicesStore } = await import('$lib/stores/speakers');
      const voicesData = get(voicesStore);

      // Find the voice with matching provider and providerVoiceId
      const voice = voicesData.voices.find(v =>
        v.provider === provider && v.providerVoiceId === voiceId
      );

      if (voice) {
        await assignVoiceToCharacter(characterId, voice.id);
        console.log(`[bookCharacters] Created assignment: ${characterId} -> ${voice.id}`);
      }
    }
  }
}


