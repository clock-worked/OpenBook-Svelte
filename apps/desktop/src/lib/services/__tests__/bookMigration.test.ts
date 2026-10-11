// ============================================================================
// Vitest service-layer tests — bookMigration.ts (Settings → "Migrate book
// data" driver). I/O is stubbed by the in-memory fs mock (helpers/inMemoryFs.ts).
// ============================================================================

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { inMemoryFs } from './helpers/inMemoryFs';
import { makeCharacterFile } from './helpers/fixtures';

vi.mock('$lib/services/fs', async () => {
  const mod = await import('./helpers/inMemoryFs');
  return mod.inMemoryFs;
});

import { CHARACTER_GUID_PATTERN } from '$lib/types';
import type { ChapterStatus } from '$lib/stores/bookState';
import { collectStaleDialogueRefs, humanizeSlug, migrateBookToV3 } from '../bookMigration';

const ROOT = 'Book-7';
const G_ALICE = 'A1B2C3D4';
const G_NARRATOR = 'N0RR4T0R';

function chapter(title: string): ChapterStatus {
  return { title, path: `${title}/chapter.txt`, parsed: true, complete: false, audio: false, scriptPath: `${title}/dialogue.json` };
}

function runOptions(chapters: ChapterStatus[], progress: string[] = []) {
  return {
    root: ROOT,
    chapters,
    unknownThreshold: 0.62,
    apiSave: async () => {
      throw new Error('backend fallback must not be used: the in-memory write always succeeds');
    },
    getBackendRootAbsolutePath: () => null,
    onProgress: (message: string) => {
      progress.push(message);
    },
  };
}

function seedBook(): void {
  inMemoryFs.__reset();
  inMemoryFs.__seed({
    // Legacy v2 root store (one cluster: Alice) + a narrator cluster.
    'characters.json': {
      formatVersion: '2.0',
      characters: [
        { id: 'alice', name: 'Alice', gender: 'Female', aliases: ['Al'], color: '#F00', notes: '' },
        { id: 'narrator', name: 'Narrator', gender: 'Unknown', aliases: [], color: null, notes: '' },
      ],
    },
    // Chapter1: legacy non-versioned script (chosenSpeaker names, no characterId slot).
    'Chapter1/chapter.txt': 'Hello there. "Hi back," said Alice. "Hmm," said the warlock.',
    'Chapter1/dialogue.json': {
      chapter: 'Chapter1',
      sourceFile: 'Chapter1/chapter.txt',
      lines: [
        { id: 1, text: 'Hello there.', span: { start: 0, end: 12 }, chosenSpeaker: 'Narrator', candidates: [], isConflict: false },
        { id: 2, text: 'Hi back,', span: { start: 14, end: 22 }, chosenSpeaker: 'Al', candidates: [{ name: 'Al', confidence: 0.9 }], isConflict: false },
        { id: 3, text: 'Hmm,', span: { start: 40, end: 44 }, chosenSpeaker: 'warlock', candidates: [{ name: 'warlock', confidence: 0.8 }, { name: 'they', confidence: 0.3 }], isConflict: false },
      ],
      stats: { numConflicts: 0, numLines: 3 },
    },
    'Chapter1/Chapter1.characters.json': { formatVersion: '2.0', characters: [{ id: 'alice' }, { id: 'pike' }] },
    // Chapter2: already versioned, but keeps a slug the store never had ("warlock").
    'Chapter2/chapter.txt': '"Begone," said the warlock. Alice fled.',
    'Chapter2/dialogue.json': {
      formatVersion: '3.2',
      chapterId: 'Chapter2',
      reviewed: true,
      reviewedAt: '2026-10-01T00:00:00.000Z',
      lines: [
        {
          id: 1, characterId: 'warlock', text: 'Begone,', span: { start: 1, end: 8 },
          metadata: { emotion: null, intensity: 1, pacing: null, prefix: null, customTags: {} },
          candidates: [{ characterId: 'warlock', confidence: 0.9 }, { characterId: 'alice', confidence: 0.2 }],
          isConflict: false,
          attribution: { confidence: 0.9, resolutionStatus: 'user_confirmed', candidates: [{ name: 'warlock', characterId: 'warlock', confidence: 0.9 }] },
        },
        {
          id: 2, characterId: 'alice', text: 'Alice fled.', span: { start: 28, end: 39 },
          metadata: { emotion: null, intensity: 1, pacing: null, prefix: null, customTags: {} },
          candidates: [], isConflict: false,
        },
      ],
      stats: { totalLines: 2, conflicts: 0, characterBreakdown: { warlock: 1, alice: 1 } },
    },
    'Chapter2/Chapter2.characters.json': { formatVersion: '2.0', characters: [{ id: 'alice' }, { id: 'warlock' }] },
    // Chapter3: clean v3-style file after the store migration (GUID-ready refs via the remap).
    'Chapter3/chapter.txt': 'Quiet.',
    'Chapter3/dialogue.json': {
      formatVersion: '3.2',
      chapterId: 'Chapter3',
      lines: [{ id: 1, characterId: null, text: 'Quiet.', span: { start: 0, end: 6 }, metadata: { customTags: {} }, candidates: [], isConflict: false }],
      stats: { totalLines: 1, conflicts: 0, characterBreakdown: {} },
    },
  });
}

