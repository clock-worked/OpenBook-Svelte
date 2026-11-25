// The book format owner (services/bookFormat.ts), driven by one vector file
// of v2 and v3 fixture books: every document kind, read and write, through
// the Book interface over the in-memory fs.

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/services/fs', async () => (await import('./helpers/inMemoryFs')).inMemoryFs);

import * as fs from '$lib/services/fs';
import type { InMemoryFs } from './helpers/inMemoryFs';
import vectors from './fixtures/bookFormatVectors.json';
import { classifyBookPath, DIALOGUE_FORMAT_VERSIONS, isVersionedDialogue, layoutPath, openBook } from '../bookFormat';

const mem = fs as unknown as InMemoryFs;

type Case = (typeof vectors.cases)[number] & Record<string, any>;

function seed(book: keyof typeof vectors.books) {
    mem.__reset();
    mem.__seed(vectors.books[book] as Record<string, unknown>);
}

describe('book format vectors', () => {
    beforeEach(() => mem.__reset());

    it.each(vectors.cases.map((c) => [c.name, c] as [string, Case]))('%s', async (_name, c) => {
        seed(c.book as keyof typeof vectors.books);
        const book = openBook('/book');
        const before = mem.__snapshot();

        if (c.op === 'version') {
            expect(await book.version()).toBe(c.expect);
            return;
        }
        if (c.op === 'readChapterDialogue') {
            expect(await book.readChapterDialogue(c.chapter)).toEqual(c.expect);
            return;
        }
        if (c.op === 'read') {
            const value: any = c.chapter
                ? await (book.read as any)(c.doc, c.chapter)
                : await (book.read as any)(c.doc);
            if (c.expectNames) expect(value?.characters?.map((ch: any) => ch.name)).toEqual(c.expectNames);
            else expect(value).toEqual(c.expect);
            expect(mem.__snapshot()).toEqual(before); // a read never writes
            return;
        }

        const args = c.chapter ? [c.doc, c.chapter, c.value, c.options] : [c.doc, c.value, c.options];
        expect(await (book.write as any)(...args)).toBe(c.expectResult);
        const after = mem.__snapshot();
        const changed = Object.keys(after).filter((key) => after[key] !== before[key]);
        expect(changed).toEqual(c.written ? [c.written] : []);
        if (c.written) expect(mem.__readJson(c.written)).toEqual(c.value);
    });

    it.each(vectors.paths.map((p) => [p.path, p.expect] as const))('classifies %s', (path, expected) => {
        expect(classifyBookPath(path)).toEqual(expected);
    });

    it('classifies what layoutPath builds', () => {
        expect(classifyBookPath(layoutPath('roster', 'Ch 1'))).toEqual({ doc: 'roster', chapter: 'Ch 1' });
        expect(classifyBookPath(layoutPath('characterFile', 'Bob.json'))).toEqual({ doc: 'characterFile', fileName: 'Bob.json' });
        expect(() => layoutPath('dialogue')).toThrow();
    });
});

describe('dialogue schema', () => {
    it('lists the versioned dialogue formats', () => {
        expect([...DIALOGUE_FORMAT_VERSIONS]).toEqual(['2.0', '3.0', '3.1', '3.2']);
        for (const version of DIALOGUE_FORMAT_VERSIONS) {
            expect(isVersionedDialogue({ formatVersion: version, lines: [] })).toBe(true);
        }
        expect(isVersionedDialogue({ formatVersion: '1.0', lines: [] })).toBe(false);
        expect(isVersionedDialogue({ chapter: 'Chapter 1', lines: [] })).toBe(false);
        expect(isVersionedDialogue(null)).toBe(false);
    });
});

describe('book version', () => {
    it('is read once per Book', async () => {
        seed('v3');
        const spy = vi.spyOn(mem, 'readCharacterFolder');
        const book = openBook('/book');
        await book.version();
        await book.write('roster', 'Ch1', { formatVersion: '2.0', characters: [] });
        await book.write('roster', 'Ch2', { formatVersion: '2.0', characters: [] });
        expect(spy).toHaveBeenCalledTimes(1);
        spy.mockRestore();
    });

    it('treats a failed folder read as v2', async () => {
        seed('v3');
        const spy = vi.spyOn(mem, 'readCharacterFolder').mockRejectedValueOnce(new Error('disk'));
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
        expect(await openBook('/book').version()).toBe('2.0');
        spy.mockRestore();
        warn.mockRestore();
    });
});
