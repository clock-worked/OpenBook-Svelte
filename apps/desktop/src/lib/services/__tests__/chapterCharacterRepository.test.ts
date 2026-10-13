// ============================================================================
// Vitest service-layer matrix — chapterCharacterRepository.ts.
//
// Covers two seam contracts:
// 1. The v3 roster invariants (Issue #2): normal editing flows must not
//    (re)create chapter roster files in v3 books — ADR 0003
//    read-legacy-write-current.
// 2. The resolveBookCharacters resolution order the parser now consumes:
//    the v3 characters/ folder first, the legacy root characters.json as
//    fallback, null when the book has neither.
//
// I/O is stubbed by the in-memory fs mock (helpers/inMemoryFs.ts).
// ============================================================================

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { inMemoryFs } from './helpers/inMemoryFs';
import { diffTree } from './helpers/diffTree';
import { makeCharacterFile } from './helpers/fixtures';

vi.mock('$lib/services/fs', async () => {
  const mod = await import('./helpers/inMemoryFs');
  return mod.inMemoryFs;
});

import type { Character, CharactersJson } from '$lib/types';
import {
  loadChapterCharactersData,
  persistChapterCharactersData,
  resolveBookCharacters,
} from '../chapterCharacterRepository';

const ROOT = 'Book-9';
const GA = 'AA01BB02'; // Alice
const ROSTER = 'Ch47/Ch47.characters.json';

function makeCharacter(overrides: Partial<Character> & { name: string }): Character {
  return {
    guid: GA,
    id: GA, // documented mirror: id = guid
    gender: 'Female',
    aliases: [],
    descriptors: [],
    color: null,
    notes: '',
    firstAppearance: null,
    stats: { totalLines: 0, chapterCount: 0 },
    ...overrides,
  } as Character;
}

function seedV3Book(): void {
  inMemoryFs.__reset();
  inMemoryFs.__seed({
    'characters/Alice.json': makeCharacterFile({ guid: GA, title: 'Alice' }),
  });
}

describe('chapterCharacterRepository (v3 roster invariants, issue #2)', () => {

  it('v3 book, roster absent: persist is a no-op — no roster file is created (ADR 0003)', async () => {
    seedV3Book();
    const before = inMemoryFs.__snapshot();

    const data: CharactersJson = {
      formatVersion: '3.0',
      characters: [makeCharacter({ name: 'Alice' })],
    };
    const ok = await persistChapterCharactersData(ROOT, ROSTER, data);

    expect(ok).toBe(false);
    expect(inMemoryFs.__has(ROSTER)).toBe(false);
    expect(diffTree(before, inMemoryFs.__snapshot())).toEqual({
      added: [],
      deleted: [],
      changed: [],
    });
  });

  it('v3 book, bypassV3Guard (the migration): roster IS written, as canonical GUID references', async () => {
    seedV3Book();

    const data: CharactersJson = {
      formatVersion: '3.0',
      characters: [makeCharacter({ name: 'Alice' })],
    };
    const ok = await persistChapterCharactersData(ROOT, ROSTER, data, { bypassV3Guard: true });

    expect(ok).toBe(true);
    expect(inMemoryFs.__readJson(ROSTER)).toEqual({
      formatVersion: '3.0',
      characters: [{ id: GA }],
    });
  });

  it('v2 book (no characters/ folder): persist writes the roster — v2 behavior unchanged', async () => {
    inMemoryFs.__reset();
    inMemoryFs.__seed({
      'characters.json': {
        formatVersion: '2.0',
        characters: [{ id: 'alice', name: 'Alice', gender: 'Female' }],
      },
    });

    const data: CharactersJson = {
      formatVersion: '2.0',
      characters: [{ id: 'alice', name: 'Alice', gender: 'Female' } as Character],
    };
    const ok = await persistChapterCharactersData(ROOT, ROSTER, data);

    expect(ok).toBe(true);
    expect(inMemoryFs.__readJson(ROSTER)).toEqual({
      formatVersion: '2.0',
      characters: [{ id: 'alice' }],
    });
  });

  it('v3 book, roster present: normal persist is a no-op — the roster file is left byte-identical (ADR 0003)', async () => {
    seedV3Book();
    inMemoryFs.__seed({
      [ROSTER]: { formatVersion: '3.0', characters: [{ id: GA }] },
    });
    const before = inMemoryFs.__snapshot();

    const data: CharactersJson = {
      formatVersion: '3.0',
      characters: [makeCharacter({ name: 'Alice' })],
    };
    const ok = await persistChapterCharactersData(ROOT, ROSTER, data);

    expect(ok).toBe(false);
    expect(inMemoryFs.__readText(ROSTER)).toBe(before[ROSTER]);
  });

  it('roster absent: loadChapterCharactersData resolves to null (missing roster = empty roster, not an error)', async () => {
    seedV3Book();

    const data = await loadChapterCharactersData(ROOT, ROSTER);

    expect(data).toBeNull();
  });
});

const LEGACY_STORE = {
  formatVersion: '2.0',
  characters: [
    {
      guid: null,
      id: 'mara',
      name: 'Mara',
      gender: 'Female',
      aliases: [],
      color: null,
      notes: '',
      firstAppearance: null,
      stats: { totalLines: 0, chapterCount: 0 },
    },
  ],
};

describe('chapterCharacterRepository.resolveBookCharacters', () => {
  beforeEach(() => {
    inMemoryFs.__reset();
  });

  it('v3 folder wins over the legacy store when both are present', async () => {
    inMemoryFs.__seed({
      'characters/Catherine.json': makeCharacterFile({ guid: '7KQX2MFT', title: 'Catherine', gender: 'Female' }),
      'characters.json': LEGACY_STORE,
    });

    const resolved = await resolveBookCharacters('');

    expect(resolved).not.toBeNull();
    expect(resolved!.formatVersion).toBe('3.0');
    expect(resolved!.characters).toHaveLength(1);
    expect(resolved!.characters[0].id).toBe('7KQX2MFT'); // id = guid mirror
    expect(resolved!.characters[0].name).toBe('Catherine');
  });

  it('falls back to the legacy characters.json when there is no v3 folder', async () => {
    inMemoryFs.__seed({ 'characters.json': LEGACY_STORE });

    const resolved = await resolveBookCharacters('');

    expect(resolved).toEqual(LEGACY_STORE);
  });

  it('returns null when the book has neither a folder nor a legacy store', async () => {
    inMemoryFs.__seed({ 'Chapter1/chapter.txt': 'nothing to resolve' });

    const resolved = await resolveBookCharacters('');

    expect(resolved).toBeNull();
  });

  it('invalid folder files are skipped and the legacy store is used', async () => {
    inMemoryFs.__seed({
      // Bad GUID + wrong formatVersion: mapCharacterFile rejects both.
      'characters/Bad.json': {
        formatVersion: '2.0',
        guid: 'not-a-guid',
        title: 'Bad',
        gender: 'Unknown',
        aliases: [],
        descriptors: [],
        color: null,
        notes: '',
        firstAppearance: null,
        stats: { totalLines: 0, chapterCount: 0 },
      },
      'characters.json': LEGACY_STORE,
    });

    const resolved = await resolveBookCharacters('');

    expect(resolved).toEqual(LEGACY_STORE);
  });
});
