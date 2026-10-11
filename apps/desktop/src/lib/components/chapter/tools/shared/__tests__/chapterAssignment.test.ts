import { describe, expect, it, vi } from 'vitest';
import { get, writable } from 'svelte/store';
import { assignCharacterToLines } from '../../review/reviewHandlers';
import { normalizeAttribution, normalizeScriptData } from '../chapterNormalization';
import { loadChapterContent, saveDialogueFromNormalized } from '../chapterPersistence';
import type { UnifiedLine } from '../../types';
import type { DialogueJson } from '$lib/types';

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

        const writeDialogue = vi.fn(async (_path: string, _data: DialogueJson) => true);
        const rawText = `\u201c${line.text}\u201d`;
        await saveDialogueFromNormalized({
            normalized: get(script), chapter: { title: 'Test chapter' }, root: 'test-root',
            rawText, unknownThreshold: 0.62, unknownSpeakerLabel: 'Unknown',
            ensureCentralCharactersForNames: async () => {},
            ensureCharacterNameToIdMap: async () => new Map([['catherine', 'CATHERINE_ID']]),
            normalizeAttribution, writeDialogue,
            getRootDirInfo: () => ({ name: 'test-root', hasHandle: true }),
            getBackendRootAbsolutePath: () => null,
            apiSave: vi.fn(),
        });
        const saved = writeDialogue.mock.calls[0][1];
        expect(saved.lines[0]).toMatchObject({ characterId: 'CATHERINE_ID', isNonSpeaker: false });
        const reloaded = await normalizeScriptData(saved, {
            root: 'test-root', unknownThreshold: 0.62,
            readCentralCharacters: async () => ({ characters: [] }),
            buildIdToNameMap: () => new Map([['catherine_id', 'Catherine']]),
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
