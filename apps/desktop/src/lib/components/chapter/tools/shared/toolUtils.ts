import type { UnifiedLine } from '../types';

/**
 * Format character name for display
 */
export function formatCharacterName(name: string | null | undefined): string {
  if (!name) return 'Unknown';
  if (name.toLowerCase() === 'narrator') return 'Narrator';
  // Capitalize first letter of each word
  return name.split(/\s+/).map(word => 
    word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
  ).join(' ');
}

/**
 * Recompute statistics for a script
 */
export function recomputeStats(scr: { lines: UnifiedLine[]; stats: any }): void {
  const numConflicts = scr.lines.filter(l => l.isConflict || !l.characterName).length;
  scr.stats = { numConflicts, numLines: scr.lines.length };
}

/**
 * Display chapter title with formatting
 */
export function displayTitle(title: string): string {
  // remove leading numeric prefix like "00-" or "12-"
  let result = title.replace(/^\d+\-/, '');
  
  // Convert "Chapter-7-Sword" to "Chapter 7: Sword"
  // Also handles "Prologue", "Epilogue", "Interlude-Title", etc.
  result = result.replace(/^(Chapter|Interlude|Extra)-(\d+)-(.+)/, '$1 $2: $3');
  
  // Handle cases like "Prologue-Title" or "Epilogue-Title" (no number)
  result = result.replace(/^(Prologue|Epilogue)-(.+)/, '$1: $2');
  
  // Replace remaining hyphens with spaces for better readability
  result = result.replace(/-/g, ' ');
  
  return result;
}


