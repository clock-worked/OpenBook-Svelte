type SpanRange = {
    start: number;
    end: number;
};

type SpanLike = {
    span: SpanRange | null;
};

type ParagraphRange = {
    start: number;
    end: number;
};

export function buildParagraphRanges(rawText: string): ParagraphRange[] {
    if (!rawText) return [];

    const ranges: ParagraphRange[] = [];
    const textLength = rawText.length;
    let index = 0;

    while (index < textLength) {
        const lineStart = index;
        let lineEnd = lineStart;
        while (lineEnd < textLength && rawText[lineEnd] !== '\n' && rawText[lineEnd] !== '\r') {
            lineEnd += 1;
        }

        const lineText = rawText.slice(lineStart, lineEnd);
        let nextLineStart = lineEnd;
        if (nextLineStart < textLength) {
            if (rawText[nextLineStart] === '\r' && rawText[nextLineStart + 1] === '\n') {
                nextLineStart += 2;
            } else {
                nextLineStart += 1;
            }
        }

        if (lineText.trim().length > 0) {
            ranges.push({ start: lineStart, end: lineEnd });
        }

        index = nextLineStart;
    }

    return ranges;
}

export function findParagraphIndexForOffset(paragraphRanges: ParagraphRange[], offset: number): number {
    if (!Number.isFinite(offset)) return -1;

    for (let index = 0; index < paragraphRanges.length; index += 1) {
        const range = paragraphRanges[index];
        if (offset >= range.start && offset < range.end) {
            return index;
        }
    }

    return -1;
}

export function isDialogueSpan(
    rawText: string,
    spanStart: number,
    paragraphRanges: ParagraphRange[] = buildParagraphRanges(rawText)
): boolean {
    if (!rawText || !Number.isFinite(spanStart)) return false;

    const paragraphIndex = findParagraphIndexForOffset(paragraphRanges, spanStart);
    if (paragraphIndex < 0) return false;

    const paragraph = paragraphRanges[paragraphIndex];
    let quoteCount = 0;
    for (let index = paragraph.start; index < Math.min(spanStart, rawText.length); index += 1) {
        const char = rawText[index];
        if (char === '"' || char === '“' || char === '”') {
            quoteCount += 1;
        }
    }

    return quoteCount % 2 === 1;
}

export function computeReturningFlags<T extends SpanLike>(
    lines: readonly T[],
    rawText: string,
    isDialogueLine: (line: T, index: number, paragraphIndex: number) => boolean
): boolean[] {
    if (!rawText || lines.length === 0) {
        return lines.map(() => false);
    }

    const paragraphRanges = buildParagraphRanges(rawText);
    const paragraphIndexes = lines.map((line) => {
        const spanStart = line.span?.start;
        if (!Number.isFinite(spanStart)) return -1;
        return findParagraphIndexForOffset(paragraphRanges, spanStart as number);
    });

    const dialogueFlags = lines.map((line, index) => {
        const paragraphIndex = paragraphIndexes[index];
        if (paragraphIndex < 0) return false;
        return isDialogueLine(line, index, paragraphIndex);
    });

    const paragraphsWithDialogue = new Set<number>();
    for (let index = 0; index < lines.length; index += 1) {
        const paragraphIndex = paragraphIndexes[index];
        if (paragraphIndex < 0 || !dialogueFlags[index]) continue;
        paragraphsWithDialogue.add(paragraphIndex);
    }

    const lastEntryByParagraph = new Map<number, number>();
    for (let index = 0; index < lines.length; index += 1) {
        const paragraphIndex = paragraphIndexes[index];
        if (paragraphIndex < 0) continue;
        lastEntryByParagraph.set(paragraphIndex, index);
    }

    return lines.map((_line, index) => {
        const paragraphIndex = paragraphIndexes[index];
        if (paragraphIndex < 0 || !paragraphsWithDialogue.has(paragraphIndex)) return false;
        return lastEntryByParagraph.get(paragraphIndex) === index;
    });
}