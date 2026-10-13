import { describe, expect, it } from 'vitest';
import type { UnifiedLine } from '$lib/components/chapter/tools/types';
import {
  JEV_FIXED_CONTEXT_WINDOW,
  buildDialogueAiAssistRequest,
  buildDialogueAiPayload,
  buildJevDialogueAiRequest,
  buildLocalDialogueAiRequest,
  countReviewableDialogueLines,
  formatDialogueAiReasonCodes,
  formatDialogueAiToolTrace,
} from '../dialogueAiPayload';
import type { DialogueAiAssistChapterInput } from '../dialogueAiPayload';

function makeLine(overrides: Partial<UnifiedLine> & { id: number; text: string }): UnifiedLine {
  return {
    span: null,
    characterName: null,
    candidates: [],
    isConflict: false,
    isReturning: false,
    ...overrides,
  };
}

function makeInput(
  lines: UnifiedLine[],
  stats: { totalLines: number; conflicts: number; characterBreakdown: Record<string, number> } | null = null,
): DialogueAiAssistChapterInput {
  return {
    chapterPath: 'Book/Ch1/chapter.txt',
    chapterTitle: 'Ch1',
    rawText: 'raw chapter text',
    normalizedScript: { lines, stats },
  };
}

describe('buildDialogueAiPayload', () => {
  it('slugifies character ids via normalizeCharacterKey (trim + lowercase, no underscore replacement)', () => {
    const input = makeInput([
      makeLine({ id: 1, text: 'Hi', characterName: '  Alice ' }),
      makeLine({ id: 2, text: 'Hey', characterName: 'Bob Smith' }),
      makeLine({ id: 3, text: 'Narration', characterName: null }),
      makeLine({ id: 4, text: 'More narration', characterName: '' }),
    ]);

    const payload = buildDialogueAiPayload(input);

    expect(payload.formatVersion).toBe('2.0');
    expect(payload.chapterId).toBe('Ch1');
    expect(payload.lines.map((line) => line.characterId)).toEqual(['alice', 'bob smith', null, null]);
  });

  it('maps candidates, conflict flags, and fixed metadata onto every line', () => {
    const input = makeInput([
      makeLine({
        id: 7,
        text: 'Line',
        characterName: 'Alice',
        candidates: [{ name: '  Alice ', confidence: 0.9 }],
        isConflict: true,
        isReturning: true,
      }),
    ]);

    const [line] = buildDialogueAiPayload(input).lines;

    expect(line.id).toBe(7);
    expect(line.text).toBe('Line');
    expect(line.candidates).toEqual([{ characterId: 'alice', confidence: 0.9 }]);
    expect(line.isConflict).toBe(true);
    expect(line.isReturning).toBe(true);
    expect(line.metadata).toEqual({
      emotion: null,
      intensity: 1,
      pacing: null,
      prefix: null,
      customTags: {},
    });
  });

  it('falls back to derived stats when the script carries none', () => {
    const input = makeInput([
      makeLine({ id: 1, text: 'a', isConflict: true }),
      makeLine({ id: 2, text: 'bb' }),
      makeLine({ id: 3, text: 'ccc', isConflict: true }),
    ]);

    const payload = buildDialogueAiPayload(input);

    expect(payload.stats).toEqual({ totalLines: 3, conflicts: 2, characterBreakdown: {} });
  });

  it('prefers the script-provided stats when present', () => {
    const input = makeInput(
      [makeLine({ id: 1, text: 'a', isConflict: true })],
      { totalLines: 42, conflicts: 7, characterBreakdown: { alice: 41 } },
    );

    expect(buildDialogueAiPayload(input).stats).toEqual({
      totalLines: 42,
      conflicts: 7,
      characterBreakdown: { alice: 41 },
    });
  });
});

describe('countReviewableDialogueLines', () => {
  it('counts only lines with a non-narrator character', () => {
    const input = makeInput([
      makeLine({ id: 1, text: 'a', characterName: 'Alice' }),
      makeLine({ id: 2, text: 'b', characterName: '  ' }),
      makeLine({ id: 3, text: 'c', characterName: null }),
      makeLine({ id: 4, text: 'd', characterName: 'Narrator' }),
      makeLine({ id: 5, text: 'e', characterName: 'BOB' }),
    ]);

    expect(countReviewableDialogueLines(input)).toBe(2);
  });
});

describe('formatDialogueAiReasonCodes', () => {
  it('replaces underscores with spaces and joins with commas', () => {
    expect(formatDialogueAiReasonCodes(['speaker_continuation', 'context_gender'])).toBe(
      'speaker continuation, context gender',
    );
    expect(formatDialogueAiReasonCodes([])).toBe('');
  });
});

describe('formatDialogueAiToolTrace', () => {
  it('renders each entry with status and optional note', () => {
    expect(
      formatDialogueAiToolTrace([
        { tool: 'parse', status: 'ok' },
        { tool: 'verify', status: 'warn', note: '2 conflicts' },
      ]),
    ).toBe('parse (ok), verify (warn: 2 conflicts)');
  });
});

describe('per-provider request payloads', () => {
  const dialogue = buildDialogueAiPayload(makeInput([makeLine({ id: 1, text: 'a', characterName: 'Alice' })]));

  it('buildDialogueAiAssistRequest passes args through', () => {
    const request = buildDialogueAiAssistRequest({
      requestId: 'req-1',
      chapterPath: 'Book/Ch1/chapter.txt',
      chapterText: 'raw',
      dialogue,
      autoApplyThreshold: 0.9,
      maxLines: 50,
      dryRun: true,
    });

    expect(request).toEqual({
      request_id: 'req-1',
      chapter_path: 'Book/Ch1/chapter.txt',
      chapter_text: 'raw',
      dialogue,
      auto_apply_threshold: 0.9,
      max_lines: 50,
      dry_run: true,
    });
  });

  it('buildJevDialogueAiRequest uses the fixed JEV parameters', () => {
    const request = buildJevDialogueAiRequest({
      requestId: 'req-2',
      chapterPath: 'Book/Ch1/chapter.txt',
      chapterText: 'raw',
      dialogue,
      maxLines: null,
    });

    expect(request).toEqual({
      request_id: 'req-2',
      chapter_path: 'Book/Ch1/chapter.txt',
      chapter_text: 'raw',
      dialogue,
      context_window: 1500,
      include_previous_speaker: true,
      include_speaker_context: true,
      max_lines: null,
    });
    expect(JEV_FIXED_CONTEXT_WINDOW).toBe(1500);
  });

  it('buildLocalDialogueAiRequest passes caller-provided parameters through', () => {
    const request = buildLocalDialogueAiRequest({
      requestId: 'req-3',
      chapterPath: 'Book/Ch1/chapter.txt',
      chapterText: 'raw',
      dialogue,
      contextWindow: 800,
      includePreviousSpeaker: false,
      includeSpeakerContext: true,
      maxLines: 10,
    });

    expect(request).toEqual({
      request_id: 'req-3',
      chapter_path: 'Book/Ch1/chapter.txt',
      chapter_text: 'raw',
      dialogue,
      context_window: 800,
      include_previous_speaker: false,
      include_speaker_context: true,
      max_lines: 10,
    });
  });
});
