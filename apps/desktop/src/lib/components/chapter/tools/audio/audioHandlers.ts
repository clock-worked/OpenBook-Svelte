import { get, type Writable } from 'svelte/store';
import type { UnifiedLine } from '../types';
import { currentChapter, bookRoot } from '$lib/stores/bookState';
import { audioState, playLine } from '$lib/stores/audio';
import { voices } from '$lib/stores/speakers';
import { readCentralCharacters } from '$lib/services/fs';
import { buildNameToIdMap } from '$lib/stores/characters';
import {
  checkAudioExistsForLine,
  getAudioPath,
  generateAudioForLine,
  getAudioPathIfExists,
  getCharacterNameFromId,
  deleteAudioLineForCharacter,
} from '$lib/services/audio';
import { triggerAudioUpdate } from '$lib/stores/audioUpdates';

async function resolveCharacterIdFromName(characterName: string): Promise<string | null> {
  const root = get(bookRoot);
  if (!root) return null;

  try {
    const central = await readCentralCharacters(root);
    if (!central?.characters) return null;
    const byName = buildNameToIdMap(central.characters);
    return byName.get(characterName.toLowerCase()) || null;
  } catch {
    return null;
  }
}

/**
 * Handle audio click on a line - either play or show generate prompt
 */
export async function handleLineAudioClick(
  lineId: number,
  characterId: string,
  audioExistsCache: Map<string, boolean>,
  showGeneratePrompt: (lineId: number, characterName: string) => void,
  event?: MouseEvent
): Promise<void> {
  const ch = get(currentChapter);
  if (!ch) {
    console.log('[Audio] No current chapter');
    return;
  }

  console.log('[Audio] Audio click on line', lineId, 'characterId:', characterId);

  // Convert character ID to proper display name for audio file lookup
  const characterName = await getCharacterNameFromId(characterId);
  console.log('[Audio] Resolved character name:', characterName);

  const cacheKey = `${ch.title}-${characterName}-${lineId}`;

  // Check cache first
  let audioExists = audioExistsCache.get(cacheKey);
  if (audioExists === undefined) {
    console.log('[Audio] Checking if audio exists...');
    audioExists = await checkAudioExistsForLine(ch.title, characterName, lineId);
    audioExistsCache.set(cacheKey, audioExists);
    console.log('[Audio] Audio exists:', audioExists);
  }

  if (audioExists) {
    // Play the audio
    const audioPath = getAudioPath(ch.title, characterName, lineId, true);
    console.log('[Audio] Playing audio from:', audioPath);
    await playLine(lineId, characterName, ch.title, audioPath, async (lineId, charId) => {
      const charName = await getCharacterNameFromId(charId);
      return getAudioPathIfExists(ch.title, charName, lineId).then(path => {
        if (!path) return null;
        return getAudioPath(ch.title, charName, lineId, true);
      });
    });
  } else {
    // Show generate dropdown
    console.log('[Audio] No audio exists - showing generate prompt');
    showGeneratePrompt(lineId, characterName);
  }
}

/**
 * Handle audio context menu (right-click)
 */
export async function handleLineAudioContextMenu(
  lineId: number,
  characterId: string,
  audioExistsCache: Map<string, boolean>,
  showContextMenu: (lineId: number, characterName: string) => boolean
): Promise<void> {
  const ch = get(currentChapter);
  if (!ch) return;

  // Convert character ID to proper display name
  const characterName = await getCharacterNameFromId(characterId);

  // Check if audio exists for this line
  const cacheKey = `${ch.title}-${characterName}-${lineId}`;
  let audioExists = audioExistsCache.get(cacheKey);
  if (audioExists === undefined) {
    audioExists = await checkAudioExistsForLine(ch.title, characterName, lineId);
    audioExistsCache.set(cacheKey, audioExists);
  }

  // Only show context menu if audio exists
  if (audioExists) {
    showContextMenu(lineId, characterName);
  }
}

/**
 * Generate audio for a line
 */
