import { writable, get } from 'svelte/store';
import { chapters, currentChapter, currentScript } from '$lib/stores/bookState';
import type { ScriptJson } from '$lib/types';
import { readScriptForChapter } from '$lib/services/fs';

// Centralized chapter stats store keyed by chapter title
// Values: { numConflicts, numLines }
export const chapterStats = writable<Record<string, { numConflicts: number; numLines: number }>>({});
export const characterLineCounts = writable<Record<string, number>>({});

// When chapters list changes, (re)load stats for chapters that have a script on disk
let loadSeq = 0;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
function refreshFromDisk() {
  // Debounce: both `chapters` and `bookRoot` can fire in quick succession
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    const list = get(chapters);
    if (!Array.isArray(list) || list.length === 0) {
      chapterStats.set({});
      return;
    }
    const seq = ++loadSeq;
    const acc: Record<string, { numConflicts: number; numLines: number }> = {};
    for (const ch of list) {
      try {
        if (!ch || !ch.title || !ch.parsed) continue;
        const script = await readScriptForChapter(ch.title) as ScriptJson | null;
        if (seq !== loadSeq) return; // superseded by a newer load
        if (script && script.stats) acc[ch.title] = script.stats;
      } catch {
        // ignore
      }
    }
    if (seq === loadSeq) chapterStats.set(acc);
  }, 200);
}

// HMR-safe subscriptions: unsubscribe old listeners on module reload
let _unsubChapters = chapters.subscribe(() => { refreshFromDisk(); });

// Keep stats live with in-memory edits to currentScript (debounced)
let scriptUpdateTimer: ReturnType<typeof setTimeout> | null = null;
let _unsubScript = currentScript.subscribe((scr) => {
  if (!scr) return; // Don't process null (chapter clearing)
  if (scriptUpdateTimer) clearTimeout(scriptUpdateTimer);
  scriptUpdateTimer = setTimeout(() => {
    scriptUpdateTimer = null;
    const ch = get(currentChapter) as { title: string } | null;
    if (!scr || !ch) return;
    const numConflicts = scr.lines.filter(l => l.isConflict || !l.chosenSpeaker).length;
    const numLines = scr.lines.length;
    chapterStats.update((s) => ({ ...s, [ch.title]: { numConflicts, numLines } }));
    // character counts
    const counts: Record<string, number> = {};
    for (const l of scr.lines) {
      const name = (l.chosenSpeaker ?? '').trim();
      if (!name) continue;
      counts[name] = (counts[name] || 0) + 1;
    }
    characterLineCounts.set(counts);
  }, 50);
});

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    _unsubChapters();
    _unsubScript();
    if (refreshTimer) clearTimeout(refreshTimer);
    if (scriptUpdateTimer) clearTimeout(scriptUpdateTimer);
  });
}

