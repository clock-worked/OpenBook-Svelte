import { writable, derived, get } from 'svelte/store';
import { currentScript } from './bookState';
import { readManifest, getCharacterNameFromId, type AudioManifest } from '$lib/services/audio';

export interface AudioState {
  currentLineId: number | null;
  currentCharacterName: string | null;
  currentChapterTitle: string | null;
  isPlaying: boolean;
  isPaused: boolean;
  currentTime: number;
  duration: number;
  totalDuration: number | null; // Total duration of all clips if all exist
  audioElement: HTMLAudioElement | null;
  getAudioPath: ((lineId: number, characterName: string) => string | null | Promise<string | null>) | null;
}

const initialState: AudioState = {
  currentLineId: null,
  currentCharacterName: null,
  currentChapterTitle: null,
  isPlaying: false,
  isPaused: false,
  currentTime: 0,
  duration: 0,
  totalDuration: null,
  audioElement: null,
  getAudioPath: null,
};

export const audioState = writable<AudioState>(initialState);

// Derived store to check if audio playback bar should be visible
export const isAudioActive = derived(
  audioState,
  ($audioState) => $audioState.isPlaying || $audioState.isPaused
);

let timeUpdateInterval: number | null = null;

/**
 * Auto-advance to next dialogue when current one ends
 */
async function autoAdvanceToNext(
  getAudioPath: (lineId: number, characterName: string) => string | null | Promise<string | null>
): Promise<void> {
  const state = get(audioState);
  const script = get(currentScript);
  
  if (!state.currentLineId || !script || !state.currentChapterTitle) {
    // Stop playback if no current line
    audioState.update(s => ({
      ...s,
      isPlaying: false,
      isPaused: false,
      currentTime: 0,
    }));
    if (timeUpdateInterval) {
      clearInterval(timeUpdateInterval);
      timeUpdateInterval = null;
    }
    return;
  }

  const currentIndex = script.lines.findIndex(l => l.id === state.currentLineId);
  if (currentIndex === -1) {
    audioState.update(s => ({
      ...s,
      isPlaying: false,
      isPaused: false,
      currentTime: 0,
    }));
    if (timeUpdateInterval) {
      clearInterval(timeUpdateInterval);
      timeUpdateInterval = null;
    }
    return;
  }

  // Find next line with audio
  for (let i = currentIndex + 1; i < script.lines.length; i++) {
    const line = script.lines[i];
    if (line.chosenSpeaker) {
      // Convert character ID to proper display name
      const characterName = await getCharacterNameFromId(line.chosenSpeaker);
      console.log(`[audio] Checking next line ${line.id}: chosenSpeaker="${line.chosenSpeaker}" -> characterName="${characterName}"`);
      const audioPath = await getAudioPathAsync(line.id, characterName, getAudioPath);
      console.log(`[audio] Audio path for line ${line.id}:`, audioPath);
      if (audioPath) {
        await playLine(line.id, characterName, state.currentChapterTitle, audioPath, getAudioPath);
        return;
      }
    }
  }

  // No next clip found - pause
  console.log('[audio] No next clip found, pausing');
  audioState.update(s => ({
    ...s,
    isPlaying: false,
    isPaused: false,
    currentTime: 0,
  }));
  if (timeUpdateInterval) {
    clearInterval(timeUpdateInterval);
    timeUpdateInterval = null;
  }
}

/**
 * Helper to handle async getAudioPath
 */
async function getAudioPathAsync(
  lineId: number,
  characterName: string,
  getAudioPath: (lineId: number, characterName: string) => string | null | Promise<string | null>
): Promise<string | null> {
  const result = getAudioPath(lineId, characterName);
  if (result && typeof result === 'object' && 'then' in result) {
    return await result;
  }
  return result || null;
}

/**
 * Calculate total duration of all dialogue clips in chapter (only if all clips exist)
 */
export async function calculateTotalDuration(
  chapterTitle: string,
  script: { lines: Array<{ id: number; chosenSpeaker: string | null }> }
): Promise<number | null> {
  const manifests = new Map<string, AudioManifest>();
  let totalDuration = 0;

  // Collect all unique characters (convert IDs to names)
  const characters = new Set<string>();
  for (const line of script.lines) {
    if (line.chosenSpeaker) {
      const characterName = await getCharacterNameFromId(line.chosenSpeaker);
      characters.add(characterName);
    }
  }

  // Load all manifests
  for (const charName of characters) {
    const manifest = await readManifest(chapterTitle, charName);
    if (!manifest) {
      // Not all clips exist
      return null;
    }
    manifests.set(charName, manifest);
  }

  // Check if all clips exist and sum durations
  for (const line of script.lines) {
    if (line.chosenSpeaker) {
      const characterName = await getCharacterNameFromId(line.chosenSpeaker);
      const manifest = manifests.get(characterName);
      if (!manifest) {
        return null;
      }

      const clip = manifest.clips.find(c => c.id === line.id && c.chapter === chapterTitle);
      if (!clip || !clip.metadata?.duration) {
        // Clip doesn't exist or has no duration
        return null;
      }

      totalDuration += clip.metadata.duration;
    }
  }

  return totalDuration;
}

