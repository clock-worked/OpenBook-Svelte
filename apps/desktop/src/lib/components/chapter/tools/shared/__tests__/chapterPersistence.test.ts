import { describe, expect, it, vi } from 'vitest';

vi.mock('$lib/services/fs', () => ({
    writeDialogue: vi.fn(async () => true),
    writeDialogueSave: vi.fn(async () => 'sidecar'),
    applyDialogueStatsDelta: vi.fn(async () => true),
    readOptionalDialogue: vi.fn(async () => null)
}));

import { applyDialogueStatsDelta, readOptionalDialogue, writeDialogue, writeDialogueSave } from '$lib/services/fs';
import { recordLineAudioGeneration, saveDialogueFromNormalized, writeChapterDialogue } from '../chapterPersistence';
import { normalizeAttribution } from '../chapterNormalization';

const mockWriteDialogue = vi.mocked(writeDialogueSave);

describe('saveDialogueFromNormalized — candidate reasons', () => {
    it('persists per-candidate reasons in the saved attribution', async () => {
        mockWriteDialogue.mockClear();

        const lines = [
            {
                id: 1,
                text: 'Hold the line.',
                span: { start: 0, end: 5 },
                characterName: 'Rashid',
                candidates: [
                    { name: 'Rashid', characterId: 'YY3GE4HN', confidence: 0.9, reasons: ['recent_named_mention'] },
                    { name: 'Sergeant Jaha', characterId: 'SJH4X2QK', confidence: 0.4, reasons: ['speech_verb'] }
                ],
                isConflict: false,
                isNonSpeaker: false,
                isReturning: false,
                attribution: null
            }
        ] as any;

        const repo = {
            ensureCharacterNameToIdMap: async () =>
                new Map([
                    ['rashid', 'YY3GE4HN'],
                    ['sergeant jaha', 'SJH4X2QK']
                ]),
            normalizeAttribution
        };

        const saved = await saveDialogueFromNormalized({
            normalized: { lines, stats: {} },
            chapter: { title: 'Chapter 1', parsed: true },
            rawText: null,
            unknownThreshold: 0.5,
            unknownSpeakerLabel: 'Unknown',
            repo: repo as any
        });

        expect(saved).toBe(true);
        expect(mockWriteDialogue).toHaveBeenCalledTimes(1);
        const payload = mockWriteDialogue.mock.calls[0][1] as any;
        const rashid = payload.lines[0].attribution.candidates.find((c: any) => c.name === 'Rashid');
        expect(rashid.reasons).toEqual(['recent_named_mention']);
    });
});

describe('dialogue save stats step (ADR 0012)', () => {
    const payload = { formatVersion: '3.2', chapterId: 'Chapter 1', lines: [] } as any;

    it('asks the sidecar for the delta after a File System Access write', async () => {
        vi.mocked(applyDialogueStatsDelta).mockClear();
        vi.mocked(writeDialogueSave).mockResolvedValueOnce('handle');

        expect(await writeChapterDialogue('Chapter 1/dialogue.json', payload)).toBe(true);
        expect(applyDialogueStatsDelta).toHaveBeenCalledTimes(1);
        expect(applyDialogueStatsDelta).toHaveBeenCalledWith('Chapter 1/dialogue.json');
    });

    it('does nothing more when the sidecar endpoint already applied it', async () => {
        vi.mocked(applyDialogueStatsDelta).mockClear();
        vi.mocked(writeDialogueSave).mockResolvedValueOnce('sidecar');

        expect(await writeChapterDialogue('Chapter 1/dialogue.json', payload)).toBe(true);
        expect(applyDialogueStatsDelta).not.toHaveBeenCalled();
    });

    it('does nothing when the content was unchanged', async () => {
        vi.mocked(applyDialogueStatsDelta).mockClear();
        vi.mocked(writeDialogueSave).mockResolvedValueOnce('unchanged');

        expect(await writeChapterDialogue('Chapter 1/dialogue.json', payload)).toBe(true);
        expect(applyDialogueStatsDelta).not.toHaveBeenCalled();
    });

    it('reports a failed write and skips the stats step', async () => {
        vi.mocked(applyDialogueStatsDelta).mockClear();
        vi.mocked(writeDialogueSave).mockResolvedValueOnce(null);

        expect(await writeChapterDialogue('Chapter 1/dialogue.json', payload)).toBe(false);
        expect(applyDialogueStatsDelta).not.toHaveBeenCalled();
    });

    it('keeps audio-metadata stamps off the stats path', async () => {
        vi.mocked(applyDialogueStatsDelta).mockClear();
        vi.mocked(writeDialogueSave).mockClear();
        vi.mocked(readOptionalDialogue).mockResolvedValueOnce({
            formatVersion: '3.2', chapterId: 'Chapter 1', lines: [{ id: 1, text: 'Hi.' }],
        } as any);

        await recordLineAudioGeneration('Chapter 1', [1], { provider: 'test' } as any);

        expect(writeDialogue).toHaveBeenCalledTimes(1);
        expect(writeDialogueSave).not.toHaveBeenCalled();
        expect(applyDialogueStatsDelta).not.toHaveBeenCalled();
    });
});
