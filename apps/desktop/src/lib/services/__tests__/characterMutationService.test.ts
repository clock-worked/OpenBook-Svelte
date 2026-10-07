// ============================================================================
// Vitest service-layer matrix — characterMutationService.ts
// (docs/character_details_test_plan.md §Vitest service-layer matrix;
// invariants IN-1…IN-8 from docs/character_details_alias_design.md).
//
// I/O is stubbed by the in-memory fs mock (helpers/inMemoryFs.ts); every
// mutation test asserts diffTree(before, after) == the expected changed set.
// ============================================================================

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { inMemoryFs } from './helpers/inMemoryFs';
import { diffTree, staleValuesInTree } from './helpers/diffTree';
import { makeCharacterFile } from './helpers/fixtures';

vi.mock('$lib/services/fs', async () => {
  const mod = await import('./helpers/inMemoryFs');
  return mod.inMemoryFs;
});

import { CHARACTER_GUID_PATTERN } from '$lib/types';
import {
  addCentralCharacterAlias,
  createCentralCharacter,
  deleteCentralCharacter,
  detachCentralCharacterAlias,
  mergeCentralCharacters,
  renameCentralCharacter,
  setCentralCharacterColor,
  setCentralCharacterDescriptors,
  setCentralCharacterGender,
  type CharacterFileRecord,
} from '../characterMutationService';

const ROOT = 'Book-9';
const GA = 'AA01BB02'; // Alice's GUID
const GB = 'CC03DD04'; // Bob's GUID

/** Canonical v3 book: 2 characters + one chapter (dialogue/roster) + voices. */
function seedBook(): void {
  inMemoryFs.__reset();
  inMemoryFs.__seed({
    'characters/Alice.json': makeCharacterFile({
      guid: GA,
      title: 'Alice',
      gender: 'Female',
      aliases: ['Al'],
      descriptors: ['brave'],
      color: '#F00',
      stats: { totalLines: 3, chapterCount: 1 },
    }),
    'characters/Bob.json': makeCharacterFile({
      guid: GB,
      title: 'Bob',
      gender: 'Male',
      descriptors: ['kind'],
      stats: { totalLines: 1, chapterCount: 1 },
    }),
    'Chapter1/dialogue.json': {
      formatVersion: '3.1',
      lines: [
        {
          id: 1,
          characterId: GA,
          text: 'Hello there.',
          candidates: [{ characterId: GB, confidence: 0.4 }],
          attribution: { candidates: [{ characterId: GA }, { characterId: null }] },
        },
        { id: 2, characterId: null, text: 'The night was cold.' },
      ],
      stats: { characterBreakdown: { [GA]: 1, [GB]: 1 } },
    },
    'Chapter1/Chapter1.characters.json': {
      formatVersion: '3.0',
      characters: [{ id: GA }, { id: GB }],
    },
    'voices.json': {
      formatVersion: '2.0',
      voices: [
        {
          id: 'vibevoice_local-a.wav',
          displayName: 'Voice A',
          provider: 'vibevoice_local',
          previewUrl: null,
          notes: '',
          // usedByCharacters is deliberately empty: it is derived, regenerable
          // metadata (IN-6) and NOT part of the merge fan-out, which reuses
          // remapCharacterIdInVoicesAssignments verbatim (assignments only).
          metadata: { usedByCharacters: [] },
        },
      ],
      assignments: [
        { characterId: GA, voiceId: 'vibevoice_local-a.wav' },
        { characterId: GB, voiceId: 'vibevoice_local-a.wav' },
      ],
    },
  });
}

function recordFor(fileName: string): CharacterFileRecord {
  const data = inMemoryFs.__readJson(`characters/${fileName}`);
  return { fileName, data };
}

const NO_DIFF = { added: [] as string[], deleted: [] as string[], changed: [] as string[] };

beforeEach(() => seedBook());