function folderByTitle(): Map<string, any> {
  const out = new Map<string, any>();
  for (const key of inMemoryFs.__keys()) {
    if (!key.startsWith('characters/')) continue;
    const doc = inMemoryFs.__readJson(key);
    if (doc?.title) out.set(doc.title, doc);
  }
  return out;
}

describe('bookMigration helpers', () => {
  it('humanizeSlug turns v2 slugs into display titles and leaves names alone', () => {
    expect(humanizeSlug('unknown-women')).toBe('Unknown Women');
    expect(humanizeSlug('warlock')).toBe('Warlock');
    expect(humanizeSlug('Catherine')).toBe('Catherine');
    expect(humanizeSlug('Unknown women')).toBe('Unknown women');
  });

  it('collectStaleDialogueRefs reports every non-GUID, non-narrator, non-null reference once', () => {
    const stale = collectStaleDialogueRefs({
      formatVersion: '3.2',
      chapterId: 'X',
      lines: [
        { id: 1, characterId: 'warlock', text: '', span: null, metadata: {} as any, candidates: [{ characterId: 'they', confidence: 0.1 }], isConflict: false, attribution: { candidates: [{ name: 'w', characterId: 'warlock', confidence: 0.9 }] } as any },
        { id: 2, characterId: G_ALICE, text: '', span: null, metadata: {} as any, candidates: [{ characterId: 'narrator', confidence: 0.1 }], isConflict: false },
        { id: 3, characterId: null, text: '', span: null, metadata: {} as any, candidates: [], isConflict: false },
      ],
      stats: { totalLines: 3, conflicts: 0, characterBreakdown: { [G_ALICE]: 1, warlock: 1 } },
    });
    expect(stale).toEqual(['they', 'warlock']);
  });
});

