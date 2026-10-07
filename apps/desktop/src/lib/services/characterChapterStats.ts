import type { Character } from '$lib/types';

/**
 * Pure chapter-statistics helpers (Lane 7). No store/fs imports: the panel
 * feeds it already-loaded data so the same derivation powers both the list
 * line-count badges and the CharacterDetails card stats (never copied twice).
 */

function normalizeCharacterKey(name: string): string {
  return String(name ?? '').trim().toLowerCase();
}

/** The identity a line carries: a GUID in v3 dialogue, a name/alias in v1 scripts. */
export interface ChapterLineIdentity {
  characterId?: string | null;
  chosenSpeaker?: string | null;
}

/**
 * Compute per-character line counts for a single chapter.
 *
 * v3 dialogue lines carry a GUID in `characterId`; legacy v1-script chapters
 * fall back to `chosenSpeaker` (a display name/alias). The identity is resolved
 * to the character's canonical display name via the same precedence the roster
 * uses: `idToCanonical` (GUID → name) first, then alias, then name. The result
 * is keyed by the canonical display name, and each alias is also mapped to its
 * canonical count so name/alias-keyed badges agree.
 */
export function computeChapterLineCounts(
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
