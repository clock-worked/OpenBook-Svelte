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
import { readCharacterFolder, writeCharacterRecord } from '../bookCharacterRepository';

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
