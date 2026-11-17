import { describe, expect, it, vi } from 'vitest';
import { get, writable } from 'svelte/store';
import { assignCharacterToLines } from '../../review/reviewHandlers';
import { normalizeScriptData } from '../chapterNormalization';
import { loadChapterContent, saveDialogueFromNormalized } from '../chapterPersistence';
import { inMemoryFs } from '$lib/services/__tests__/helpers/inMemoryFs';
import { makeCharacterFile } from '$lib/services/__tests__/helpers/fixtures';
import type { UnifiedLine } from '../../types';

// The chapter persistence owner reaches storage and identity resolution only
// through fs: stub that seam with the in-memory fs.
vi.mock('$lib/services/fs', async () => {
    const mod = await import('$lib/services/__tests__/helpers/inMemoryFs');
    return mod.inMemoryFs;
});

const G_CATHERINE = 'C4THR1NE';

describe('non-speaker role assignment', () => {
    it('clears the non-speaker flag and retains the chosen role through save and reload', async () => {
        const line: UnifiedLine = {
            id: 182, text: 'Is there anything else I should know?',
            span: { start: 1, end: 36 }, characterName: null,
            candidates: [], isNonSpeaker: true, isConflict: false, isReturning: false,
        };
        const script = writable({ lines: [line], stats: { numLines: 1, numConflicts: 0 } });
        const rebuild = vi.fn();
        const scheduleSave = vi.fn();
        assignCharacterToLines([182], 'Catherine', script, rebuild, scheduleSave);
        expect(get(script).lines[0]).toMatchObject({
            characterName: 'Catherine', isNonSpeaker: false, isConflict: false,
            attribution: { resolutionStatus: 'user_confirmed' },
        });
        expect(rebuild).toHaveBeenCalledOnce();
        expect(scheduleSave).toHaveBeenCalledOnce();

        inMemoryFs.__reset();
        inMemoryFs.__seed({ 'characters/Catherine.json': makeCharacterFile({ guid: G_CATHERINE, title: 'Catherine' }) });
        const rawText = `\u201c${line.text}\u201d`;
        await saveDialogueFromNormalized({
            normalized: get(script), chapter: { title: 'Test chapter' }, root: 'test-root',
            rawText, unknownThreshold: 0.62, unknownSpeakerLabel: 'Unknown',
        });
        const saved = inMemoryFs.__readJson('Test chapter/dialogue.json');
        expect(saved.lines[0]).toMatchObject({ characterId: G_CATHERINE, isNonSpeaker: false });
        const reloaded = await normalizeScriptData(saved, {
            root: 'test-root', unknownThreshold: 0.62,
            readCentralCharacters: async () => ({ characters: [] }),
            buildIdToNameMap: () => new Map([[G_CATHERINE.toLowerCase(), 'Catherine']]),
        });
        expect(reloaded?.lines[0]).toMatchObject({ characterName: 'Catherine', isNonSpeaker: false });
    });

    it('repairs spans before publishing loaded lines to editing and returning-flag consumers', async () => {
        const result = await loadChapterContent({
            chapter: { title: 'Test chapter', path: 'chapter.txt' }, root: 'test-root',
            unknownThreshold: 0.62, unknownSpeakerLabel: 'Unknown',
            readDialogueForChapter: async () => ({
                chapter: 'Test chapter', sourceFile: 'chapter.txt',
                lines: [{
                    id: 1, text: 'Hello.', span: { start: 20, end: 26 },
                    chosenSpeaker: 'Alice', candidates: [], isConflict: false,
                }],
                stats: { numLines: 1, numConflicts: 0 },
            }),
            normalizeScriptData, readCentralCharacters: async () => ({ characters: [] }),
            buildIdToNameMap: () => new Map(),
            readTextFile: async () => '\u201cHello.\u201d',
        });
        expect(result.normalized?.lines[0].span).toEqual({ start: 1, end: 7 });
        expect(result.currentScript?.lines[0].span).toEqual({ start: 1, end: 7 });
    });
});
