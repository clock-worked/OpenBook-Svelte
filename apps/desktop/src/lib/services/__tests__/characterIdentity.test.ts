// ============================================================================
// Canonical identity module (characterIdentity.ts): cross-language parity
// vectors + unit tests for the collision scheme and the constant sets.
//
// The vector assertions are driven by the shared file
// tests/character_identity_vectors.json, which the Python side asserts
// against in py_services/test_character_identity_parity.py. Keep both green
// when changing either implementation.
// ============================================================================

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
  ALWAYS_BLOCKED_CHARACTER_NAMES,
  CROCKFORD_BASE32_ALPHABET,
  WINDOWS_RESERVED_FILE_NAMES,
  buildUniqueCharacterId,
  isCharacterGuid,
  normalizeCharacterKey,
  resolveCharacterNameForId,
  resolveLineCharacterId,
  sanitizeCharacterFileStem,
} from '../characterIdentity';

interface SanitizeVector { title: string; expected: string; }
interface NormalizeVector { value: unknown; expected: string; }
interface GuidVector { value: unknown; valid: boolean; }

interface IdentityVectors {
  description: string;
  sanitize: SanitizeVector[];
  normalize: NormalizeVector[];
  guids: GuidVector[];
}

// Repo root is six levels up from __tests__/ (services/lib/src/desktop/apps).
const VECTORS_PATH = fileURLToPath(
  new URL('../../../../../../tests/character_identity_vectors.json', import.meta.url),
);

const vectors: IdentityVectors = JSON.parse(readFileSync(VECTORS_PATH, 'utf8'));

describe('characterIdentity parity vectors (tests/character_identity_vectors.json)', () => {
  it.each(vectors.sanitize)('sanitizeCharacterFileStem($title) === $expected', (vector) => {
    expect(sanitizeCharacterFileStem(vector.title)).toBe(vector.expected);
  });

  it.each(vectors.normalize)('normalizeCharacterKey(%p) === %s', (vector) => {
    expect(normalizeCharacterKey(vector.value)).toBe(vector.expected);
  });

  it.each(vectors.guids)('isCharacterGuid(%p) === %s', (vector) => {
    expect(isCharacterGuid(vector.value)).toBe(vector.valid);
  });
});

describe('characterIdentity unit tests', () => {
  it('CROCKFORD_BASE32_ALPHABET is 32 unique symbols excluding I, L, O, U', () => {
    expect(CROCKFORD_BASE32_ALPHABET).toHaveLength(32);
    expect(new Set(CROCKFORD_BASE32_ALPHABET.split('')).size).toBe(32);
    for (const excluded of ['I', 'L', 'O', 'U']) {
      expect(CROCKFORD_BASE32_ALPHABET).not.toContain(excluded);
    }
  });

  it('WINDOWS_RESERVED_FILE_NAMES is the 22 device names (4 + COM1-9 + LPT1-9)', () => {
    expect(WINDOWS_RESERVED_FILE_NAMES.size).toBe(22);
    expect(WINDOWS_RESERVED_FILE_NAMES).toContain('CON');
    expect(WINDOWS_RESERVED_FILE_NAMES).toContain('NUL');
    expect(WINDOWS_RESERVED_FILE_NAMES).toContain('COM1');
    expect(WINDOWS_RESERVED_FILE_NAMES).toContain('COM9');
    expect(WINDOWS_RESERVED_FILE_NAMES).toContain('LPT9');
    expect(WINDOWS_RESERVED_FILE_NAMES).not.toContain('COM10');
  });

  it('ALWAYS_BLOCKED_CHARACTER_NAMES holds the 11 house blocklist words', () => {
    expect(ALWAYS_BLOCKED_CHARACTER_NAMES.size).toBe(11);
    for (const word of ['he', 'she', 'it', 'the', 'that', 'they', 'them', 'this', 'these', 'those', 'as']) {
      expect(ALWAYS_BLOCKED_CHARACTER_NAMES).toContain(word);
    }
  });

  it('buildUniqueCharacterId: the single -2/-3/... collision scheme', () => {
    expect(buildUniqueCharacterId('alice', new Set(['alice']))).toBe('alice-2');
    expect(buildUniqueCharacterId('alice', new Set(['alice', 'alice-2', 'alice-3']))).toBe('alice-4');
    expect(buildUniqueCharacterId('bob', new Set(['alice', 'alice-2']))).toBe('bob');
  });

  it('buildUniqueCharacterId: accepts a plain iterable, not just a Set', () => {
    expect(buildUniqueCharacterId('alice', ['alice', 'alice-2'])).toBe('alice-3');
    expect(buildUniqueCharacterId('alice', [] as string[])).toBe('alice');
  });

  it('isCharacterGuid: trims surrounding whitespace before matching (TS-side only)', () => {
    // Deliberately NOT in the vector file: Python's is_valid_guid does not
    // trim, so padded inputs are a known TS-only allowance.
    expect(isCharacterGuid('  AB12CD34  ')).toBe(true);
  });

  it('normalizeCharacterKey: ?? idiom stringifies falsy-but-meaningful values (TS-side only)', () => {
    // Deliberately NOT in the vector file: Python's `or` idiom diverges for
    // 0 / false.
    expect(normalizeCharacterKey(0)).toBe('0');
    expect(normalizeCharacterKey(false)).toBe('false');
  });
});

