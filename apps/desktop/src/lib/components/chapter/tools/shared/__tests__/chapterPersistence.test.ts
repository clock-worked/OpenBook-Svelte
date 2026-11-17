// ============================================================================
// Owner-level tests for chapterPersistence (chapter save + load), stubbed at
// the fs seam with the in-memory fs (services/__tests__/helpers/inMemoryFs.ts).
// ============================================================================

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { inMemoryFs } from '$lib/services/__tests__/helpers/inMemoryFs';
import { diffTree, treePaths } from '$lib/services/__tests__/helpers/diffTree';
import { makeCharacterFile } from '$lib/services/__tests__/helpers/fixtures';

vi.mock('$lib/services/fs', async () => {
    const mod = await import('$lib/services/__tests__/helpers/inMemoryFs');
    return mod.inMemoryFs;
});

import {
    applyStatsAfterDialogueSave,
    DIALOGUE_FORMAT_VERSIONS,
    isVersionedDialogue,
    loadChapterContent,
    recordLineAudioGeneration,
    saveDialogueFromNormalized,
    writeChapterDialogue,
} from '../chapterPersistence';

const ROOT = 'Book';
const G_RASHID = 'YY3GE4HN';
const G_JAHA = 'SJH4X2QK';

function seedRoster(): void {
    inMemoryFs.__seed({
        'characters/Rashid.json': makeCharacterFile({ guid: G_RASHID, title: 'Rashid', aliases: ['Rash'] }),
        'characters/Sergeant Jaha.json': makeCharacterFile({ guid: G_JAHA, title: 'Sergeant Jaha' }),
    });
}

function line(overrides: Record<string, unknown> = {}) {
    return {
        id: 1,
        text: 'Hold the line.',
        span: { start: 0, end: 5 },
        characterName: 'Rashid',
        candidates: [
            { name: 'Rashid', characterId: G_RASHID, confidence: 0.9, reasons: ['recent_named_mention'] },
            { name: 'Sergeant Jaha', characterId: G_JAHA, confidence: 0.4, reasons: ['speech_verb'] },
        ],
        isConflict: false,
        isNonSpeaker: false,
        isReturning: false,
        attribution: null,
        ...overrides,
    } as any;
}

function save(lines: any[], extra: Record<string, unknown> = {}) {
    return saveDialogueFromNormalized({
        normalized: { lines, stats: {} },
        chapter: { title: 'Chapter 1', parsed: true },
        root: ROOT,
        rawText: null,
        unknownThreshold: 0.5,
        unknownSpeakerLabel: 'Unknown',
        ...extra,
    });
}

beforeEach(() => {
    inMemoryFs.__reset();
    seedRoster();
});

describe('saveDialogueFromNormalized', () => {
    it('writes the chapter dialogue.json and nothing else', async () => {
        const before = inMemoryFs.__snapshot();

        expect(await save([line()])).toBe(true);

        expect(treePaths(diffTree(before, inMemoryFs.__snapshot()))).toEqual(['Chapter 1/dialogue.json']);
        const saved = inMemoryFs.__readJson('Chapter 1/dialogue.json');
        expect(saved).toMatchObject({ formatVersion: '3.2', chapterId: 'Chapter 1', reviewed: false });
        expect(saved.lines[0]).toMatchObject({ id: 1, characterId: G_RASHID });
        expect(saved.stats.characterBreakdown).toEqual({ [G_RASHID]: 1 });
    });

    it('resolves names through the roster (aliases included) via the fs seam', async () => {
        expect(await save([line({ characterName: 'Rash', candidates: [] })])).toBe(true);
        expect(inMemoryFs.__readJson('Chapter 1/dialogue.json').lines[0].characterId).toBe(G_RASHID);
    });

    it('persists per-candidate reasons in the saved attribution', async () => {
        expect(await save([line()])).toBe(true);

        const saved = inMemoryFs.__readJson('Chapter 1/dialogue.json');
        const rashid = saved.lines[0].attribution.candidates.find((c: any) => c.name === 'Rashid');
        expect(rashid.reasons).toEqual(['recent_named_mention']);
        expect(saved.lines[0].candidates.map((c: any) => c.characterId)).toEqual([G_RASHID, G_JAHA]);
    });

    it('keeps per-line audio metadata that exists only on disk', async () => {
        const record = { provider: 'test', generatedAt: '2026-11-01T00:00:00.000Z' };
        inMemoryFs.__seed({
            'Chapter 1/dialogue.json': {
                formatVersion: '3.2',
                chapterId: 'Chapter 1',
                lines: [{ id: 1, text: 'Hold the line.', audioGeneration: record, readAs: 'Hold. The. Line.' }],
            },
        });

        expect(await save([line()])).toBe(true);

        const saved = inMemoryFs.__readJson('Chapter 1/dialogue.json');
        expect(saved.lines[0].audioGeneration).toEqual(record);
        expect(saved.lines[0].readAs).toBe('Hold. The. Line.');
    });

    it('records the reviewed flag only on a reviewed save', async () => {
        expect(await save([line()], { reviewed: true, reviewedAt: '2026-11-17T00:00:00.000Z' })).toBe(true);
        expect(inMemoryFs.__readJson('Chapter 1/dialogue.json')).toMatchObject({
            reviewed: true,
            reviewedAt: '2026-11-17T00:00:00.000Z',
            reviewedLineCount: 1,
        });
    });
});

