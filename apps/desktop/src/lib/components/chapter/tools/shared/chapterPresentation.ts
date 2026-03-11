import type { UnifiedLine } from '$lib/components/chapter/tools/types';

export type ChapterRun = {
    text: string;
    characterId: string | null;
    lineId?: number;
};

export type ChapterAnimationPlan = {
    animateOnLoad: boolean[];
    typingDelays: number[];
    highlightDelays: number[];
    revealDelays: number[];
};

const LINE_FADE_DURATION_MS = 300;
const LINE_FADE_STAGGER_MS = 50;
const APPROX_CHARS_PER_LINE = 72;
const ROW_HIGHLIGHT_START_AFTER_TYPING_MS = 500;

type ParagraphSpan = { text: string; start: number; end: number };

function estimateRowLineChunkCount(runs: ChapterRun[]): number {
    let totalChars = 0;
    for (const run of runs) {
        totalChars += run.text?.length ?? 0;
    }
    if (totalChars <= 0) return 0;
    return Math.max(1, Math.ceil(totalChars / APPROX_CHARS_PER_LINE));
}

function estimateRowTypingDurationMs(runs: ChapterRun[]): number {
    const lineCount = estimateRowLineChunkCount(runs);
    if (lineCount <= 0) return 0;
    return ((lineCount - 1) * LINE_FADE_STAGGER_MS) + LINE_FADE_DURATION_MS;
}

export function createStaticChapterAnimationPlan(rowCount: number): ChapterAnimationPlan {
    return {
        animateOnLoad: new Array<boolean>(rowCount).fill(false),
        typingDelays: new Array<number>(rowCount).fill(0),
        highlightDelays: new Array<number>(rowCount).fill(0),
        revealDelays: new Array<number>(rowCount).fill(0),
    };
}

export function buildChapterAnimationPlanData(runsSnapshot: ChapterRun[][]): ChapterAnimationPlan {
    const animatedRowIndices: number[] = [];
    for (let index = 0; index < runsSnapshot.length; index += 1) {
        animatedRowIndices.push(index);
    }

    const animateOnLoad = new Array<boolean>(runsSnapshot.length).fill(false);
    const typingDelays = new Array<number>(runsSnapshot.length).fill(0);
    const highlightDelays = new Array<number>(runsSnapshot.length).fill(0);
    const revealDelays = new Array<number>(runsSnapshot.length).fill(0);

    let nextGlobalLineStartMs = 0;
    const typingEndByRow = new Map<number, number>();
    for (const rowIndex of animatedRowIndices) {
        animateOnLoad[rowIndex] = true;
        typingDelays[rowIndex] = nextGlobalLineStartMs;
        const typingDuration = estimateRowTypingDurationMs(runsSnapshot[rowIndex] ?? []);
        typingEndByRow.set(rowIndex, typingDelays[rowIndex] + typingDuration);

        const lineCount = estimateRowLineChunkCount(runsSnapshot[rowIndex] ?? []);
        nextGlobalLineStartMs += Math.max(1, lineCount) * LINE_FADE_STAGGER_MS;
    }

    for (const rowIndex of animatedRowIndices) {
        const typingEndMs = typingEndByRow.get(rowIndex) ?? 0;
        const rowHighlightStartMs = typingEndMs + ROW_HIGHLIGHT_START_AFTER_TYPING_MS;
        highlightDelays[rowIndex] = Math.max(0, rowHighlightStartMs - typingEndMs);
    }

    const maxHighlightDelayMs = Math.max(0, ...highlightDelays);

    for (let index = 0; index < runsSnapshot.length; index += 1) {
        if (!animateOnLoad[index]) revealDelays[index] = maxHighlightDelayMs;
    }

    return {
        animateOnLoad,
        typingDelays,
        highlightDelays,
        revealDelays,
    };
}

