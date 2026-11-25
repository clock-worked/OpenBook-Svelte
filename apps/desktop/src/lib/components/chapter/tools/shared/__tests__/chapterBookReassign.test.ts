// Book-wide character reassign / usage count on v3.2 chapters.
//
// Deleting a book character reassigns its lines across the book (to Unknown
// or a typed replacement). The panel used to match v1 `chosenSpeaker` names,
// which a v3.2 dialogue.json does not have, so v3 lines kept pointing at the
// removed character's id and the usage prompt under-counted. These tests pin
// the id-based read-modify-write in the persistence owner.
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/services/fs', async () => (await import('$lib/services/__tests__/helpers/inMemoryFs')).inMemoryFs);

import { inMemoryFs } from '$lib/services/__tests__/helpers/inMemoryFs';
import { countCharacterIdInDialogue, reassignCharacterIdInDialogue } from '../chapterPersistence';

const ALICE = 'ALICE001';
const BOB = 'BOB00002';
const CAROL = 'CAROL003';
const STAMP = { generator: 'kokoro', voice: 'af_bella', generatedAt: '2026-11-01T00:00:00Z' };

function line(id: number, characterId: string | null, candidates: Array<[string, number]>, extra: Record<string, unknown> = {}) {
    return {
        id,
        characterId,
        text: `"Line ${id}."`,
        span: { start: id * 10, end: id * 10 + 9 },
        metadata: { emotion: null, intensity: 1, pacing: null, prefix: null, customTags: { sourceAlias: 'x' } },
        candidates: candidates.map(([cid, confidence]) => ({ characterId: cid, confidence })),
        isConflict: false,
        isNonSpeaker: false,
        attribution: {
            confidence: 0.9,
            resolutionStatus: 'resolved',
            candidates: candidates.map(([cid, confidence]) => ({ characterId: cid, name: cid, confidence })),
        },
        audioGeneration: null,
        readAs: null,
        ...extra,
    };
}

function chapterDoc(chapterId: string, lines: any[]) {
    return { formatVersion: '3.2', chapterId, reviewed: true, reviewedAt: '2026-11-02T00:00:00Z', lines, stats: { totalLines: lines.length, conflicts: 0, characterBreakdown: {} } };
}

function seedBook() {
    inMemoryFs.__seed({
        'Ch1/dialogue.json': chapterDoc('Ch1', [
            line(1, ALICE, [[ALICE, 0.9]], { audioGeneration: STAMP, readAs: 'Hel-lo.' }),
            line(2, BOB, [[BOB, 0.8], [ALICE, 0.3]]),
            line(3, ALICE, [[ALICE, 0.6], [BOB, 0.5]], { readAs: 'Say it slow.' }),
        ]),
        'Ch2/dialogue.json': chapterDoc('Ch2', [
            line(1, ALICE, [[ALICE, 0.7], [CAROL, 0.6]]),
            line(2, CAROL, [[CAROL, 0.9]]),
        ]),
        'Ch3/Ch3.script.json': { chapter: 'Ch3', lines: [{ id: 1, chosenSpeaker: 'Alice', candidates: [] }] },
    });
}

function readDoc(title: string) {
    return JSON.parse(inMemoryFs.__snapshot()[`${title}/dialogue.json`]);
}

describe('book-wide reassign on v3.2 dialogue', () => {
    beforeEach(() => {
        inMemoryFs.__reset();
        seedBook();
    });

    it('counts lines by characterId and returns null for a chapter without versioned dialogue', async () => {
        expect(await countCharacterIdInDialogue('Ch1', ALICE)).toBe(2);
        expect(await countCharacterIdInDialogue('Ch2', ALICE)).toBe(1);
        expect(await countCharacterIdInDialogue('Ch3', ALICE)).toBeNull();
    });

    it('reassigns ids to a named replacement and keeps every other field on disk', async () => {
        expect(await reassignCharacterIdInDialogue('Ch1', ALICE, BOB)).toBe(2);
        const doc = readDoc('Ch1');

        expect(doc.formatVersion).toBe('3.2');
        expect(doc.reviewed).toBe(true);
        expect(doc.reviewedAt).toBe('2026-11-02T00:00:00Z');
        expect(doc.lines.map((l: any) => l.characterId)).toEqual([BOB, BOB, BOB]);

        // Preserved per-line metadata.
        expect(doc.lines[0].audioGeneration).toEqual(STAMP);
        expect(doc.lines[0].readAs).toBe('Hel-lo.');
        expect(doc.lines[2].readAs).toBe('Say it slow.');
        expect(doc.lines[0].metadata.customTags.sourceAlias).toBe('x');

        // Candidates remapped; a duplicate BOB merges at the higher confidence.
        expect(doc.lines[0].candidates).toEqual([{ characterId: BOB, confidence: 0.9 }]);
        expect(doc.lines[1].candidates).toEqual([{ characterId: BOB, confidence: 0.8 }]);
        expect(doc.lines[2].candidates).toEqual([{ characterId: BOB, confidence: 0.6 }]);
        expect(doc.lines[0].attribution.candidates[0].characterId).toBe(BOB);
        expect(doc.lines.some((l: any) => l.candidates.some((c: any) => c.characterId === ALICE))).toBe(false);

        expect(doc.lines.every((l: any) => l.isConflict === false)).toBe(true);
        expect(doc.stats).toMatchObject({ totalLines: 3, conflicts: 0, characterBreakdown: { [BOB]: 3 } });
    });

    it('reassigns to Unknown (null), drops the old candidate and flags the line for review', async () => {
        expect(await reassignCharacterIdInDialogue('Ch2', ALICE, null)).toBe(1);
        const doc = readDoc('Ch2');

        expect(doc.lines[0].characterId).toBeNull();
        expect(doc.lines[0].isConflict).toBe(true);
        expect(doc.lines[0].candidates).toEqual([{ characterId: CAROL, confidence: 0.6 }]);
        expect(doc.lines[1].characterId).toBe(CAROL);
        expect(doc.stats).toMatchObject({ totalLines: 2, conflicts: 1, characterBreakdown: { [CAROL]: 1 } });
    });

    it('leaves the legacy v1 chapter to the caller and does not write untouched chapters', async () => {
        const before = inMemoryFs.__snapshot();
        expect(await reassignCharacterIdInDialogue('Ch3', ALICE, BOB)).toBeNull();
        expect(await reassignCharacterIdInDialogue('Ch2', BOB, CAROL)).toBe(0);
        expect(inMemoryFs.__snapshot()).toEqual(before);
    });
});
