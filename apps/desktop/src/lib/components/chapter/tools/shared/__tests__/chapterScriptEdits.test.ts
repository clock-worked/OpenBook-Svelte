// Character-panel speaker edits on a chapter that has a v3.2 dialogue.json.
//
// The panel edits the loader's v1 `currentScript` view (chosenSpeaker /
// candidates / isConflict). Writing that view straight to `ch.scriptPath`
// (= `<Ch>/dialogue.json`) replaced the v3.2 document with a v1 one, dropping
// characterId, audioGeneration and readAs (ADR 0003: write current schema
// only). ChapterView's follow-up sync save re-wrote v3.2, but it re-reads
// disk metadata *after* the v1 write, so stamps recorded after load were
// lost for good. These tests pin the persistence owner's contract instead.
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/services/fs', async () => (await import('$lib/services/__tests__/helpers/inMemoryFs')).inMemoryFs);

import type { ScriptJson } from '$lib/types';
import { inMemoryFs } from '$lib/services/__tests__/helpers/inMemoryFs';
import {
    loadChapterContent,
    recordLineAudioGeneration,
    saveDialogueFromNormalized,
    writeChapterScriptEdits,
} from '../chapterPersistence';
import { normalizeAttribution, normalizeScriptData } from '../chapterNormalization';
import { mergeNormalizedFromLegacy } from '../chapterStateSync';

const TITLE = 'Ch1';
const RAW = 'Alice said, "Hello." Bob said, "Hi there."';
const ROSTER = [
    { id: 'ALICE001', name: 'Alice', aliases: [] },
    { id: 'BOB00002', name: 'Bob', aliases: ['Robert'] },
];
const nameToId = new Map([
    ['alice', 'ALICE001'],
    ['bob', 'BOB00002'],
    ['robert', 'BOB00002'],
]);
const STAMP = { generator: 'kokoro', voice: 'af_bella', generatedAt: '2026-11-01T00:00:00Z' } as any;
const LATE_STAMP = { generator: 'xtts', voice: 'bob', generatedAt: '2026-11-20T00:00:00Z' } as any;

function v32Line(id: number, characterId: string, text: string, start: number) {
    return {
        id,
        characterId,
        text,
        span: { start, end: start + text.length },
        metadata: { emotion: null, intensity: 1, pacing: null, prefix: null, customTags: {} },
        candidates: [{ characterId, confidence: 0.9 }],
        isConflict: false,
        isNonSpeaker: false,
        audioGeneration: id === 1 ? STAMP : null,
        readAs: id === 1 ? 'Hel-lo.' : null,
    };
}

function seedBook() {
    inMemoryFs.__seed({
        'characters.json': { formatVersion: '2.0', characters: ROSTER },
        [`${TITLE}/${TITLE}.txt`]: RAW,
        [`${TITLE}/dialogue.json`]: {
            formatVersion: '3.2',
            chapterId: TITLE,
            reviewed: false,
            lines: [v32Line(1, 'ALICE001', '"Hello."', 11), v32Line(2, 'BOB00002', '"Hi there."', 30)],
            stats: { totalLines: 2, conflicts: 0, characterBreakdown: { ALICE001: 1, BOB00002: 1 } },
        },
    });
}

const chapter = { title: TITLE, path: `${TITLE}/${TITLE}.txt`, parsed: true, scriptPath: `${TITLE}/dialogue.json` };

async function load() {
    return loadChapterContent({
        chapter,
        root: 'book',
        unknownThreshold: 0.5,
        unknownSpeakerLabel: 'Unknown',
        readDialogueForChapter: inMemoryFs.readDialogueForChapter,
        normalizeScriptData,
        readCentralCharacters: inMemoryFs.readCentralCharacters,
        buildIdToNameMap: (chars: any[]) => new Map(chars.map((c) => [String(c.id).toLowerCase(), c.name])),
        readTextFile: inMemoryFs.readTextFile,
    });
}

/** ChapterView's reaction to `currentScript.set(...)`: merge + save. */
async function chapterViewSyncSave(normalized: any, script: ScriptJson) {
    const merged = mergeNormalizedFromLegacy({ legacy: script, normalized, normalizeAttribution, unknownThreshold: 0.5 });
    if (!merged) return;
    await saveDialogueFromNormalized({
        normalized: merged,
        chapter,
        rawText: RAW,
        unknownThreshold: 0.5,
        unknownSpeakerLabel: 'Unknown',
        repo: {
            ensureCentralCharactersForNames: async () => {},
            ensureCharacterNameToIdMap: async () => nameToId,
            normalizeAttribution,
        },
    });
}

function disk() {
    return inMemoryFs.__readJson(`${TITLE}/dialogue.json`) as any;
}

