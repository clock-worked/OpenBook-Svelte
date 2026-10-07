// ============================================================================
// Vitest service-layer matrix — characterMigration.ts (FE auto-on-open driver)
// (docs/character_details_test_plan.md §Vitest service-layer matrix, rows
// "migration v2→v3 (FE module)" and "migration v1 (FE module)";
// algorithm: docs/schema_characters_v3.md §Migration).
//
// I/O is stubbed by the in-memory fs mock (helpers/inMemoryFs.ts).
// ============================================================================

import { describe, expect, it, vi } from 'vitest';
import { inMemoryFs } from './helpers/inMemoryFs';
import { diffTree, staleValuesInTree } from './helpers/diffTree';
import { makeCharacterFile } from './helpers/fixtures';

vi.mock('$lib/services/fs', async () => {
  const mod = await import('./helpers/inMemoryFs');
  return mod.inMemoryFs;
});

import { CHARACTER_GUID_PATTERN } from '$lib/types';
import { migrateCharactersV1ToV3, migrateCharactersV2ToV3 } from '../characterMigration';

const ROOT = 'Book-9';
const OLD_SLUGS = ['alice', 'bob'];
const CROCKFORD_BASE32_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

/**
 * Independent re-implementation of the deterministic mint
 * (SHA-256("<book> ::v3::<v2-id>") → first 5 bytes → 8-char Crockford,
 * docs/schema_characters_v3.md §GUID spec) — the test's own derivation, so a
 * deviation in the source fails the equality below.
 */
