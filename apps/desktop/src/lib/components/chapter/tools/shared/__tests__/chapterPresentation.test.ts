import { describe, expect, it, vi } from 'vitest';
import { alignChapterLineSpans, buildParagraphRuns } from '../chapterPresentation';
import type { UnifiedLine } from '../../types';

function line(id: number, text: string, start: number | null, end?: number): UnifiedLine {
    return {
        id, text, span: start == null ? null : { start, end: end ?? start + text.length },
        characterName: 'Alice', candidates: [], isConflict: false, isReturning: false,
    };
}

describe('chapter span alignment and presentation', () => {
    it('keeps non-speaker quoted text associated with its line ID', () => {
        const text = '\u201cIs there anything else I should know?\u201d';
        const quote = { ...line(182, text.slice(1, -1), 1), characterName: null, isNonSpeaker: true };
        const runs = buildParagraphRuns([quote], text)[0];
        expect(runs.map((run) => run.text).join('')).toBe(text);
        expect(runs.find((run) => run.lineId === 182)?.text).toBe(quote.text);
    });

    it('repairs shifted, invalid and overlapping spans without mutating the input', () => {
        const text = 'First.\r\n\u201cSecond.\u201d\r\nThird.';
        const lines = [line(1, 'First.', 2), line(2, 'Second.', 3), line(3, 'Third.', NaN, Infinity)];
        const aligned = alignChapterLineSpans(lines, text);
        expect(aligned.map((item) => item.span)).toEqual([
            { start: 0, end: 6 }, { start: 9, end: 16 }, { start: 19, end: 25 },
        ]);
        expect(lines[0].span?.start).toBe(2);
        expect(buildParagraphRuns(lines, text).flat().filter((run) => run.lineId != null)
            .map((run) => run.lineId)).toEqual([1, 2, 3]);
    });

    it('does not consume a future line when an unmatched source paragraph precedes it', () => {
        const text = 'Unparsed introduction.\nHello.\nUnparsed ending.';
        const start = text.indexOf('Hello.');
        const runs = buildParagraphRuns([line(7, 'Hello.', start)], text);
        expect(runs.map((row) => row.map((run) => run.text).join(''))).toEqual(text.split('\n'));
        expect(runs.flat().find((run) => run.lineId === 7)?.text).toBe('Hello.');
    });

    it('maps repeated quotes forward rather than reusing an earlier occurrence', () => {
        const text = 'Hello.\nHello.';
        const aligned = alignChapterLineSpans([line(1, 'Hello.', 0), line(2, 'Hello.', 0)], text);
        expect(aligned.map((item) => item.span?.start)).toEqual([0, 7]);
    });

    it('renders every portion of a span crossing paragraph boundaries', () => {
        const text = 'First.\nSecond.';
        const runs = buildParagraphRuns([line(1, text, 0)], text);
        expect(runs.flat().map((run) => run.text)).toEqual(['First.', 'Second.']);
        expect(runs.flat().every((run) => run.lineId === 1)).toBe(true);
    });

    it('keeps unalignable and intentionally edited lines visible and logs alignment failures', () => {
        const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
        try {
            const lines = [line(1, 'Hello.', 0), line(2, 'Missing quote.', 100), line(3, 'Edited.', null)];
            const runs = buildParagraphRuns(lines, 'Hello.').flat();
            expect(runs.filter((run) => run.lineId != null).map((run) => run.lineId)).toEqual([1, 2, 3]);
            expect(runs.find((run) => run.lineId === 2)?.text).toBe('Missing quote.');
            expect(warning).toHaveBeenCalledWith(expect.any(String), { lineIds: [2] });
        } finally {
            warning.mockRestore();
        }
    });

    it('preserves no-source and all-edited chapter rendering', () => {
        const lines = [line(1, 'Edited.', null)];
        expect(buildParagraphRuns(lines, null)).toEqual(buildParagraphRuns(lines, 'Original.'));
    });

    it('repairs code-point offsets after characters occupying two UTF-16 code units', () => {
        const text = '\u{1F4D6}\n\u201cHello.\u201d';
        expect(alignChapterLineSpans([line(1, 'Hello.', 3)], text)[0].span).toEqual({ start: 4, end: 10 });
    });

    it('preserves standalone edited rows in script order without hiding following lines', () => {
        const lines = [line(1, 'First.', 0), line(2, 'Edited.', null), line(3, 'Last.', 7)];
        const runs = buildParagraphRuns(lines, 'First.\nLast.');
        expect(runs.flat().filter((run) => run.lineId != null).map((run) => run.lineId)).toEqual([1, 2, 3]);
    });
});