/**
 * Play audio for a specific dialogue line
 */
export async function playLine(
  lineId: number,
  characterName: string,
  chapterTitle: string,
  audioPath: string,
  getAudioPath?: (lineId: number, characterName: string) => string | null | Promise<string | null>
): Promise<void> {
  console.log('[audio] playLine called:', { lineId, characterName, chapterTitle, audioPath });
  
  const state = get(audioState);
  
  // Stop current playback if any
  if (state.audioElement) {
    console.log('[audio] Stopping previous playback');
    state.audioElement.pause();
    // Remove all event listeners before clearing src to prevent error events
    const oldAudio = state.audioElement;
    oldAudio.onloadedmetadata = null;
    oldAudio.onended = null;
    oldAudio.onerror = null;
    oldAudio.src = '';
    oldAudio.load(); // Force clear
    if (timeUpdateInterval) {
      clearInterval(timeUpdateInterval);
      timeUpdateInterval = null;
    }
  }

  // Create new audio element
  const audio = new Audio(audioPath);
  console.log('[audio] Created audio element with src:', audioPath);
  
  // Set up event listeners
  audio.addEventListener('loadedmetadata', () => {
    console.log('[audio] Metadata loaded, duration:', audio.duration);
    audioState.update(s => ({ ...s, duration: audio.duration }));
  });

  audio.addEventListener('ended', () => {
    console.log('[audio] Playback ended');
    const state = get(audioState);
    
    // Auto-advance to next dialogue
    if (state.getAudioPath) {
      autoAdvanceToNext(state.getAudioPath);
    } else {
      audioState.update(s => ({
        ...s,
        isPlaying: false,
        isPaused: false,
        currentTime: 0,
      }));
      if (timeUpdateInterval) {
        clearInterval(timeUpdateInterval);
        timeUpdateInterval = null;
      }
    }
  });

  audio.addEventListener('error', (e) => {
    console.error('[audio] Playback error:', e, audio.error);
    audioState.update(s => ({
      ...s,
      isPlaying: false,
      isPaused: false,
      audioElement: null,
    }));
    if (timeUpdateInterval) {
      clearInterval(timeUpdateInterval);
      timeUpdateInterval = null;
    }
  });

  // Calculate total duration if not already set
  let totalDuration = get(audioState).totalDuration;
  if (totalDuration === null) {
    const script = get(currentScript);
    if (script) {
      totalDuration = await calculateTotalDuration(chapterTitle, script);
    }
  }

  // Update state - use audio.duration if already loaded
  audioState.set({
    currentLineId: lineId,
    currentCharacterName: characterName,
    currentChapterTitle: chapterTitle,
    isPlaying: true,
    isPaused: false,
    currentTime: 0,
    duration: audio.duration || 0, // Use the audio element's duration if available
    totalDuration: totalDuration,
    audioElement: audio,
    getAudioPath: getAudioPath || null,
  });
  
  console.log('[audio] State updated, isPlaying=true');

  // Start time update interval
  timeUpdateInterval = setInterval(() => {
    const state = get(audioState);
    if (state.audioElement && state.isPlaying) {
      // Also update duration if it wasn't available initially
      audioState.update(s => ({ 
        ...s, 
        currentTime: audio.currentTime,
        duration: s.duration || audio.duration || 0
      }));
    }
  }, 100) as any;

  // Play the audio
  try {
    console.log('[audio] Starting playback...');
    await audio.play();
    console.log('[audio] Playback started successfully');
  } catch (error) {
    console.error('[audio] Failed to play audio:', error);
    audioState.update(s => ({
      ...s,
      isPlaying: false,
      audioElement: null,
    }));
    if (timeUpdateInterval) {
      clearInterval(timeUpdateInterval);
      timeUpdateInterval = null;
    }
  }
}

/**
 * Pause current playback
 */
export function pause(): void {
  const state = get(audioState);
  if (state.audioElement && state.isPlaying) {
    state.audioElement.pause();
    audioState.update(s => ({ ...s, isPlaying: false, isPaused: true }));
  }
}

/**
 * Resume paused playback
 */
export function resume(): void {
  const state = get(audioState);
  if (state.audioElement && state.isPaused) {
    state.audioElement.play();
    audioState.update(s => ({ ...s, isPlaying: true, isPaused: false }));
  }
}

