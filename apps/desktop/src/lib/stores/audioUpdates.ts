import { writable } from 'svelte/store';

/**
 * Store to track audio updates (generation/deletion)
 * Increment this value to notify components that audio has changed
 */
export const audioUpdateTrigger = writable<number>(0);

/**
 * Trigger a reload of audio-related data across the app
 */
export function triggerAudioUpdate() {
  audioUpdateTrigger.update(n => n + 1);
}

