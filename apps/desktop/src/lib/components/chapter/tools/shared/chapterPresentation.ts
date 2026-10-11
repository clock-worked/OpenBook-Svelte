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

export function alignChapterLineSpans(lines: UnifiedLine[], rawText: string): UnifiedLine[] {
    let cursor = 0;
    const unresolvedIds: number[] = [];
    const aligned = lines.map((line) => {
        // Null spans are intentional for edited text, which need not occur in the source.
        if (!line.span) return line;
        const { start, end } = line.span;
        const text = line.text.trim();
        const valid = Number.isInteger(start) && Number.isInteger(end)
            && start >= cursor && end > start && end <= rawText.length;
        if (valid && text && rawText.slice(start, end).trim() === text) {
            cursor = end;
            return line;
        }

        const matchStart = text ? rawText.indexOf(text, cursor) : -1;
        if (matchStart >= 0) {
            const span = { start: matchStart, end: matchStart + text.length };
            cursor = span.end;
            return { ...line, span };
        }

        unresolvedIds.push(line.id);
        return { ...line, span: null };
    });
    if (unresolvedIds.length) {
        console.warn('[ChapterView] Could not align line spans; keeping lines as editable standalone text', {
            lineIds: unresolvedIds,
        });
    }
    return aligned;
}

export function buildParagraphRuns(
    lines: UnifiedLine[],
    rawText: string | null,
    narratorLabel = 'Narrator'
): ChapterRun[][] {
    if (!rawText) {
        return lines.map((line) => [{ text: line.text, characterId: line.characterName, lineId: line.id }]);
    }

    const alignedLines = alignChapterLineSpans(lines, rawText);
    const positioned = alignedLines.filter(
        (line): line is UnifiedLine & { span: NonNullable<UnifiedLine['span']> } => line.span != null
    );
    if (!positioned.length) {
        return alignedLines.map((line) => [{ text: line.text, characterId: line.characterName, lineId: line.id }]);
    }

    const paras = splitParagraphsWithOffsets(rawText);
    const all: ChapterRun[][] = [];
    const renderedIds = new Set<number>();
    const standalone: Array<{ line: UnifiedLine; beforeOffset: number }> = [];
    let beforeOffset = Infinity;
    for (let index = alignedLines.length - 1; index >= 0; index--) {
        const line = alignedLines[index];
        if (line.span) {
            beforeOffset = line.span.start;
        } else {
            standalone.push({ line, beforeOffset });
        }
    }
    standalone.reverse();
    let standaloneIndex = 0;
    let positionedIndex = 0;

    for (const paragraph of paras) {
        while (standaloneIndex < standalone.length && standalone[standaloneIndex].beforeOffset < paragraph.end) {
            const { line } = standalone[standaloneIndex++];
            all.push([{ text: line.text, characterId: line.characterName, lineId: line.id }]);
            renderedIds.add(line.id);
        }
        const runs: ChapterRun[] = [];
        let cursor = paragraph.start;
        while (positionedIndex < positioned.length && positioned[positionedIndex].span.end <= paragraph.start) {
            positionedIndex++;
        }
        for (let index = positionedIndex; index < positioned.length; index++) {
            const line = positioned[index];
            const span = line.span;
            if (span.start >= paragraph.end) break;
            const start = Math.max(paragraph.start, span.start);
            const end = Math.min(paragraph.end, span.end);
            if (cursor < start) {
                runs.push({ text: rawText.slice(cursor, start), characterId: null });
            }
            runs.push({ text: rawText.slice(start, end), characterId: line.characterName, lineId: line.id });
            renderedIds.add(line.id);
            cursor = end;
        }
        if (!runs.length) {
            all.push([{ text: paragraph.text, characterId: narratorLabel }]);
        } else {
            if (cursor < paragraph.end) {
                runs.push({ text: rawText.slice(cursor, paragraph.end), characterId: null });
            }
            all.push(runs);
        }
    }

    for (const line of alignedLines) {
        if (renderedIds.has(line.id)) continue;
        all.push([{ text: line.text, characterId: line.characterName, lineId: line.id }]);
    }

    return all;
}
