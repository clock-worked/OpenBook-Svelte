import { describe, expect, it } from 'vitest';
import { normalizeScriptData } from '../chapterNormalization';
import { buildIdToNameMap } from '$lib/stores/characters';
import type { Character } from '$lib/types';

const centralCharacters: Character[] = [
    {
        guid: 'YY3GE4HN',
        id: 'YY3GE4HN',
        name: 'Rashid',
        gender: 'Male',
        aliases: [],
        color: '#dcedc8',
        notes: '',
        firstAppearance: null,
        stats: { totalLines: 0, chapterCount: 0 },
        voice: null,
        provider: null,
        voiceId: null,
        voiceMeta: null,
        manifestStats: null,
    },
    {
        guid: 'SJH4X2QK',
        id: 'SJH4X2QK',
        name: 'Sergeant Jaha',
        gender: 'Male',
        aliases: [],
        color: null,
        notes: '',
        firstAppearance: null,
        stats: { totalLines: 0, chapterCount: 0 },
        voice: null,
        provider: null,
        voiceId: null,
        voiceMeta: null,
        manifestStats: null,
    },
];

function makeOptions() {
    return {
        root: 'book-root',
        unknownThreshold: 0.5,
        readCentralCharacters: async (_root: string) => ({ formatVersion: '3.0' as const, characters: centralCharacters }),
        buildIdToNameMap,
    };
}

describe('normalizeScriptData — v3 display-name resolution', () => {
    it('canonicalizes a v2 slug chosenSpeaker to the character title', async () => {
        const data = {
            lines: [
                { id: 1, text: 'Hold the line.', chosenSpeaker: 'sergeant-jaha', candidates: [], isConflict: false },
            ],
        } as any;

        const result = await normalizeScriptData(data, makeOptions());

        expect(result?.lines[0].characterName).toBe('Sergeant Jaha');
    });

    it('canonicalizes a v2 slug in candidates to the character title', async () => {
        const data = {
            lines: [
                {
                    id: 1,
                    text: 'Hold the line.',
                    chosenSpeaker: null,
                    candidates: [{ name: 'sergeant-jaha', confidence: 0.9 }],
                    isConflict: false,
                },
            ],
        } as any;

        const result = await normalizeScriptData(data, makeOptions());

        expect(result?.lines[0].candidates[0].name).toBe('Sergeant Jaha');
    });

    it('resolves an uppercase v3 GUID characterId to the title (map keys are lowercase)', async () => {
        const data = {
            lines: [
                { id: 2, text: 'You are late.', characterId: 'YY3GE4HN', candidates: [], isConflict: false },
            ],
        } as any;

        const result = await normalizeScriptData(data, makeOptions());

        expect(result?.lines[0].characterName).toBe('Rashid');
    });

    it('leaves unknown speakers untouched', async () => {
        const data = {
            lines: [
                { id: 3, text: 'Who is there?', chosenSpeaker: 'mystery-person', candidates: [], isConflict: false },
            ],
        } as any;

        const result = await normalizeScriptData(data, makeOptions());

        expect(result?.lines[0].characterName).toBe('mystery-person');
    });
});
