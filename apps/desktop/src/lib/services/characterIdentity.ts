// ============================================================================
// Canonical character identity primitives.
//
// Single home for the identity fragments that drifted across the service
// layer (each previously had 2-6 hand-rolled copies):
//   - CROCKFORD_BASE32_ALPHABET      (was: 2 copies)
//   - WINDOWS_RESERVED_FILE_NAMES    (was: 3 copies)
//   - normalizeCharacterKey          (was: 6 copies)
//   - sanitizeCharacterFileStem      (was: 2 divergent copies)
//   - ALWAYS_BLOCKED_CHARACTER_NAMES (speaker blocklist word set)
//   - buildUniqueCharacterId         (ID-collision suffixing — one scheme)
//   - isCharacterGuid                (v3 GUID validation)
//
// Cross-language parity: `sanitizeCharacterFileStem`, `normalizeCharacterKey`
// and `isCharacterGuid` are pinned against py_services/character_store.py by
// the shared vector file tests/character_identity_vectors.json — asserted on
// the TS side by __tests__/characterIdentity.test.ts (vitest) and on the
// Python side by py_services/test_character_identity_parity.py (unittest).
//
// Dependency rule: this module stays import-light (only ../types, relative) so
// fs-independent modules can use it without pulling in the fs import chain
// (same rationale as characterFileMapping.ts).
// ============================================================================

// Relative import (not $lib/types): this module is part of the node:test
// import graph (closedWorldCharacterWorkflow.test.ts / parserCatalog.test.ts
// run under plain `node --test`, which cannot resolve the $lib alias).
// Explicit .ts extension matches the convention in this import graph
// (closedWorldCharacterWorkflow.ts / parserCatalog.ts).
import { CHARACTER_GUID_PATTERN } from '../types.ts';

/**
 * Crockford base32 alphabet: 0-9 + A-Z minus I, L, O, U (32 symbols).
 * MUST match CHARACTER_GUID_PATTERN (types.ts) and CROCKFORD_BASE32_ALPHABET
 * in py_services/character_store.py. Normative source:
 * docs/schema_characters_v3.md §GUID spec.
 */
export const CROCKFORD_BASE32_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

/** Windows device names that must never become a bare filename stem. */
export const WINDOWS_RESERVED_FILE_NAMES = new Set<string>([
    'CON', 'PRN', 'AUX', 'NUL',
    ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
    ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
]);

/**
 * Canonical key form for character names / IDs / aliases: trim + lowercase.
 * Uses `??` (not `||`) so falsy-but-meaningful values (0, false) stringify
 * instead of collapsing to ''.
 */
export function normalizeCharacterKey(value: unknown): string {
    return String(value ?? '').trim().toLowerCase();
}

const INVALID_FILENAME_CHARS = /[/\\:*?"<>|\u0000-\u001f\u007f]/g;
const CONSECUTIVE_DASHES = /-{2,}/g;

/**
 * `characters/` filename STEM for a title (byte-for-byte mirror of
 * py_services/character_store.py `sanitize_filename`; parity pinned by
 * tests/character_identity_vectors.json): replace `\/:*?"<>|` + control
 * chars (incl. DEL \x7f) with `-`, collapse repeats, trim, strip edge
 * dashes, cap 80 chars, guard Windows reserved names with a `-` prefix
 * (original case preserved). Returns the stem — callers append `.json`.
 */
export function sanitizeCharacterFileStem(title: string): string {
    let text = String(title ?? '').replace(INVALID_FILENAME_CHARS, '-');
    text = text.replace(CONSECUTIVE_DASHES, '-').trim().replace(/^-+|-+$/g, '');
    text = text.slice(0, 80).replace(/-+$/g, '');
    if (!text) return '-';
    if (WINDOWS_RESERVED_FILE_NAMES.has(text.toUpperCase())) return `-${text}`;
    return text;
}

/** Speaker tokens that must never become characters (house blocklist). */
export const ALWAYS_BLOCKED_CHARACTER_NAMES = new Set([
    'he', 'she', 'as', 'it', 'that', 'they', 'them', 'the', 'this', 'these', 'those',
]);

/**
 * The single ID-collision scheme: base, base-2, base-3, …
 *
 * The parser's former `name_2` counter is retired in favor of this
 * sibling-suffix form; `[-_]\d+$` suffixes remain strippable by the
 * parser's stripNumericSlugSuffix, and the closed-world workflow's
 * `new-character-2` behavior is preserved.
 *
 * Takes a PRECOMPUTED base ID — callers own the name→base derivation
 * (slugify in the mutation path, lowercase in the parser).
 */
export function buildUniqueCharacterId(
    baseId: string,
    existingIds: ReadonlySet<string> | Iterable<string>,
): string {
    const taken = existingIds instanceof Set ? existingIds : new Set(existingIds);
    let id = baseId;
    let suffix = 2;
    while (taken.has(id)) id = `${baseId}-${suffix++}`;
    return id;
}

/** True when value is an 8-char uppercase Crockford base32 GUID. */
export function isCharacterGuid(value: unknown): value is string {
    return typeof value === 'string' && CHARACTER_GUID_PATTERN.test(value.trim());
}