describe('dialogue save stats step (ADR 0012)', () => {
    const payload = { formatVersion: '3.2', chapterId: 'Chapter 1', lines: [] } as any;

    it('asks the sidecar for the delta after a File System Access write', async () => {
        inMemoryFs.__setDialogueSaveRoute('handle');

        expect(await writeChapterDialogue('Chapter 1/dialogue.json', payload)).toBe(true);
        expect(inMemoryFs.__statsDeltaCalls()).toEqual(['Chapter 1/dialogue.json']);
    });

    it('does nothing more when the sidecar endpoint already applied it', async () => {
        expect(await writeChapterDialogue('Chapter 1/dialogue.json', payload)).toBe(true);
        expect(inMemoryFs.__statsDeltaCalls()).toEqual([]);
    });

    it('does nothing for an unchanged (skipped) write', () => {
        applyStatsAfterDialogueSave('Chapter 1/dialogue.json', 'unchanged');
        expect(inMemoryFs.__statsDeltaCalls()).toEqual([]);
    });

    it('runs on a chapter save', async () => {
        inMemoryFs.__setDialogueSaveRoute('handle');

        expect(await save([line()])).toBe(true);
        expect(inMemoryFs.__statsDeltaCalls()).toEqual(['Chapter 1/dialogue.json']);
    });

    it('keeps audio-metadata stamps off the stats path', async () => {
        inMemoryFs.__setDialogueSaveRoute('handle');
        inMemoryFs.__seed({
            'Chapter 1/dialogue.json': { formatVersion: '3.2', chapterId: 'Chapter 1', lines: [{ id: 1, text: 'Hi.' }] },
        });

        await recordLineAudioGeneration('Chapter 1', [1], { provider: 'test' } as any);

        expect(inMemoryFs.__readJson('Chapter 1/dialogue.json').lines[0].audioGeneration).toEqual({ provider: 'test' });
        expect(inMemoryFs.__statsDeltaCalls()).toEqual([]);
    });
});

describe('loadChapterContent', () => {
    const chapter = { title: 'Chapter 1', path: 'Chapter 1/chapter.txt', parsed: true };

    function seedChapter(dialogue: unknown): void {
        inMemoryFs.__seed({
            // The in-memory readCentralCharacters serves the legacy root file.
            'characters.json': { formatVersion: '2.0', characters: [{ id: G_RASHID, name: 'Rashid', aliases: [] }] },
            'Chapter 1/chapter.txt': '\u201cHold the line.\u201d',
            'Chapter 1/dialogue.json': dialogue,
        });
    }

    it.each([...DIALOGUE_FORMAT_VERSIONS])('reads a %s dialogue.json as versioned', async (version) => {
        seedChapter({
            formatVersion: version,
            chapterId: 'Chapter 1',
            reviewed: true,
            reviewedAt: '2026-11-17T00:00:00.000Z',
            lines: [{ id: 1, characterId: G_RASHID, text: 'Hold the line.', span: { start: 1, end: 15 }, candidates: [] }],
            stats: { totalLines: 1, conflicts: 0, characterBreakdown: {} },
        });

        const loaded = await loadChapterContent({ chapter, root: ROOT, unknownThreshold: 0.5 });

        expect(loaded.isV2Format).toBe(true);
        expect(loaded.normalized?.lines[0].characterName).toBe('Rashid');
        expect(loaded.currentScript?.lines[0].chosenSpeaker).toBe('Rashid');
        expect(loaded.rawText).toBe('\u201cHold the line.\u201d');
        expect(loaded).toMatchObject({ reviewed: true, reviewedAt: '2026-11-17T00:00:00.000Z' });
    });

    it('falls back to the v1 script for an unlisted format version', async () => {
        seedChapter({ formatVersion: '9.9', chapterId: 'Chapter 1', lines: [] });
        inMemoryFs.__seed({
            'Chapter 1/Chapter 1.script.json': {
                chapter: 'Chapter 1',
                sourceFile: 'Chapter 1/chapter.txt',
                lines: [{ id: 1, text: 'Hold the line.', span: { start: 1, end: 15 }, chosenSpeaker: 'Rashid', candidates: [], isConflict: false }],
                stats: { numLines: 1, numConflicts: 0 },
            },
        });

        const loaded = await loadChapterContent({ chapter, root: ROOT, unknownThreshold: 0.5 });

        expect(loaded.isV2Format).toBe(false);
        expect(loaded.normalized?.lines[0].characterName).toBe('Rashid');
        expect(loaded.reviewed).toBe(false);
    });

    it('skips the dialogue read for an unparsed chapter', async () => {
        seedChapter({ formatVersion: '3.2', chapterId: 'Chapter 1', lines: [] });

        const loaded = await loadChapterContent({ chapter: { ...chapter, parsed: false }, root: ROOT, unknownThreshold: 0.5 });

        expect(loaded.normalized).toBeNull();
        expect(loaded.currentScript).toBeNull();
        expect(loaded.rawText).toBe('\u201cHold the line.\u201d');
    });
});

describe('dialogue format-version list (single source)', () => {
    it('lists the versions the app reads as versioned dialogue', () => {
        expect([...DIALOGUE_FORMAT_VERSIONS]).toEqual(['2.0', '3.0', '3.1', '3.2']);
    });

    it('classifies documents by the list', () => {
        for (const version of DIALOGUE_FORMAT_VERSIONS) {
            expect(isVersionedDialogue({ formatVersion: version, lines: [] })).toBe(true);
        }
        expect(isVersionedDialogue({ formatVersion: '1.0', lines: [] })).toBe(false);
        expect(isVersionedDialogue({ chapter: 'Chapter 1', lines: [] })).toBe(false);
        expect(isVersionedDialogue(null)).toBe(false);
    });
});