function splitParagraphsWithOffsets(src: string): ParagraphSpan[] {
    const result: ParagraphSpan[] = [];
    const n = src.length;
    let lineStart = 0;
    const pushSpan = (s0: number, e0: number) => {
        let s = s0;
        let e = e0;
        while (s < e && /\s/.test(src[s]) && src[s] !== '\n' && src[s] !== '\r') s++;
        while (e > s && /\s/.test(src[e - 1]) && src[e - 1] !== '\n' && src[e - 1] !== '\r') e--;
        if (e > s) result.push({ text: src.slice(s, e), start: s, end: e });
    };
    let i = 0;
    while (i < n) {
        const ch = src[i];
        if (ch === '\n' || ch === '\r') {
            pushSpan(lineStart, i);
            if (ch === '\r' && i + 1 < n && src[i + 1] === '\n') {
                i += 1;
            }
            i += 1;
            lineStart = i;
            continue;
        }
        i += 1;
    }
    pushSpan(lineStart, n);
    return result;
}

function buildRunsForParagraphLoose(
    paragraph: string,
    paraStart: number,
    paraEnd: number,
    lines: UnifiedLine[],
    startIdx: number
): { runs: ChapterRun[]; nextIdx: number } {
    const runs: ChapterRun[] = [];
    let cursor = 0;
    let i = startIdx;
    while (i < lines.length) {
        const line = lines[i];
        const span = line.span;
        const hasSpan = span && typeof span.start === 'number' && typeof span.end === 'number';

        if (!hasSpan) {
            if (runs.length > 0) break;
            runs.push({ text: line.text, characterId: line.characterName, lineId: line.id });
            return { runs, nextIdx: i + 1 };
        }

        const startAbs = span.start;
        const endAbs = span.end;
        if (startAbs >= paraEnd) break;
        if (endAbs <= paraStart) {
            i++;
            continue;
        }
        const start = Math.max(0, startAbs - paraStart);
        const end = Math.min(paragraph.length, endAbs - paraStart);

        if (start < cursor) {
            i++;
            continue;
        }
        if (cursor < start) {
            runs.push({ text: paragraph.slice(cursor, start), characterId: null });
        }
        const textSlice = paragraph.slice(start, Math.min(end, paragraph.length));
        runs.push({ text: textSlice, characterId: line.characterName, lineId: line.id });
        cursor = Math.min(end, paragraph.length);
        if (cursor >= paragraph.length) {
            i++;
            break;
        }
        i++;
    }

    if (cursor < paragraph.length) runs.push({ text: paragraph.slice(cursor), characterId: null });
    return { runs, nextIdx: i };
}

export function buildParagraphRuns(
    lines: UnifiedLine[],
    rawText: string | null,
    narratorLabel = 'Narrator'
): ChapterRun[][] {
    if (!rawText) {
        return lines.map((line) => [{ text: line.text, characterId: line.characterName, lineId: line.id }]);
    }

    const paras = splitParagraphsWithOffsets(rawText);
    const all: ChapterRun[][] = [];
    let idx = 0;

    while (idx < lines.length && !lines[idx].span) {
        const line = lines[idx];
        all.push([{ text: line.text, characterId: line.characterName, lineId: line.id }]);
        idx++;
    }

    for (const paragraph of paras) {
        if (idx >= lines.length) break;

        const { runs, nextIdx } = buildRunsForParagraphLoose(paragraph.text, paragraph.start, paragraph.end, lines, idx);

        if (runs.length === 0 || (runs.length === 1 && runs[0].characterId === null && runs[0].text === paragraph.text)) {
            all.push([{ text: paragraph.text, characterId: narratorLabel }]);
            idx = Math.min(idx + 1, lines.length);
        } else {
            all.push(runs);
            idx = nextIdx;
        }
    }

    while (idx < lines.length && !lines[idx].span) {
        const line = lines[idx];
        all.push([{ text: line.text, characterId: line.characterName, lineId: line.id }]);
        idx++;
    }

    return all;
}