describe('canonical line/character resolution (audio generation path)', () => {
  // Mirrors Book-19 shapes: v3 book where the Narrator is a first-class
  // character (uppercase Crockford GUID) and dialogue lines carry
  // `characterId`; name→id maps are lowercase-keyed.
  const NARRATOR_ID = '4JQA1N6V';
  const nameToId = new Map<string, string>([
    ['narrator', NARRATOR_ID],
    ['protagonist', NARRATOR_ID], // alias
  ]);
  const idToName = new Map<string, string>([[NARRATOR_ID.toLowerCase(), 'Narrator']]);

  describe('resolveLineCharacterId', () => {
    it('v3 line: returns the characterId as-is (uppercase GUID preserved)', () => {
      expect(resolveLineCharacterId({ characterId: NARRATOR_ID }, nameToId)).toBe(NARRATOR_ID);
    });

    it('legacy line: resolves chosenSpeaker by name, case-insensitively', () => {
      expect(resolveLineCharacterId({ chosenSpeaker: 'Narrator' }, nameToId)).toBe(NARRATOR_ID);
      expect(resolveLineCharacterId({ chosenSpeaker: 'narrator' }, nameToId)).toBe(NARRATOR_ID);
    });

    it('legacy line: resolves chosenSpeaker by alias', () => {
      expect(resolveLineCharacterId({ chosenSpeaker: 'Protagonist' }, nameToId)).toBe(NARRATOR_ID);
    });

    it('characterId present wins over chosenSpeaker', () => {
      expect(resolveLineCharacterId({ characterId: NARRATOR_ID, chosenSpeaker: 'Protagonist' }, nameToId)).toBe(NARRATOR_ID);
    });

    it('characterId null with no speaker is an Unknown line, not a narrator line', () => {
      expect(resolveLineCharacterId({ characterId: null }, nameToId)).toBeNull();
      expect(resolveLineCharacterId({}, nameToId)).toBeNull();
    });

    it('unresolvable chosenSpeaker is null', () => {
      expect(resolveLineCharacterId({ chosenSpeaker: 'Some Stranger' }, nameToId)).toBeNull();
    });
  });

  describe('resolveCharacterNameForId', () => {
    it('uppercase v3 GUID resolves through the lowercase-keyed id→name map', () => {
      expect(resolveCharacterNameForId(NARRATOR_ID, idToName)).toBe('Narrator');
    });

    it('lowercase id also resolves', () => {
      expect(resolveCharacterNameForId(NARRATOR_ID.toLowerCase(), idToName)).toBe('Narrator');
    });

    it('falls back to the fallback name, then the raw id', () => {
      const unknown = 'AB12CD34';
      expect(resolveCharacterNameForId(unknown, idToName, 'Fallback')).toBe('Fallback');
      expect(resolveCharacterNameForId(unknown, idToName)).toBe(unknown);
    });
  });
});