describe('writeChapterScriptEdits — chapter with a v3.2 dialogue.json', () => {
    beforeEach(() => {
        inMemoryFs.__reset();
        seedBook();
    });

    it('set speaker: edits characterId in place and keeps the v3.2 document', async () => {
        const loaded = await load();
        const scr = loaded.currentScript!;
        expect(scr.lines[0].chosenSpeaker).toBe('Alice');

        scr.lines[0].chosenSpeaker = 'Bob';
        scr.lines[0].isConflict = false;
        expect(await writeChapterScriptEdits({ chapter, script: scr, nameToId })).toBe(true);

        const doc = disk();
        expect(doc.formatVersion).toBe('3.2');
        expect(doc.lines[0].characterId).toBe('BOB00002');
        expect(doc.lines[0].chosenSpeaker).toBeUndefined();
        expect(doc.lines[0].audioGeneration).toEqual(STAMP);
        expect(doc.lines[0].readAs).toBe('Hel-lo.');
        expect(doc.lines[0].metadata).toBeDefined();
        expect(doc.lines[1].characterId).toBe('BOB00002');
        expect(doc.stats.characterBreakdown).toEqual({ BOB00002: 2 });
    });

    it('keeps a stamp recorded after load through the follow-up ChapterView save', async () => {
        const loaded = await load();
        // Audio generated for line 2 after the chapter view loaded: disk only.
        await recordLineAudioGeneration(TITLE, [2], LATE_STAMP);

        const scr = loaded.currentScript!;
        scr.lines[1].chosenSpeaker = 'Alice';
        await writeChapterScriptEdits({ chapter, script: scr, nameToId });
        await chapterViewSyncSave(loaded.normalized, scr);

        const doc = disk();
        expect(doc.formatVersion).toBe('3.2');
        expect(doc.lines[1].characterId).toBe('ALICE001');
        expect(doc.lines[1].audioGeneration).toEqual(LATE_STAMP);
        expect(doc.lines[0].audioGeneration).toEqual(STAMP);
        expect(doc.lines[0].readAs).toBe('Hel-lo.');
    });

    it('remove character: clears characterId and marks the line a conflict', async () => {
        const loaded = await load();
        const scr = loaded.currentScript!;
        scr.lines[0].chosenSpeaker = null;
        scr.lines[0].isConflict = true;
        await writeChapterScriptEdits({ chapter, script: scr, nameToId });

        const doc = disk();
        expect(doc.formatVersion).toBe('3.2');
        expect(doc.lines[0].characterId).toBeNull();
        expect(doc.lines[0].isConflict).toBe(true);
        expect(doc.lines[0].audioGeneration).toEqual(STAMP);
    });

    it('reviewed lines: an explicit characterId and non-speaker flag win', async () => {
        const loaded = await load();
        const scr = loaded.currentScript!;
        (scr.lines[0] as any).characterId = 'BOB00002';
        scr.lines[0].chosenSpeaker = 'Robert';
        (scr.lines[1] as any).characterId = null;
        scr.lines[1].chosenSpeaker = null;
        scr.lines[1].isNonSpeaker = true;
        await writeChapterScriptEdits({ chapter, script: scr, nameToId });

        const doc = disk();
        expect(doc.lines[0].characterId).toBe('BOB00002');
        expect(doc.lines[1].characterId).toBeNull();
        expect(doc.lines[1].isNonSpeaker).toBe(true);
        expect(doc.lines[1].isConflict).toBe(false);
        expect(doc.lines[1].candidates).toEqual([]);
    });

    it('an unresolved name leaves the line\'s characterId as it was', async () => {
        const loaded = await load();
        const scr = loaded.currentScript!;
        scr.lines[0].chosenSpeaker = 'Nobody Known';
        await writeChapterScriptEdits({ chapter, script: scr, nameToId });
        expect(disk().lines[0].characterId).toBe('ALICE001');
    });
});

describe('writeChapterScriptEdits — chapter with no dialogue.json', () => {
    beforeEach(() => inMemoryFs.__reset());

    it('keeps writing the v1 script for a legacy chapter', async () => {
        const script: ScriptJson = {
            chapter: 'Old',
            sourceFile: 'Old/Old.txt',
            lines: [{ id: 1, text: 'x', span: null, chosenSpeaker: 'Alice', candidates: [], isConflict: false }],
        } as any;
        const legacy = { title: 'Old', scriptPath: 'Old/Old.script.json' };
        expect(await writeChapterScriptEdits({ chapter: legacy, script, nameToId })).toBe(true);
        expect(inMemoryFs.__readJson('Old/Old.script.json')).toEqual(script);
        expect(inMemoryFs.__has('Old/dialogue.json')).toBe(false);
    });
});
