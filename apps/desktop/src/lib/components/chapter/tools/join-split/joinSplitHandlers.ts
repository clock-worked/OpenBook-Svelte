import { get, type Writable } from 'svelte/store';
import type { UnifiedLine } from '../types';
import { recomputeStats } from '../shared/toolUtils';

/**
 * Join lines in the specified direction
 */
export function joinLines(
  targetLineId: number,
  direction: 'left' | 'right' | 'both',
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  rebuildParagraphRuns: () => void,
  scheduleSave: () => void
): void {
  const scr = get(normalizedScript);
  if (!scr) return;

  const lineIdx = scr.lines.findIndex(l => l.id === targetLineId);
  if (lineIdx === -1) return;

  console.log('[JoinSplit] joinLines called:', { targetLineId, direction, lineIdx, totalLines: scr.lines.length });

  let linesToJoin: number[] = [];
  let newText = '';
  let newSpan: { start: number; end: number } | null = null;
  let primaryLine = scr.lines[lineIdx];
  
  if (direction === 'left' && lineIdx > 0) {
    const prevLine = scr.lines[lineIdx - 1];
    linesToJoin = [prevLine.id, primaryLine.id];
    newText = prevLine.text + ' ' + primaryLine.text;
    if (prevLine.span && primaryLine.span) {
      newSpan = { start: prevLine.span.start, end: primaryLine.span.end };
    }
    // Merge into the previous line
    prevLine.text = newText;
    prevLine.span = newSpan;
    // Keep the character assignment from the target line if it exists
    if (primaryLine.characterName && !prevLine.characterName) {
      prevLine.characterName = primaryLine.characterName;
    }
    // Merge candidates
    const candidateNames = new Set(prevLine.candidates.map(c => c.name));
    for (const cand of primaryLine.candidates) {
      if (!candidateNames.has(cand.name)) {
        prevLine.candidates.push(cand);
      }
    }
    prevLine.isConflict = prevLine.isConflict || primaryLine.isConflict;
    // Remove current line
    scr.lines.splice(lineIdx, 1);
  } else if (direction === 'right' && lineIdx < scr.lines.length - 1) {
    const nextLine = scr.lines[lineIdx + 1];
    linesToJoin = [primaryLine.id, nextLine.id];
    newText = primaryLine.text + ' ' + nextLine.text;
    if (primaryLine.span && nextLine.span) {
      newSpan = { start: primaryLine.span.start, end: nextLine.span.end };
    }
    // Merge into current line
    primaryLine.text = newText;
    primaryLine.span = newSpan;
    // Merge candidates
    const candidateNames = new Set(primaryLine.candidates.map(c => c.name));
    for (const cand of nextLine.candidates) {
      if (!candidateNames.has(cand.name)) {
        primaryLine.candidates.push(cand);
      }
    }
    primaryLine.isConflict = primaryLine.isConflict || nextLine.isConflict;
    // Remove next line
    scr.lines.splice(lineIdx + 1, 1);
  } else if (direction === 'both' && lineIdx > 0 && lineIdx < scr.lines.length - 1) {
    const prevLine = scr.lines[lineIdx - 1];
    const nextLine = scr.lines[lineIdx + 1];
    linesToJoin = [prevLine.id, primaryLine.id, nextLine.id];
    newText = prevLine.text + ' ' + primaryLine.text + ' ' + nextLine.text;
    if (prevLine.span && nextLine.span) {
      newSpan = { start: prevLine.span.start, end: nextLine.span.end };
    }
    // Merge into the previous line
    prevLine.text = newText;
    prevLine.span = newSpan;
    // Keep character from target if needed
    if (primaryLine.characterName && !prevLine.characterName) {
      prevLine.characterName = primaryLine.characterName;
    }
    // Merge all candidates
    const candidateNames = new Set(prevLine.candidates.map(c => c.name));
    for (const line of [primaryLine, nextLine]) {
      for (const cand of line.candidates) {
        if (!candidateNames.has(cand.name)) {
          prevLine.candidates.push(cand);
          candidateNames.add(cand.name);
        }
      }
    }
    prevLine.isConflict = prevLine.isConflict || primaryLine.isConflict || nextLine.isConflict;
    // Remove current and next lines
    scr.lines.splice(lineIdx, 2);
  }

  // Renumber all IDs starting from the affected section
  for (let i = 0; i < scr.lines.length; i++) {
    scr.lines[i].id = i;
  }

  console.log('[JoinSplit] Join complete:', { direction, newLineCount: scr.lines.length, mergedText: newText });

  recomputeStats(scr);
  
  // Create a fresh copy to ensure reactivity
  const updatedScript = {
    lines: scr.lines.map(l => ({ ...l })),
    stats: { ...scr.stats }
  };
  
  console.log('[JoinSplit] Setting normalizedScript with lines 13-17:', updatedScript.lines.slice(13, 18).map(l => ({ id: l.id, text: l.text.substring(0, 50) })));
  
  normalizedScript.set(updatedScript);
  
  // Verify the store was updated
  setTimeout(() => {
    const verify = get(normalizedScript);
    console.log('[JoinSplit] Verifying store after 100ms, lines 13-17:', verify?.lines.slice(13, 18).map(l => ({ id: l.id, text: l.text.substring(0, 50) })));
  }, 100);
  
  rebuildParagraphRuns();
  scheduleSave();
}

