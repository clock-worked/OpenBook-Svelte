// ============================================================================
// Issue #2 regression: changing a character assignment must not (re)create the
// retired chapter roster file `<Ch>/<Ch>.characters.json` in a v3 book
// (ADR 0002 IN-2, ADR 0003 read-legacy-write-current).
//
// Drives the real assignment entry point (assignCharacterToLines, which also
// runs syncCharactersWithUsage) against the in-memory fs. Its roster persists
// are fire-and-forget, so each test flushes pending work before asserting;
// the v2 control proves that flush is long enough to observe a write.
// ============================================================================

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { writable } from 'svelte/store';
import { assignCharacterToLines } from '../../review/reviewHandlers';
import { bookRoot, currentChapter } from '$lib/stores/bookState';
import { characters } from '$lib/stores/characters';
import { inMemoryFs } from '$lib/services/__tests__/helpers/inMemoryFs';
import { diffTree } from '$lib/services/__tests__/helpers/diffTree';
import { makeCharacterFile } from '$lib/services/__tests__/helpers/fixtures';
import type { UnifiedLine } from '../../types';

vi.mock('$lib/services/fs', async () => {
    const mod = await import('$lib/services/__tests__/helpers/inMemoryFs');
    return mod.inMemoryFs;
});

const ROOT = 'Book-9';
const CHAPTER = '47-Interlude-Rats';
const ROSTER = `${CHAPTER}/${CHAPTER}.characters.json`;

function makeScript() {
    const line: UnifiedLine = {
        id: 1, text: 'Where are the rats?',
        span: { start: 1, end: 20 }, characterName: null,
        candidates: [], isNonSpeaker: false, isConflict: false, isReturning: false,
    };
    return writable({ lines: [line], stats: { numLines: 1, numConflicts: 0 } });
}

async function flushFireAndForget(): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, 0));
}

async function assignAlice(): Promise<void> {
    bookRoot.set(ROOT);
    currentChapter.set({ title: CHAPTER } as any);
    await flushFireAndForget(); // let the characters store's chapter load settle
    characters.set({ formatVersion: '3.0', characters: [] });
    assignCharacterToLines([1], 'Alice', makeScript(), vi.fn(), vi.fn());
    await flushFireAndForget();
}

describe('assignment change and the chapter roster file (issue #2)', () => {
    beforeEach(() => {
        inMemoryFs.__reset();
    });

    it('v3 book, roster absent: assigning a character writes no chapter roster file', async () => {
        inMemoryFs.__seed({
            'characters/Alice.json': makeCharacterFile({ guid: 'AA01BB02', title: 'Alice' }),
        });
        const before = inMemoryFs.__snapshot();

        await assignAlice();

        expect(inMemoryFs.__has(ROSTER)).toBe(false);
        expect(diffTree(before, inMemoryFs.__snapshot())).toEqual({ added: [], deleted: [], changed: [] });
    });

    it('v2 book (control): assigning a character still writes the chapter roster', async () => {
        inMemoryFs.__seed({
            'characters.json': {
                formatVersion: '2.0',
                characters: [{ id: 'alice', name: 'Alice', gender: 'Female' }],
            },
        });

        await assignAlice();

        expect(inMemoryFs.__readJson(ROSTER)).toEqual({
            formatVersion: '3.0',
            characters: [{ id: 'alice' }],
        });
    });
});