describe('characterMutationService (v3 single-file ops)', () => {

  it('rename: file renamed, title updated, old title kept as alias; diffTree == {del Alice.json, add Alicia.json}; references byte-identical (core safety, IN-1/IN-3/IN-7)', async () => {
    const before = inMemoryFs.__snapshot();
    const result = await renameCentralCharacter(ROOT, recordFor('Alice.json'), 'Alicia');

    expect(result.ok).toBe(true);
    const after = inMemoryFs.__snapshot();

    expect(inMemoryFs.__has('characters/Alice.json')).toBe(false);
    expect(inMemoryFs.__has('characters/Alicia.json')).toBe(true);
    const renamed = inMemoryFs.__readJson('characters/Alicia.json');
    expect(renamed.guid).toBe(GA); // identity is the GUID — unchanged
    expect(renamed.title).toBe('Alicia');
    expect(renamed.aliases).toContain('Alice'); // old title preserved (R1/IN-7)

    const diff = diffTree(before, after);
    expect(diff).toEqual({
      added: ['characters/Alicia.json'],
      deleted: ['characters/Alice.json'],
      changed: [],
    });

    // Dialogue / roster / voices / the other character: byte-identical.
    for (const path of [
      'Chapter1/dialogue.json',
      'Chapter1/Chapter1.characters.json',
      'voices.json',
      'characters/Bob.json',
    ]) {
      expect(after[path], `${path} must be byte-identical`).toBe(before[path]);
    }
  });

  it('rename collision: target title equals another character (case-insensitive) → visible rejection, zero files changed (R7/IN-8)', async () => {
    const before = inMemoryFs.__snapshot();
    const result = await renameCentralCharacter(ROOT, recordFor('Alice.json'), 'bob');

    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/Bob/);
    expect(result.message).toMatch(/already exists/);
    expect(diffTree(before, inMemoryFs.__snapshot())).toEqual(NO_DIFF);
  });

  it('addAlias: exactly one file changes and the aliases array is the only mutation (IN-3)', async () => {
    const before = inMemoryFs.__snapshot();
    const ok = await addCentralCharacterAlias(ROOT, recordFor('Alice.json'), 'Ally');
    expect(ok).toBe(true);

    const diff = diffTree(before, inMemoryFs.__snapshot());
    expect(diff).toEqual({ added: [], deleted: [], changed: ['characters/Alice.json'] });

    const previous = JSON.parse(before['characters/Alice.json']);
    const updated = inMemoryFs.__readJson('characters/Alice.json');
    expect(updated.aliases).toEqual(['Al', 'Ally']);
    for (const key of Object.keys(previous)) {
      if (key === 'aliases' || key === 'updatedAt') continue;
      expect(updated[key], `field '${key}' must be untouched`).toEqual(previous[key]);
    }
  });

  it('addAlias: case-insensitive duplicate → false, no write', async () => {
    const before = inMemoryFs.__snapshot();
    const ok = await addCentralCharacterAlias(ROOT, recordFor('Alice.json'), 'AL');
    expect(ok).toBe(false);
    expect(inMemoryFs.__snapshot()).toEqual(before);
  });

  it('addAlias: alias equal to another character\'s title → false, no write (IN-8)', async () => {
    const before = inMemoryFs.__snapshot();
    const ok = await addCentralCharacterAlias(ROOT, recordFor('Alice.json'), 'Bob');
    expect(ok).toBe(false);
    expect(inMemoryFs.__snapshot()).toEqual(before);
  });

  it('removeAlias: exactly one file changes; removing a missing alias → false, no write (IN-3)', async () => {
    const before = inMemoryFs.__snapshot();
    const ok = await detachCentralCharacterAlias(ROOT, recordFor('Alice.json'), 'Al');
    expect(ok).toBe(true);
    expect(diffTree(before, inMemoryFs.__snapshot())).toEqual({
      added: [],
      deleted: [],
      changed: ['characters/Alice.json'],
    });
    expect(inMemoryFs.__readJson('characters/Alice.json').aliases).toEqual([]);

    const before2 = inMemoryFs.__snapshot();
    const okMissing = await detachCentralCharacterAlias(ROOT, recordFor('Alice.json'), 'Nope');
    expect(okMissing).toBe(false);
    expect(inMemoryFs.__snapshot()).toEqual(before2);
  });

  it('descriptors: set replaces the list in exactly one file; a filtered set ("remove") also touches nothing else (IN-3)', async () => {
    const before = inMemoryFs.__snapshot();
    const ok = await setCentralCharacterDescriptors(ROOT, recordFor('Alice.json'), ['tall', 'red hair']);
    expect(ok).toBe(true);
    expect(diffTree(before, inMemoryFs.__snapshot())).toEqual({
      added: [],
      deleted: [],
      changed: ['characters/Alice.json'],
    });
    expect(inMemoryFs.__readJson('characters/Alice.json').descriptors).toEqual(['tall', 'red hair']);

    const before2 = inMemoryFs.__snapshot();
    const ok2 = await setCentralCharacterDescriptors(ROOT, recordFor('Alice.json'), ['tall']);
    expect(ok2).toBe(true);
    expect(diffTree(before2, inMemoryFs.__snapshot())).toEqual({
      added: [],
      deleted: [],
      changed: ['characters/Alice.json'],
    });
    expect(inMemoryFs.__readJson('characters/Alice.json').descriptors).toEqual(['tall']);
  });

  it('setGender: exactly one file changes; the other character is byte-identical (IN-3)', async () => {
    const before = inMemoryFs.__snapshot();
    const ok = await setCentralCharacterGender(ROOT, recordFor('Alice.json'), 'Male');
    expect(ok).toBe(true);
    const after = inMemoryFs.__snapshot();
    expect(diffTree(before, after)).toEqual({
      added: [],
      deleted: [],
      changed: ['characters/Alice.json'],
    });
    expect(inMemoryFs.__readJson('characters/Alice.json').gender).toBe('Male');
    expect(after['characters/Bob.json']).toBe(before['characters/Bob.json']);
  });

  it('setColor: exactly one file changes; the other character is byte-identical (IN-3)', async () => {
    const before = inMemoryFs.__snapshot();
    const ok = await setCentralCharacterColor(ROOT, recordFor('Bob.json'), '#11AA22');
    expect(ok).toBe(true);
    const after = inMemoryFs.__snapshot();
    expect(diffTree(before, after)).toEqual({
      added: [],
      deleted: [],
      changed: ['characters/Bob.json'],
    });
    expect(inMemoryFs.__readJson('characters/Bob.json').color).toBe('#11AA22');
    expect(after['characters/Alice.json']).toBe(before['characters/Alice.json']);
  });

  it('create: new characters/<Title>.json with a pattern-valid, folder-unique GUID; only addition in the tree (R7 auto-upsert, IN-8)', async () => {
    const before = inMemoryFs.__snapshot();
    const created = await createCentralCharacter(ROOT, 'Dana', 'Female');
    expect(created).not.toBeNull();
    expect(created!.guid).toMatch(CHARACTER_GUID_PATTERN);
    const folderGuids = [GA, GB, created!.guid];
    expect(new Set(folderGuids).size).toBe(folderGuids.length);

    const after = inMemoryFs.__snapshot();
    expect(diffTree(before, after)).toEqual({
      added: ['characters/Dana.json'],
      deleted: [],
      changed: [],
    });
    const onDisk = inMemoryFs.__readJson('characters/Dana.json');
    expect(onDisk.formatVersion).toBe('3.0');
    expect(onDisk.guid).toBe(created!.guid);
    expect(onDisk.title).toBe('Dana');
  });

  it('delete: file removed; a second delete is an idempotent success (IN-2)', async () => {
    const before = inMemoryFs.__snapshot();
    const first = await deleteCentralCharacter(ROOT, recordFor('Bob.json'));
    expect(first).toBe(true);
    expect(inMemoryFs.__has('characters/Bob.json')).toBe(false);
    const second = await deleteCentralCharacter(ROOT, recordFor('Bob.json'));
    expect(second).toBe(true);
    expect(diffTree(before, inMemoryFs.__snapshot())).toEqual({
      added: [],
      deleted: ['characters/Bob.json'],
      changed: [],
    });
  });

  it('merge: target absorbs aliases (incl. source title) + descriptor union; exact changed-file list; zero source-GUID tokens remain tree-wide (the one fan-out, IN-4/IN-5/IN-7)', async () => {
    const before = inMemoryFs.__snapshot();
    const result = await mergeCentralCharacters(
      ROOT,
      [{ title: 'Chapter1' }],
      recordFor('Alice.json'), // source: GA
      recordFor('Bob.json'), // target: GB
    );
    expect(result.ok).toBe(true);
    const after = inMemoryFs.__snapshot();

    // Source file deleted; the survivor keeps its GUID.
    expect(inMemoryFs.__has('characters/Alice.json')).toBe(false);
    const survivor = inMemoryFs.__readJson('characters/Bob.json');
    expect(survivor.guid).toBe(GB);
    expect(survivor.title).toBe('Bob');
    expect(survivor.aliases).toEqual(['Alice', 'Al']); // source title absorbed + source aliases
    expect([...survivor.descriptors].sort()).toEqual(['brave', 'kind']); // descriptor union

    // diffTree == the exact expected changed-file list.
    const diff = diffTree(before, after);
    expect(diff.deleted).toEqual(['characters/Alice.json']);
    expect(diff.added).toEqual([]);
    expect(diff.changed).toEqual([
      'Chapter1/Chapter1.characters.json',
      'Chapter1/dialogue.json',
      'characters/Bob.json',
      'voices.json',
    ]);

    // Per-family spot checks: the fan-out is exact-string GUID substitution.
    const dialogue = inMemoryFs.__readJson('Chapter1/dialogue.json');
    expect(dialogue.lines[0].characterId).toBe(GB);
    expect(dialogue.lines[0].candidates[0].characterId).toBe(GB);
    expect(dialogue.lines[0].attribution.candidates[0].characterId).toBe(GB);
    expect(dialogue.stats.characterBreakdown).toEqual({ [GB]: 2 });
    const roster = inMemoryFs.__readJson('Chapter1/Chapter1.characters.json');
    expect(roster.characters.map((entry: any) => entry.id)).toEqual([GB, GB]);
    const voices = inMemoryFs.__readJson('voices.json');
    expect(voices.assignments.every((assignment: any) => assignment.characterId === GB)).toBe(true);

    // Tree-wide scan: zero GA tokens remain anywhere.
    expect(staleValuesInTree(after, [GA])).toEqual([]);
  });
});
