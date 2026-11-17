// ============================================================================
// Vitest service-layer matrix — bookCharacterRepository.ts
// (docs/character_details_test_plan.md §Vitest service-layer matrix, row
// "persist round-trip": a record with roleLabels on disk survives an
// unrelated character's persist — invariants IN-2/IN-5).
// ============================================================================

import { describe, expect, it, vi } from 'vitest';
import { inMemoryFs } from './helpers/inMemoryFs';
import { diffTree } from './helpers/diffTree';
import { makeCharacterFile } from './helpers/fixtures';

vi.mock('$lib/services/fs', async () => {
  const mod = await import('./helpers/inMemoryFs');
  return mod.inMemoryFs;
});

import type { Character } from '$lib/types';
import { readCharacterFolder } from '$lib/services/fs';
import { loadBookCharactersData, writeCharacterRecord } from '../bookCharacterRepository';
import { characterStats } from '../characterStats';

const ROOT = 'Book-9';
const GA = 'AA01BB02'; // Alice
const GB = 'CC03DD04'; // Bob

describe('bookCharacterRepository (v3 folder persistence)', () => {

  it('roleLabels round-trip: a character file on disk carrying roleLabels survives an unrelated character\'s persist intact (IN-2/IN-5)', async () => {
    inMemoryFs.__reset();
    inMemoryFs.__seed({
      'characters/Alice.json': makeCharacterFile({
        guid: GA,
        title: 'Alice',
        gender: 'Female',
        roleLabels: ['protagonist'],
      }),
    });
    const before = inMemoryFs.__snapshot();

    // Load the folder (populates the raw-document cache), then persist an
    // UNRELATED character.
    const folder = await readCharacterFolder(ROOT);
    expect(folder?.characters.map((character) => character.name)).toEqual(['Alice']);

    const bob: Character = {
      guid: GB,
      id: GB, // documented mirror: id = guid
      name: 'Bob',
      gender: 'Male',
      aliases: [],
      descriptors: [],
      color: null,
      notes: '',
      firstAppearance: null,
      stats: { totalLines: 0, chapterCount: 0 },
    };
    const written = await writeCharacterRecord(ROOT, bob);
    expect(written).toBe(true);

    const after = inMemoryFs.__snapshot();

    // Alice's file is byte-identical — the unknown roleLabels field survived.
    expect(after['characters/Alice.json']).toBe(before['characters/Alice.json']);
    expect(inMemoryFs.__readJson('characters/Alice.json').roleLabels).toEqual(['protagonist']);

    // The only tree change is Bob's new file.
    expect(diffTree(before, after)).toEqual({
      added: ['characters/Bob.json'],
      deleted: [],
      changed: [],
    });
  });
});

describe('bookCharacterRepository (stats: the accumulated stats are the one answer)', () => {
  it('a seeded book loads with the accumulated stats, never re-derived from dialogue; only a missing firstAppearance is backfilled', async () => {
    inMemoryFs.__reset();
    inMemoryFs.__seed({
      // Accumulated stats (ADR 0012) deliberately disagree with the dialogue
      // below: the answer must be the accumulated stats.
      'characters/Alice.json': makeCharacterFile({
        guid: GA,
        title: 'Alice',
        stats: { totalLines: 5, chapterCount: 2 },
      }),
      'characters/Bob.json': makeCharacterFile({
        guid: GB,
        title: 'Bob',
        firstAppearance: '01-One',
        stats: { totalLines: 0, chapterCount: 0 },
      }),
      '01-One/dialogue.json': { formatVersion: '3.0', lines: [{ id: 'l1', characterId: GB, text: 'Hi.' }] },
      '02-Two/dialogue.json': { formatVersion: '3.0', lines: [{ id: 'l2', characterId: GA, text: 'Hello.' }] },
    });
    const before = inMemoryFs.__snapshot();

    const data = await loadBookCharactersData(ROOT, [
      { title: '01-One', parsed: true },
      { title: '02-Two', parsed: true },
    ]);
    const byName = new Map(data.characters.map((character) => [character.name, character]));

    expect(characterStats(byName.get('Alice'))).toEqual({ totalLines: 5, chapterCount: 2 });
    expect(characterStats(byName.get('Bob'))).toEqual({ totalLines: 0, chapterCount: 0 });
    expect(byName.get('Alice')?.firstAppearance).toBe('02-Two');
    expect(byName.get('Bob')?.firstAppearance).toBe('01-One');

    const after = inMemoryFs.__snapshot();
    // Only Alice's file changed (the firstAppearance backfill), and its
    // stats on disk are still the accumulated ones.
    expect(diffTree(before, after)).toEqual({ added: [], deleted: [], changed: ['characters/Alice.json'] });
    expect(inMemoryFs.__readJson('characters/Alice.json').stats).toEqual({ totalLines: 5, chapterCount: 2 });
  });
});
