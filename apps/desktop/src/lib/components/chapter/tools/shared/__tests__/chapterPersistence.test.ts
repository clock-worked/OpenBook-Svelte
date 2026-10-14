import { describe, expect, it, vi } from 'vitest';

vi.mock('$lib/services/fs', () => ({
    writeDialogue: vi.fn(async () => true)
}));

import { writeDialogue } from '$lib/services/fs';
import { saveDialogueFromNormalized } from '../chapterPersistence';
import { normalizeAttribution } from '../chapterNormalization';

const mockWriteDialogue = vi.mocked(writeDialogue);

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
            ensureCentralCharactersForNames: async () => {},
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