async function expectedGuid(bookName: string, v2Id: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${bookName}::v3::${v2Id}`));
  const bytes = new Uint8Array(digest.slice(0, 5));
  let value = 0;
  for (let i = 0; i < 5; i++) value = value * 256 + bytes[i];
  const chars: string[] = [];
  for (let i = 0; i < 8; i++) {
    chars.push(CROCKFORD_BASE32_ALPHABET[value % 32]);
    value = Math.floor(value / 32);
  }
  return chars.reverse().join('');
}

/** Canonical v2 book: root store + 2 chapters (dialogue + roster) + voices + one manifest + a decoy. */
function seedV2Book(): void {
  inMemoryFs.__reset();
  inMemoryFs.__seed({
    'characters.json': {
      formatVersion: '2.0',
      characters: [
        {
          id: 'alice',
          name: 'Alice',
          gender: 'Female',
          aliases: ['Al'],
          descriptors: ['tall'],
          race: null,
          color: '#F00',
          notes: 'hero',
          firstAppearance: 'Chapter1',
          count: 3,
          chapterCount: 2,
          stats: { totalLines: 3, chapterCount: 2 },
          voice: null,
          provider: null,
          voiceId: null,
          voiceMeta: null,
          manifestStats: null,
          roleLabels: ['protagonist'],
        },
        {
          id: 'bob',
          name: 'Bob',
          gender: 'Male',
          aliases: [],
          descriptors: [],
          race: null,
          color: '#00F',
          notes: '',
          firstAppearance: 'Chapter1',
          count: 1,
          chapterCount: 1,
          stats: { totalLines: 1, chapterCount: 1 },
          voice: null,
          provider: null,
          voiceId: null,
          voiceMeta: null,
          manifestStats: null,
          roleLabels: [],
        },
      ],
    },
    'Chapter1/dialogue.json': {
      formatVersion: '3.1',
      lines: [
        {
          id: 1,
          characterId: 'alice',
          text: 'Hello there.',
          candidates: [{ characterId: 'bob', confidence: 0.4 }],
          attribution: { candidates: [{ characterId: 'alice' }, { characterId: null }] },
        },
        { id: 2, characterId: 'bob', text: 'Hi back.' },
      ],
      stats: { characterBreakdown: { alice: 1, bob: 1 } },
    },
    'Chapter2/dialogue.json': {
      formatVersion: '3.1',
      lines: [{ id: 1, characterId: 'bob', text: 'See you tomorrow.' }],
      stats: { characterBreakdown: { bob: 1 } },
    },
    'Chapter1/Chapter1.characters.json': {
      formatVersion: '2.0',
      characters: ['alice', { id: 'bob' }],
    },
    'Chapter2/Chapter2.characters.json': {
      formatVersion: '2.0',
      characters: [{ id: 'alice' }],
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
          metadata: { usedByCharacters: ['alice'] },
        },
      ],
      assignments: [
        { characterId: 'alice', voiceId: 'vibevoice_local-a.wav' },
        { characterId: 'bob', voiceId: 'vibevoice_local-a.wav' },
      ],
    },
    'Chapter1/audio_lines/Alice/manifest.json': {
      characterId: 'alice',
      characterName: 'Alice',
      clips: [{ id: 1, chapter: 'Chapter1', characterId: 'alice' }],
      metadata: { totalClips: 1 },
    },
    'notes.json': { hello: 'decoy' },
  });
}

describe('characterMigration (FE module)', () => {

  it('v2 → v3: splits the store into characters/*.json (valid deterministic GUIDs), remaps every reference family, deletes root characters.json, zero stale slugs tree-wide, backups hold the pre-images', async () => {
    seedV2Book();
    const before = inMemoryFs.__snapshot();
    const [gAlice, gBob] = [await expectedGuid(ROOT, 'alice'), await expectedGuid(ROOT, 'bob')];

    const result = await migrateCharactersV2ToV3(ROOT, ['Chapter1', 'Chapter2']);

    // Result shape.
    expect(result.ok).toBe(true);
    expect(result.abortReason).toBeUndefined();
    expect(result.retiredLegacy).toBe(true);
    expect(result.createdCharacterFiles).toEqual(['characters/Alice.json', 'characters/Bob.json']);
    expect(result.remap).toEqual({ alice: gAlice, bob: gBob });
    expect(result.counts).toEqual({
      clusters: 2,
      dialogueRefs: 8,
      rosterRefs: 3,
      voiceRefs: 3,
      manifestRefs: 2,
    });

    const after = inMemoryFs.__snapshot();

    // Legacy store deleted; decoy untouched.
    expect(inMemoryFs.__has('characters.json')).toBe(false);
    expect(after['notes.json']).toBe(before['notes.json']);

    // Character files: pattern-valid deterministic GUIDs + verbatim v2 fields.
    const aliceFile = inMemoryFs.__readJson('characters/Alice.json');
    expect(aliceFile.formatVersion).toBe('3.0');
    expect(aliceFile.guid).toBe(gAlice);
    expect(aliceFile.guid).toMatch(CHARACTER_GUID_PATTERN);
    expect(aliceFile.title).toBe('Alice');
    expect(aliceFile.gender).toBe('Female');
    expect(aliceFile.aliases).toEqual(['Al']);
    expect(aliceFile.descriptors).toEqual(['tall']);
    expect(aliceFile.color).toBe('#F00');
    expect(aliceFile.notes).toBe('hero');
    expect(aliceFile.firstAppearance).toBe('Chapter1');
    expect(aliceFile.stats).toEqual({ totalLines: 3, chapterCount: 2 });
    expect(aliceFile.roleLabels).toEqual(['protagonist']);
    expect(aliceFile.id).toBeUndefined(); // the v2 slug id is retired
    const bobFile = inMemoryFs.__readJson('characters/Bob.json');
    expect(bobFile.guid).toBe(gBob);
    expect(bobFile.guid).toMatch(CHARACTER_GUID_PATTERN);
    expect(bobFile.title).toBe('Bob');

    // Dialogue: all four reference sites are GUIDs.
    const ch1 = inMemoryFs.__readJson('Chapter1/dialogue.json');
    expect(ch1.lines[0].characterId).toBe(gAlice);
    expect(ch1.lines[0].candidates[0].characterId).toBe(gBob);
    expect(ch1.lines[0].attribution.candidates[0].characterId).toBe(gAlice);
    expect(ch1.lines[1].characterId).toBe(gBob);
    expect(ch1.stats.characterBreakdown).toEqual({ [gAlice]: 1, [gBob]: 1 });
    const ch2 = inMemoryFs.__readJson('Chapter2/dialogue.json');
    expect(ch2.lines[0].characterId).toBe(gBob);
    expect(ch2.stats.characterBreakdown).toEqual({ [gBob]: 1 });

    // Rosters: every entry a GUID, membership unchanged, v3 roster version.
    const roster1 = inMemoryFs.__readJson('Chapter1/Chapter1.characters.json');
    expect(roster1.formatVersion).toBe('3.0');
    expect(roster1.characters).toEqual([{ id: gAlice }, { id: gBob }]);
    const roster2 = inMemoryFs.__readJson('Chapter2/Chapter2.characters.json');
    expect(roster2.characters).toEqual([{ id: gAlice }]);

    // Voices: assignments + usedByCharacters are GUIDs.
    const voices = inMemoryFs.__readJson('voices.json');
    expect(voices.assignments.map((assignment: any) => assignment.characterId)).toEqual([gAlice, gBob]);
    expect(voices.voices[0].metadata.usedByCharacters).toEqual([gAlice]);

    // Manifest: characterId remapped, characterName stays a name, path (dir name) unchanged.
    const manifest = inMemoryFs.__readJson('Chapter1/audio_lines/Alice/manifest.json');
    expect(manifest.characterId).toBe(gAlice);
    expect(manifest.characterName).toBe('Alice');
    expect(manifest.clips[0].characterId).toBe(gAlice);

    // Zero old-slug occurrences tree-wide (recursive string-value walk, excl. _backups).
    expect(staleValuesInTree(after, OLD_SLUGS, { excludePrefix: '_backups/' })).toEqual([]);

    // Backups: pre-images of every changed file (legacy store + 6 reference files).
    const backupPaths = Object.keys(after).filter((path) => path.startsWith('_backups/character-v3/'));
    expect(backupPaths.length).toBe(7);
    const backupRelPaths = backupPaths
      .map((path) => path.split('/').slice(3).join('/'))
      .sort();
    expect(backupRelPaths).toEqual([
      'Chapter1/Chapter1.characters.json',
      'Chapter1/audio_lines/Alice/manifest.json',
      'Chapter1/dialogue.json',
      'Chapter2/Chapter2.characters.json',
      'Chapter2/dialogue.json',
      'characters.json',
      'voices.json',
    ]);
    // The retired store's pre-image is content-identical to the original.
    const backupStorePath = backupPaths.find((path) => path.endsWith('/characters.json'));
    expect(backupStorePath).toBeDefined();
    expect(JSON.parse(after[backupStorePath!])).toEqual(JSON.parse(before['characters.json']));
  });

  it('v2 → v3: second run on the migrated tree is a no-op success with zero writes and no second backup', async () => {
    seedV2Book();
    const first = await migrateCharactersV2ToV3(ROOT, ['Chapter1', 'Chapter2']);
    expect(first.ok).toBe(true);

    const before = inMemoryFs.__snapshot();
    const second = await migrateCharactersV2ToV3(ROOT, ['Chapter1', 'Chapter2']);
    expect(second.ok).toBe(true);
    expect(second.changedFiles).toEqual([]);

    const after = inMemoryFs.__snapshot();
    expect(diffTree(before, after)).toEqual({ added: [], deleted: [], changed: [] });

    const tsDirs = (snapshot: Record<string, string>) =>
      new Set(
        Object.keys(snapshot)
          .filter((path) => path.startsWith('_backups/character-v3/'))
          .map((path) => path.split('/')[3]),
      );
    expect(tsDirs(after)).toEqual(tsDirs(before));
  });

  it('v2 → v3: in-flight state (folder with valid files AND root store present) → abort, zero writes', async () => {
    const gAlice = await expectedGuid(ROOT, 'alice');
    inMemoryFs.__reset();
    inMemoryFs.__seed({
      'characters/Alice.json': makeCharacterFile({ guid: gAlice, title: 'Alice' }),
      'characters.json': {
        formatVersion: '2.0',
        characters: [{ id: 'alice', name: 'Alice', gender: 'Female' }],
      },
    });
    const before = inMemoryFs.__snapshot();

    const result = await migrateCharactersV2ToV3(ROOT, ['Chapter1']);

    expect(result.ok).toBe(false);
    expect(result.abortReason).toMatch(/in flight/i);
    expect(diffTree(before, inMemoryFs.__snapshot())).toEqual({ added: [], deleted: [], changed: [] });
  });

  it('v1 → v3: name-keyed book.characters.json migrates with mapped genders (m/f/u) and valid deterministic GUIDs; legacy store deleted; v1 script stays name-based; zero old slugs tree-wide (R11)', async () => {
    inMemoryFs.__reset();
    inMemoryFs.__seed({
      'book.characters.json': {
        characters: [
          { name: 'Alice', gender: 'f', aliases: ['Al'], color: '#111', first_seen_chapter: 'Chapter1' },
          { name: 'Bob', gender: 'm', aliases: [], color: '#222', first_seen_chapter: 'Chapter1' },
          { name: 'Cara', gender: 'u', aliases: [], color: null },
        ],
      },
      'Chapter1/Chapter1.script.json': {
        formatVersion: '1.0',
        lines: [{ text: 'Hi Alice.', chosenSpeaker: 'Alice' }],
      },
    });
    const before = inMemoryFs.__snapshot();

    const result = await migrateCharactersV1ToV3(ROOT, ['Chapter1']);

    expect(result.ok).toBe(true);
    const [gAlice, gBob, gCara] = [
      await expectedGuid(ROOT, 'alice'),
      await expectedGuid(ROOT, 'bob'),
      await expectedGuid(ROOT, 'cara'),
    ];
    expect(result.createdCharacterFiles).toEqual([
      'characters/Alice.json',
      'characters/Bob.json',
      'characters/Cara.json',
    ]);
    expect(result.remap).toEqual({ alice: gAlice, bob: gBob, cara: gCara });
    expect(inMemoryFs.__has('book.characters.json')).toBe(false);

    const alice = inMemoryFs.__readJson('characters/Alice.json');
    expect(alice.guid).toBe(gAlice);
    expect(alice.guid).toMatch(CHARACTER_GUID_PATTERN);
    expect(alice.title).toBe('Alice');
    expect(alice.gender).toBe('Female');
    const bob = inMemoryFs.__readJson('characters/Bob.json');
    expect(bob.guid).toBe(gBob);
    expect(bob.guid).toMatch(CHARACTER_GUID_PATTERN);
    expect(bob.gender).toBe('Male');
    const cara = inMemoryFs.__readJson('characters/Cara.json');
    expect(cara.guid).toBe(gCara);
    expect(cara.guid).toMatch(CHARACTER_GUID_PATTERN);
    expect(cara.gender).toBe('Unknown');

    // v1 script file stays name-based (R11): byte-identical.
    const after = inMemoryFs.__snapshot();
    expect(after['Chapter1/Chapter1.script.json']).toBe(before['Chapter1/Chapter1.script.json']);

    // Zero old-slug occurrences tree-wide (recursive string-value walk, excl. _backups).
    expect(staleValuesInTree(after, ['alice', 'bob', 'cara'], { excludePrefix: '_backups/' })).toEqual([]);
  });
});
