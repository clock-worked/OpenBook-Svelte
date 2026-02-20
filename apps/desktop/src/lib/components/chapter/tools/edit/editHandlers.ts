import { get, type Writable } from 'svelte/store';
import type { UnifiedLine } from '../types';

/**
 * Start editing a line
 */
export function startEditLine(
  lineId: number,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  setEditState: (lineId: number, text: string) => void
): void {
  const scr = get(normalizedScript);
  if (!scr) return;
  
  const line = scr.lines.find(l => l.id === lineId);
  if (!line) return;
  
  setEditState(lineId, line.text);
}

/**
 * Save edited line
 */
export function saveEditedLine(
  lineId: number,
  editedText: string,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  rebuildParagraphRuns: () => void,
  scheduleSave: () => void
): void {
  const scr = get(normalizedScript);
  if (!scr) return;
  
  const lineIdx = scr.lines.findIndex(l => l.id === lineId);
  if (lineIdx === -1) return;
  
  // Update the line text and set span to null (text no longer matches original file position)
  const updatedLines = [...scr.lines];
  updatedLines[lineIdx] = { ...updatedLines[lineIdx], text: editedText, span: null };
  
  normalizedScript.set({ ...scr, lines: updatedLines });
  rebuildParagraphRuns();
  scheduleSave();
}