export async function handleGenerateLineAudio(
  lineId: number,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  audioExistsCache: Map<string, boolean>,
  setGenerating: (lineId: number | null) => void
): Promise<void> {
  const ch = get(currentChapter);
  if (!ch) {
    console.error('[Audio] No current chapter');
    return;
  }
  const scr = get(normalizedScript);
  if (!ch || !scr) return;

  const line = scr.lines.find(l => l.id === lineId);
  if (!line || !line.characterName) return;

  // Resolve display name -> canonical character ID for voice assignment lookup
  const resolvedCharacterId = await resolveCharacterIdFromName(line.characterName);
  const characterId = resolvedCharacterId || line.characterName.toLowerCase();

  // Get voice assignment
  const assignment = get(voices).assignments.find(a => a.characterId === characterId);
  if (!assignment) {
    alert(`No voice assigned to ${line.characterName}`);
    return;
  }

  const voice = get(voices).voices.find(v => v.id === assignment.voiceId);
  if (!voice) {
    alert(`Voice not found for ${line.characterName}`);
    return;
  }

  setGenerating(lineId);

  const result = await generateAudioForLine(
    lineId,
    line.text,
    line.characterName,
    characterId,
    voice.providerVoiceId,
    voice.provider,
    ch.title,
    ch.path || ''
  );

  setGenerating(null);

  if (result.success) {
    // Update cache
    const cacheKey = `${ch.title}-${line.characterName}-${lineId}`;
    audioExistsCache.set(cacheKey, true);

    // Trigger audio update for AudioPanel
    triggerAudioUpdate();

    // Auto-play the generated audio with cache-busting to ensure fresh audio
    if (result.audioPath) {
      // Use cache-busting to force browser to load the new audio
      const freshAudioPath = getAudioPath(ch.title, line.characterName, lineId, true);
      await playLine(lineId, line.characterName, ch.title, freshAudioPath, async (lineId, charName) => {
        const charName2 = await getCharacterNameFromId(charName);
        // Use cache-busting for freshly generated audio
        return getAudioPath(ch.title, charName2, lineId, true);
      });
    }
  } else {
    alert(`Failed to generate audio: ${result.error}`);
  }
}

/**
 * Delete audio for a line
 */
export async function deleteAudioLine(
  lineId: number,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  audioExistsCache: Map<string, boolean>
): Promise<void> {
  const ch = get(currentChapter);
  const scr = get(normalizedScript);
  if (!ch || !scr) return;

  const line = scr.lines.find(l => l.id === lineId);
  if (!line || !line.characterName) return;

  try {
    const deleted = await deleteAudioLineForCharacter(ch.title, line.characterName, lineId);

    if (deleted) {
      // Update cache
      const cacheKey = `${ch.title}-${line.characterName}-${lineId}`;
      audioExistsCache.set(cacheKey, false);

      // Trigger audio update for AudioPanel
      triggerAudioUpdate();

      console.log('[Audio] Audio deleted successfully');
    } else {
      alert('Failed to delete audio: Unknown error');
    }
  } catch (error) {
    console.error('[Audio] Error deleting audio:', error);
    alert(`Failed to delete audio: ${error instanceof Error ? error.message : 'Unknown error'}`);
  }
}

/**
 * Regenerate audio for a line (delete then generate)
 */
export async function regenerateAudioLine(
  lineId: number,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  audioExistsCache: Map<string, boolean>,
  setGenerating: (lineId: number | null) => void
): Promise<void> {
  // Delete existing audio first
  await deleteAudioLine(lineId, normalizedScript, audioExistsCache);

  // Generate new audio
  await handleGenerateLineAudio(lineId, normalizedScript, audioExistsCache, setGenerating);
}

/**
 * Seek within playing audio
 */
export async function handleLineSeek(lineId: number, event: MouseEvent): Promise<void> {
  const state = get(audioState);
  if (state.currentLineId !== lineId) return;

  const target = event.currentTarget as HTMLElement;
  const rect = target.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const percentage = x / rect.width;

  // Import seek function from audio store
  const { seek } = await import('$lib/stores/audio');
  seek(percentage * state.duration);
}

/**
 * Get audio progress percentage for a line
 */
export function getAudioProgress(lineId: number): number {
  const state = get(audioState);
  if (state.currentLineId !== lineId) return 0;
  if (state.duration === 0) return 0;
  return (state.currentTime / state.duration) * 100;
}