describe('migrateBookToV3', () => {
  beforeEach(() => {
    seedBook();
  });

  it('migrates the store, rewrites legacy + stale dialogue as v3.2 with GUIDs, creates speakers that had no character, drops candidate-only names, rebuilds rosters, and backs up pre-images', async () => {
    const before = inMemoryFs.__snapshot();
    const progress: string[] = [];

    const report = await migrateBookToV3(runOptions([chapter('Chapter1'), chapter('Chapter2'), chapter('Chapter3')], progress));

    expect(report.ok).toBe(true);
    expect(report.abortReason).toBeUndefined();

    // Pass 1: store → folder.
    expect(report.characters.status).toBe('migrated');
    expect(report.characters.createdFiles).toBe(2);
    expect(inMemoryFs.__has('characters.json')).toBe(false);

    const folder = folderByTitle();
    expect([...folder.keys()].sort()).toEqual(['Alice', 'Narrator', 'Warlock']);
    const alice = folder.get('Alice');
    const narrator = folder.get('Narrator');
    const warlock = folder.get('Warlock');
    for (const doc of [alice, narrator, warlock]) {
      expect(doc.formatVersion).toBe('3.0');
      expect(doc.guid).toMatch(CHARACTER_GUID_PATTERN);
    }
    // The newly created speaker is a plain v3 record, Unknown gender.
    expect(report.dialogue.createdCharacters).toEqual(['Warlock']);
    expect(warlock.gender).toBe('Unknown');
    // "they" was only ever a candidate: no character, listed in the report.
    expect(report.dialogue.droppedCandidateNames).toEqual(['they']);
    expect(report.dialogue.unresolvedNames).toEqual([]);

    // Pass 2: dialogue.
    expect(report.dialogue.scanned).toBe(3);
    expect(report.dialogue.migrated).toEqual(['Chapter1', 'Chapter2']);
    expect(report.dialogue.skipped).toBe(1);
    expect(report.dialogue.failed).toEqual([]);

    const ch1 = inMemoryFs.__readJson('Chapter1/dialogue.json');
    expect(ch1.formatVersion).toBe('3.2');
    expect(ch1.chapterId).toBe('Chapter1');
    expect(ch1.lines.map((line: any) => line.characterId)).toEqual([narrator.guid, alice.guid, warlock.guid]);
    // Alias "Al" resolved to the Alice cluster; candidate ids are GUIDs; "they" dropped.
    expect(ch1.lines[1].candidates).toEqual([{ characterId: alice.guid, confidence: 0.9 }]);
    expect(ch1.lines[2].candidates).toEqual([{ characterId: warlock.guid, confidence: 0.8 }]);
    // attribution.candidates is name-based provenance: "they" stays, with a null id.
    expect(ch1.lines[2].attribution.candidates.map((c: any) => [c.name, c.characterId])).toEqual([['Warlock', warlock.guid], ['they', null]]);
    expect(ch1.stats.characterBreakdown).toEqual({ [narrator.guid]: 1, [alice.guid]: 1, [warlock.guid]: 1 });
    expect(collectStaleDialogueRefs(ch1)).toEqual([]);

    const ch2 = inMemoryFs.__readJson('Chapter2/dialogue.json');
    expect(ch2.formatVersion).toBe('3.2');
    expect(ch2.lines.map((line: any) => line.characterId)).toEqual([warlock.guid, alice.guid]);
    expect(ch2.lines[0].candidates.map((c: any) => c.characterId).sort()).toEqual([alice.guid, warlock.guid].sort());
    expect(ch2.stats.characterBreakdown).toEqual({ [warlock.guid]: 1, [alice.guid]: 1 });
    // Review state survives the rewrite.
    expect(ch2.reviewed).toBe(true);
    expect(ch2.reviewedAt).toBe('2026-10-01T00:00:00.000Z');
    expect(ch2.reviewedLineCount).toBe(2);
    expect(collectStaleDialogueRefs(ch2)).toEqual([]);

    // Rosters: GUID refs only; stale "pike" dropped; migrated speakers added.
    const roster1 = inMemoryFs.__readJson('Chapter1/Chapter1.characters.json');
    expect(roster1.formatVersion).toBe('3.0');
    expect(roster1.characters.map((entry: any) => entry.id).sort()).toEqual([alice.guid, narrator.guid, warlock.guid].sort());
    const roster2 = inMemoryFs.__readJson('Chapter2/Chapter2.characters.json');
    expect(roster2.characters.map((entry: any) => entry.id).sort()).toEqual([alice.guid, warlock.guid].sort());
    expect(report.dialogue.staleRosterEntriesDropped).toBe(1);
    expect(report.dialogue.rostersRewritten).toBe(2);

    // Untouched clean chapter.
    expect(inMemoryFs.__readText('Chapter3/dialogue.json')).toBe(before['Chapter3/dialogue.json']);

    // Backups: dialogue pre-images under _backups/dialogue-v3/<ts>/.
    expect(report.backupDir).toMatch(/^_backups\/dialogue-v3\/\d{8}T\d{6}Z$/);
    const backup1 = inMemoryFs.__readJson(`${report.backupDir}/Chapter1/dialogue.json`);
    expect(backup1.lines[2].chosenSpeaker).toBe('warlock');
    // Pre-image as of the dialogue pass: the store migration already remapped "alice" (its own backup holds the v2 original).
    expect(inMemoryFs.__readJson(`${report.backupDir}/Chapter1/Chapter1.characters.json`).characters).toEqual([{ id: alice.guid }, { id: 'pike' }]);
    expect(inMemoryFs.__has(`${report.backupDir}/Chapter3/dialogue.json`)).toBe(false);

    expect(progress.some((message) => message.includes('Chapter1/dialogue.json'))).toBe(true);
  });

  it('is idempotent: a second run reports everything current and writes nothing', async () => {
    const chapters = [chapter('Chapter1'), chapter('Chapter2'), chapter('Chapter3')];
    const first = await migrateBookToV3(runOptions(chapters));
    expect(first.ok).toBe(true);

    const before = inMemoryFs.__snapshot();
    const second = await migrateBookToV3(runOptions(chapters));

    expect(second.ok).toBe(true);
    expect(second.characters.status).toBe('already-v3');
    expect(second.dialogue.migrated).toEqual([]);
    expect(second.dialogue.skipped).toBe(3);
    expect(second.dialogue.createdCharacters).toEqual([]);
    expect(second.dialogue.rostersRewritten).toBe(0);
    expect(second.backupDir).toBeNull();
    expect(inMemoryFs.__snapshot()).toEqual(before);
  });

  it('aborts without touching dialogue when the character migration is in flight (folder AND legacy store present)', async () => {
    inMemoryFs.__seed({
      'characters/Alice.json': makeCharacterFile({ guid: G_ALICE, title: 'Alice' }),
    });
    const before = inMemoryFs.__snapshot();

    const report = await migrateBookToV3(runOptions([chapter('Chapter1'), chapter('Chapter2')]));

    expect(report.ok).toBe(false);
    expect(report.characters.status).toBe('aborted');
    expect(report.abortReason).toContain('in flight');
    expect(report.dialogue.scanned).toBe(0);
    expect(inMemoryFs.__snapshot()).toEqual(before);
  });

  it('on a v3 book with no legacy store, only the stale dialogue/rosters are rewritten', async () => {
    inMemoryFs.__reset();
    inMemoryFs.__seed({
      'characters/Alice.json': makeCharacterFile({ guid: G_ALICE, title: 'Alice' }),
      'characters/Narrator.json': makeCharacterFile({ guid: G_NARRATOR, title: 'Narrator' }),
      'Chapter1/chapter.txt': '"Hi," said Alice.',
      'Chapter1/dialogue.json': {
        chapter: 'Chapter1',
        sourceFile: 'Chapter1/chapter.txt',
        lines: [{ id: 1, text: 'Hi,', span: { start: 1, end: 4 }, chosenSpeaker: 'Alice', candidates: [], isConflict: false }],
        stats: { numConflicts: 0, numLines: 1 },
      },
      'Chapter2/dialogue.json': {
        formatVersion: '3.2',
        chapterId: 'Chapter2',
        lines: [{ id: 1, characterId: G_NARRATOR, text: 'Calm.', span: { start: 0, end: 5 }, metadata: { customTags: {} }, candidates: [], isConflict: false }],
        stats: { totalLines: 1, conflicts: 0, characterBreakdown: { [G_NARRATOR]: 1 } },
      },
      'Chapter2/Chapter2.characters.json': { formatVersion: '3.0', characters: [{ id: G_NARRATOR }, { id: 'emperor' }] },
    });
    const before = inMemoryFs.__snapshot();

    const report = await migrateBookToV3(runOptions([chapter('Chapter1'), chapter('Chapter2')]));

    expect(report.ok).toBe(true);
    expect(report.characters.status).toBe('already-v3');
    expect(report.dialogue.migrated).toEqual(['Chapter1']);
    expect(report.dialogue.createdCharacters).toEqual([]);

    const ch1 = inMemoryFs.__readJson('Chapter1/dialogue.json');
    expect(ch1.formatVersion).toBe('3.2');
    expect(ch1.lines[0].characterId).toBe(G_ALICE);

    // Chapter2 dialogue untouched; its roster loses only the stale slug.
    expect(inMemoryFs.__readText('Chapter2/dialogue.json')).toBe(before['Chapter2/dialogue.json']);
    expect(inMemoryFs.__readJson('Chapter2/Chapter2.characters.json').characters).toEqual([{ id: G_NARRATOR }]);
    expect(report.dialogue.staleRosterEntriesDropped).toBe(1);
    expect(inMemoryFs.__has(`${report.backupDir}/Chapter2/Chapter2.characters.json`)).toBe(true);
  });
});
