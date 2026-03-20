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