/**
 * Seek to a specific time position
 */
export function seek(timeInSeconds: number): void {
  const state = get(audioState);
  if (state.audioElement) {
    state.audioElement.currentTime = timeInSeconds;
    audioState.update(s => ({ ...s, currentTime: timeInSeconds }));
  }
}

/**
 * Stop current playback and reset state
 */
export function stop(): void {
  const state = get(audioState);
  if (state.audioElement) {
    state.audioElement.pause();
    state.audioElement.src = '';
  }
  if (timeUpdateInterval) {
    clearInterval(timeUpdateInterval);
    timeUpdateInterval = null;
  }
  audioState.set(initialState);
}

/**
 * Jump to next line with audio
 */
export async function next(
  getAudioPath: (lineId: number, characterName: string) => string | null | Promise<string | null>
): Promise<void> {
  const state = get(audioState);
  const script = get(currentScript);
  
  if (!state.currentLineId || !script || !state.currentChapterTitle) return;

  const currentIndex = script.lines.findIndex(l => l.id === state.currentLineId);
  if (currentIndex === -1) return;

  // Find next line with audio
  for (let i = currentIndex + 1; i < script.lines.length; i++) {
    const line = script.lines[i];
    if (line.chosenSpeaker) {
      const characterName = await getCharacterNameFromId(line.chosenSpeaker);
      const audioPath = await getAudioPathAsync(line.id, characterName, getAudioPath);
      if (audioPath) {
        await playLine(line.id, characterName, state.currentChapterTitle, audioPath, getAudioPath);
        return;
      }
    }
  }
}

/**
 * Jump to previous line with audio
 */
export async function previous(
  getAudioPath: (lineId: number, characterName: string) => string | null | Promise<string | null>
): Promise<void> {
  const state = get(audioState);
  const script = get(currentScript);
  
  if (!state.currentLineId || !script || !state.currentChapterTitle) return;

  const currentIndex = script.lines.findIndex(l => l.id === state.currentLineId);
  if (currentIndex === -1) return;

  // Find previous line with audio
  for (let i = currentIndex - 1; i >= 0; i--) {
    const line = script.lines[i];
    if (line.chosenSpeaker) {
      const characterName = await getCharacterNameFromId(line.chosenSpeaker);
      const audioPath = await getAudioPathAsync(line.id, characterName, getAudioPath);
      if (audioPath) {
        await playLine(line.id, characterName, state.currentChapterTitle, audioPath, getAudioPath);
        return;
      }
    }
  }
}

/**
 * Jump to next line with a different speaker
 */
export async function nextSpeaker(
  getAudioPath: (lineId: number, characterName: string) => string | null | Promise<string | null>
): Promise<void> {
  const state = get(audioState);
  const script = get(currentScript);
  
  if (!state.currentLineId || !script || !state.currentChapterTitle || !state.currentCharacterName) return;

  const currentIndex = script.lines.findIndex(l => l.id === state.currentLineId);
  if (currentIndex === -1) return;

  // Find next line with a different speaker and audio
  for (let i = currentIndex + 1; i < script.lines.length; i++) {
    const line = script.lines[i];
    if (line.chosenSpeaker && line.chosenSpeaker !== state.currentCharacterName) {
      const characterName = await getCharacterNameFromId(line.chosenSpeaker);
      const audioPath = await getAudioPathAsync(line.id, characterName, getAudioPath);
      if (audioPath) {
        await playLine(line.id, characterName, state.currentChapterTitle, audioPath, getAudioPath);
        return;
      }
    }
  }
}

/**
 * Jump to previous line with a different speaker
 */
export async function previousSpeaker(
  getAudioPath: (lineId: number, characterName: string) => string | null | Promise<string | null>
): Promise<void> {
  const state = get(audioState);
  const script = get(currentScript);
  
  if (!state.currentLineId || !script || !state.currentChapterTitle || !state.currentCharacterName) return;

  const currentIndex = script.lines.findIndex(l => l.id === state.currentLineId);
  if (currentIndex === -1) return;

  // Find previous line with a different speaker and audio
  for (let i = currentIndex - 1; i >= 0; i--) {
    const line = script.lines[i];
    if (line.chosenSpeaker && line.chosenSpeaker !== state.currentCharacterName) {
      const characterName = await getCharacterNameFromId(line.chosenSpeaker);
      const audioPath = await getAudioPathAsync(line.id, characterName, getAudioPath);
      if (audioPath) {
        await playLine(line.id, characterName, state.currentChapterTitle, audioPath, getAudioPath);
        return;
      }
    }
  }
}

/**
 * Format time in MM:SS format
 */
export function formatTime(seconds: number): string {
  if (!isFinite(seconds)) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

