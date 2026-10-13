// ============================================================================
// Vitest service-layer matrix — chapterCharacterRepository.ts
// (Issue #2: v3 books must not get chapter roster files (re)created by
// normal editing flows — ADR 0003 read-legacy-write-current.)
// ============================================================================

import { describe, expect, it, vi } from 'vitest';
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
