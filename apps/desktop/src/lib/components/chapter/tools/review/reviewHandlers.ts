import { get, type Writable } from 'svelte/store';
import type { UnifiedLine, MenuContext } from '../types';
import { characters } from '$lib/stores/characters';
import { currentChapter, bookRoot } from '$lib/stores/bookState';
import { getCharactersPath, writeCharacters } from '$lib/services/fs';
import { bookCharacters } from '$lib/stores/bookCharacters';
import type { Character } from '$lib/types';
import { recomputeStats } from '../shared/toolUtils';

/**
 * Get character options for a line, ordered by relevance
 */
export function getCharacterOptionsForLine(
  lineId: number,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>
): { value: string; label: string }[] {
  const scr = get(normalizedScript);
  const chars = get(characters);
  const options = new Set<string>();

  if (scr) {
    const firstLine = scr.lines.find(l => l.id === lineId);
    let topTwo: string[] = [];

    if (firstLine) {
      const cands = (firstLine.candidates || []).map(c => c?.name).filter((n): n is string => !!n);
      topTwo = Array.from(new Set(cands.slice(0, 2)));
      for (const c of cands) if (c) options.add(c);
      if (firstLine.characterName) options.add(firstLine.characterName);
    }

    for (const ch of (chars?.characters ?? [])) if (ch && ch.name) options.add(ch.name);

    const namesAll = Array.from(options);
    const narratorEntry = namesAll.find(n => n.toLowerCase() === 'narrator');
    const rest = namesAll.filter(n => !topTwo.includes(n) && n.toLowerCase() !== 'narrator').sort((a, b) => a.localeCompare(b));
    const ordered = [...topTwo, ...(narratorEntry && !topTwo.includes(narratorEntry) ? [narratorEntry] : []), ...rest];

    return ordered.map(n => ({ value: n, label: n }));
  }

  return [];
}

/**
 * Get character options for menu context (line, paragraph, or selection)
 */
export function getCharacterOptionsForContext(
  context: MenuContext,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>
): { value: string; label: string }[] {
  const scr = get(normalizedScript);
  const chars = get(characters);
  const allNames = new Set<string>();

  if (!scr) return [];

  const firstId = context.lineIds[0];
  const firstLine = scr.lines.find(l => l.id === firstId);
  let topTwo: string[] = [];

  if (firstLine) {
    const cands = (firstLine.candidates || []).map(c => c?.name).filter((n): n is string => !!n);
    topTwo = Array.from(new Set(cands.slice(0, 2)));
    for (const c of cands) allNames.add(c);
    if (firstLine.characterName) allNames.add(firstLine.characterName);
  }

  for (const ch of (chars?.characters ?? [])) if (ch && ch.name) allNames.add(ch.name);

  const namesAll = Array.from(allNames);
  const narratorEntry = namesAll.find(n => n.toLowerCase() === 'narrator');
  const rest = namesAll.filter(n => !topTwo.includes(n) && n.toLowerCase() !== 'narrator').sort((a, b) => a.localeCompare(b));
  const ordered = [...topTwo, ...(narratorEntry && !topTwo.includes(narratorEntry) ? [narratorEntry] : []), ...rest];

  return ordered.map(n => ({ value: n, label: n }));
}

/**
 * Sync characters.json with actual usage in the script
 */
export function syncCharactersWithUsage(
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>
): void {
  const scr = get(normalizedScript);
  const ch: any = get(currentChapter);
  const root = get(bookRoot);

  if (!scr || !ch || !root) return;

  const used = new Set<string>();
  for (const line of scr.lines) {
    if (line.characterName && line.characterName.trim().length) used.add(line.characterName);
    if (line.isConflict && Array.isArray(line.candidates)) {
      for (const c of line.candidates) if (c && typeof c.name === 'string' && c.name.trim().length) used.add(c.name);
    }
  }

  // Pull from book-level first (single source of truth), then chapter-level
  const bookChars = get(bookCharacters);
  const bookByName = new Map((bookChars?.characters ?? []).map(c => [c.name, c] as const));
  const prev = get(characters);
  const byName = new Map((prev?.characters ?? []).map(c => [c.name, c] as const));

  const updated = {
    formatVersion: prev?.formatVersion || '2.0',
    characters: Array.from(used).map(name => {
      const bookChar = bookByName.get(name);
      const chapterChar = byName.get(name);
      return {
        name,
        gender: bookChar?.gender ?? chapterChar?.gender ?? 'Unknown',
        color: bookChar?.color ?? chapterChar?.color ?? null,
        voice: bookChar?.voice ?? chapterChar?.voice ?? null,
        provider: bookChar?.provider ?? chapterChar?.provider ?? null,
        voiceId: bookChar?.voiceId ?? chapterChar?.voiceId ?? null,
        voiceMeta: bookChar?.voiceMeta ?? chapterChar?.voiceMeta ?? null,
      } as Character;
    })
  };

  const path = getCharactersPath(root, ch.title);
  writeCharacters(path, updated);
  characters.set(updated);
}