/**
 * Split a line at the specified position
 */
export function splitLine(
  lineId: number,
  position: number,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  rebuildParagraphRuns: () => void,
  scheduleSave: () => void
): void {
  const scr = get(normalizedScript);
  if (!scr) return;

  const lineIdx = scr.lines.findIndex(l => l.id === lineId);
  if (lineIdx === -1) return;

  const line = scr.lines[lineIdx];
  
  // Split the text
  const firstPart = line.text.substring(0, position).trim();
  const secondPart = line.text.substring(position).trim();
  
  if (!firstPart || !secondPart) {
    return; // Don't split if either part would be empty
  }

  // Update the original line with first part
  line.text = firstPart;
  
  // Create new line for second part
  const newLine: UnifiedLine = {
    id: lineIdx + 1, // Temporary ID, will be renumbered
    text: secondPart,
    span: null, // We'll need to recalculate spans or set to null
    characterName: line.characterName, // Inherit character assignment
    candidates: [...line.candidates], // Copy candidates
    isConflict: line.isConflict
  };

  // Update span if it exists
  if (line.span) {
    const midPoint = line.span.start + position;
    const originalEnd = line.span.end;
    line.span = { start: line.span.start, end: midPoint };
    newLine.span = { start: midPoint, end: originalEnd };
  }

  // Insert new line
  scr.lines.splice(lineIdx + 1, 0, newLine);

  // Renumber all IDs
  for (let i = 0; i < scr.lines.length; i++) {
    scr.lines[i].id = i;
  }

  recomputeStats(scr);
  normalizedScript.set({ ...scr, lines: [...scr.lines] });
  rebuildParagraphRuns();
  scheduleSave();
  
  // Clear selection
  window.getSelection()?.removeAllRanges();
}

/**
 * Handle text selection for split operation
 */
export function handleTextSelection(
  lineId: number,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  showSplitButton: (lineId: number, position: number, x: number, y: number) => void,
  hideSplitButton: () => void
): void {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    hideSplitButton();
    return;
  }

  const range = selection.getRangeAt(0);
  const selectedText = selection.toString();
  
  // Only show split button if text is selected and it's not the entire line
  if (selectedText.length > 0 && range.startOffset !== range.endOffset) {
    const scr = get(normalizedScript);
    const line = scr?.lines.find(l => l.id === lineId);
    
    if (line && range.startOffset > 0 && range.startOffset < line.text.length) {
      // Position the split button near the selection
      const rect = range.getBoundingClientRect();
      showSplitButton(lineId, range.startOffset, rect.left + rect.width / 2, rect.bottom + 8);
    }
  } else {
    hideSplitButton();
  }
}


