import { writable } from 'svelte/store';

export interface SelectionRange {
  startLineId: number | null;
  endLineId: number | null;
}

export type ToolMode = 'review' | 'join-split' | 'edit' | 'audio';

export const selection = writable<SelectionRange>({ startLineId: null, endLineId: null });
export const conflictCursor = writable<number | null>(null);
export const toolMode = writable<ToolMode>('review');
export const audioGenerateLineId = writable<number | null>(null);

/**
 * The line the user asked to inspect via Ctrl/⌘-click (review mode).
 * `seq` increments on every trace so re-tracing the same line re-fires
 * subscribers (e.g. the Trace tab switching back into view).
 */
export const tracedLine = writable<{ lineId: number; seq: number } | null>(null);


