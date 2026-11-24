// ============================================================================
// Character stats — the one answer (candidate 5, extends ADR 0012).
//
// "How many lines does this character have?" is answered by the backend's
// accumulated stats (ADR 0012), which it writes onto each character record
// (`stats.totalLines` / `stats.chapterCount`). Every UI badge, sort and
// count reads them through `characterStats` (or the two shorthands below);
// nothing re-derives book-wide stats from raw dialogue.
//
// The legacy `count` / `chapterCount` mirrors (v1/v2 records) are read only
// as a fallback when a record carries no `stats` (ADR 0003: read legacy,
// write current) — and only here.
//
// Where a per-chapter view is needed (the chapter character panel),
// `chapterLineCounts` counts one chapter's already-loaded dialogue lines.
//
// Dependency rule: import-light (types + characterIdentity, relative with
// explicit .ts) so it stays usable from fs-independent modules and tests.
// ============================================================================

import type { Character } from '../types.ts';
import { normalizeCharacterKey } from './characterIdentity.ts';

export interface CharacterStats {
  totalLines: number;
  chapterCount: number;
}

/** The record fields the answer reads (a full `Character` satisfies it). */
export type CharacterStatsSource = Pick<Partial<Character>, 'stats' | 'count' | 'chapterCount'>;

function nonNegativeCount(...candidates: unknown[]): number {
  for (const value of candidates) {
    if (typeof value === 'number' && Number.isFinite(value)) return Math.max(0, value);
  }
  return 0;
}

/** The character's book-wide stats: the backend's accumulated stats. */
export function characterStats(character: CharacterStatsSource | null | undefined): CharacterStats {
  if (!character) return { totalLines: 0, chapterCount: 0 };
  return {
    totalLines: nonNegativeCount(character.stats?.totalLines, character.count),
    chapterCount: nonNegativeCount(character.stats?.chapterCount, character.chapterCount),
  };
}

/** Book-wide line count (shorthand for `characterStats(c).totalLines`). */
export function characterLineCount(character: CharacterStatsSource | null | undefined): number {
  return characterStats(character).totalLines;
}

/** Number of chapters the character speaks in (shorthand). */
export function characterChapterCount(character: CharacterStatsSource | null | undefined): number {
  return characterStats(character).chapterCount;
}

/** The identity a line carries: a GUID in v3 dialogue, a name/alias in v1 scripts. */
export interface ChapterLineIdentity {
  characterId?: string | null;
  chosenSpeaker?: string | null;
}

/**
 * Per-character line counts for a single, already-loaded chapter.
 *
 * v3 dialogue lines carry a GUID in `characterId`; legacy v1-script chapters
 * fall back to `chosenSpeaker` (a display name/alias). The identity is resolved
 * to the character's canonical display name via the same precedence the roster
 * uses: GUID/id first, then alias, then name. The result is keyed by the
 * canonical display name, and each alias is also mapped to its canonical
 * count so name/alias-keyed badges agree.
 */
export function chapterLineCounts(
  scriptLines: ChapterLineIdentity[] | null | undefined,
  characters: Character[],
): Map<string, number> {
  const counts = new Map<string, number>();
  if (!scriptLines || scriptLines.length === 0) return counts;

  const idToCanonical = new Map<string, string>();
  const aliasToCanonical = new Map<string, string>();
  const nameToCanonical = new Map<string, string>();
  const canonicalToAliases = new Map<string, Set<string>>();

  for (const character of characters) {
    const canonicalKey = normalizeCharacterKey(character.name);
    if (canonicalKey) nameToCanonical.set(canonicalKey, character.name);

    const identityKey = normalizeCharacterKey(character.guid ?? character.id);
    if (identityKey) idToCanonical.set(identityKey, character.name);

    const aliases = Array.isArray(character.aliases) ? character.aliases : [];
    for (const alias of aliases) {
      const key = normalizeCharacterKey(alias);
      if (!key) continue;
      aliasToCanonical.set(key, character.name);
      if (!canonicalToAliases.has(character.name)) {
        canonicalToAliases.set(character.name, new Set());
      }
      canonicalToAliases.get(character.name)?.add(alias);
    }
  }

  for (const line of scriptLines) {
    // v3: characterId is a GUID; v1-script: fall back to the chosen speaker name.
    const rawIdentity = line.characterId ?? line.chosenSpeaker;
    if (!rawIdentity) continue;
    const key = normalizeCharacterKey(rawIdentity);
    const canonical =
      idToCanonical.get(key) || aliasToCanonical.get(key) || nameToCanonical.get(key) || rawIdentity;
    counts.set(canonical, (counts.get(canonical) || 0) + 1);
  }

  for (const [canonical, aliases] of canonicalToAliases.entries()) {
    const canonicalCount = counts.get(canonical) || 0;
    for (const alias of aliases) {
      counts.set(alias, canonicalCount);
    }
  }

  return counts;
}