/**
 * Assign a character to multiple lines
 */
export function assignCharacterToLines(
  lineIds: number[],
  characterName: string,
  normalizedScript: Writable<{ lines: UnifiedLine[]; stats: any } | null>,
  rebuildParagraphRuns: () => void,
  scheduleSave: () => void
): void {
  const scr = get(normalizedScript);
  if (!scr) return;

  const byId = new Set(lineIds);
  for (const line of scr.lines) {
    if (!byId.has(line.id)) continue;
    line.characterName = characterName;
    if (!line.candidates.some(c => c.name === characterName)) {
      line.candidates.push({ name: characterName, confidence: 1 });
    }
    const attributionCandidatesByName = new Map(
      (line.attribution?.candidates || []).map((candidate) => [candidate.name, candidate] as const)
    );
    const topConfidence = Math.max(
      0,
      ...line.candidates.map((candidate) => Number.isFinite(candidate.confidence) ? candidate.confidence : 0)
    );
    line.attribution = {
      confidence: 1,
      topCandidateConfidence: Math.max(topConfidence, 1),
      marginToSecond: 1,
      misattributionRisk: 0,
      resolutionStatus: 'user_confirmed',
      thresholdUsed: line.attribution?.thresholdUsed ?? 0.62,
      sourceAlias: line.attribution?.sourceAlias ?? null,
      sourceCandidates: line.attribution?.sourceCandidates ?? [],
      sourceDescriptors: line.attribution?.sourceDescriptors ?? [],
      contextGender: line.attribution?.contextGender ?? null,
      contextGenderCue: line.attribution?.contextGenderCue ?? null,
      genderConflict: line.attribution?.genderConflict ?? false,
      parserBackend: line.attribution?.parserBackend ?? null,
      decisionTrace: line.attribution?.decisionTrace ?? null,
      candidates: line.candidates.map((candidate) => ({
        name: candidate.name,
        characterId: attributionCandidatesByName.get(candidate.name)?.characterId ?? null,
        confidence: candidate.name === characterName ? 1 : candidate.confidence,
        reasons: attributionCandidatesByName.get(candidate.name)?.reasons,
      })),
    };
    line.isConflict = false;
  }

  recomputeStats(scr);
  normalizedScript.set({ ...scr, lines: [...scr.lines] });
  rebuildParagraphRuns();
  scheduleSave();

  // Ensure the characters list includes the assigned character so the panel updates immediately
  try {
    const chars = get(characters);
    const exists = (chars?.characters ?? []).some(c => c.name === characterName);
    if (!exists) {
      const ch: any = get(currentChapter);
      const root = get(bookRoot);
      if (ch && root) {
        // Pull color from book-level (single source of truth)
        const bookChars = get(bookCharacters);
        const bookChar = bookChars?.characters?.find(c => c.name === characterName);
        const updated = {
          formatVersion: chars?.formatVersion || '2.0',
          characters: [...(chars?.characters ?? []), {
            name: characterName,
            gender: bookChar?.gender ?? 'Unknown',
            color: bookChar?.color ?? null,
            voice: bookChar?.voice ?? null,
            provider: bookChar?.provider ?? null,
            voiceId: bookChar?.voiceId ?? null,
            voiceMeta: bookChar?.voiceMeta ?? null,
          } as Character]
        };
        const path = getCharactersPath(root, ch.title);
        writeCharacters(path, updated);
        characters.set(updated);
      }
    }
  } catch { }

  // Prune any characters no longer used in assignments or unresolved conflicts
  syncCharactersWithUsage(normalizedScript);
}


