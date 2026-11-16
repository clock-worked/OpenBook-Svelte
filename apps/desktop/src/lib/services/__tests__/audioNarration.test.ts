import { describe, expect, it } from 'vitest';

import {
  buildLineTextWithSpeechVerbTags,
  extractNarrationTags,
  isShortSpeechVerbTag,
} from '../audioNarration';
import type { DialogueLine } from '$lib/types';

function makeLine(partial: {
  id: number;
  characterId: string | null;
  text: string;
  span: { start: number; end: number } | null;
}): DialogueLine {
  return {
    id: partial.id,
    characterId: partial.characterId,
    text: partial.text,
    span: partial.span,
    metadata: { emotion: null, intensity: 0, pacing: null, prefix: null, customTags: {} },
    candidates: [],
    isConflict: false,
  };
}

describe('isShortSpeechVerbTag', () => {
  it('accepts a short narration tag containing a speech verb', () => {
    expect(isShortSpeechVerbTag('Caleb muttered, shaking his head.')).toBe(true);
  });

  it('rejects text without a speech verb', () => {
    expect(isShortSpeechVerbTag('The door slammed shut behind him.')).toBe(false);
  });

  it('rejects a long narration sentence even when it contains a speech verb', () => {
    expect(isShortSpeechVerbTag('Caleb said that he had no idea what was going on, and that nobody else did either, apparently.')).toBe(false);
  });

  it('rejects empty text', () => {
    expect(isShortSpeechVerbTag('')).toBe(false);
  });
});

describe('extractNarrationTags', () => {
  it('keeps only short narrator tags with spans, ordered by span position', () => {
    const lines = [
      makeLine({ id: 1, characterId: 'caleb', text: 'First that sword fanatic and now the Chosen of the Heartsoul Daolord,', span: { start: 0, end: 69 } }),
      makeLine({ id: 2, characterId: 'narrator', text: 'Caleb muttered, shaking his head.', span: { start: 47, end: 78 } }),
      makeLine({ id: 3, characterId: 'narrator', text: 'The room was very quiet, and nobody moved for a long moment afterwards.', span: { start: 100, end: 158 } }),
      makeLine({ id: 4, characterId: 'caleb', text: 'Caleb whispered, leaning close.', span: { start: 200, end: 228 } }),
      makeLine({ id: 5, characterId: 'narrator', text: 'No span attached, just said something.', span: null }),
    ];

    const tags = extractNarrationTags(lines);
    expect(tags).toEqual([
      { text: 'Caleb muttered, shaking his head.', spanStart: 47, spanEnd: 78 },
    ]);
  });

  it('returns an empty array when there are no narrator lines', () => {
    expect(extractNarrationTags([])).toEqual([]);
  });
});

describe('buildLineTextWithSpeechVerbTags', () => {
  it('prepends a tag that sits directly before the line', () => {
    const raw = 'Caleb muttered, shaking his head. "First that sword fanatic and now the Chosen of the Heartsoul Daolord,"';
    const tag = makeLine({ id: 1, characterId: 'narrator', text: 'Caleb muttered, shaking his head.', span: { start: 0, end: 33 } });
    const line = makeLine({ id: 2, characterId: 'caleb', text: 'First that sword fanatic and now the Chosen of the Heartsoul Daolord,', span: { start: 35, end: 104 } });
    const tags = extractNarrationTags([tag, line]);

    expect(buildLineTextWithSpeechVerbTags(line, tags, raw)).toBe(
      'Caleb muttered, shaking his head. First that sword fanatic and now the Chosen of the Heartsoul Daolord,'
    );
  });

  it('appends a tag that sits directly after the line', () => {
    const raw = '"Could this get any worse?" Caleb muttered, shaking his head';
    const line = makeLine({ id: 1, characterId: 'caleb', text: 'Could this get any worse?', span: { start: 1, end: 26 } });
    const tag = makeLine({ id: 2, characterId: 'narrator', text: 'Caleb muttered, shaking his head', span: { start: 28, end: 60 } });
    const tags = extractNarrationTags([line, tag]);

    expect(buildLineTextWithSpeechVerbTags(line, tags, raw)).toBe(
      'Could this get any worse? Caleb muttered, shaking his head',
    );
  });

  it('re-inserts a tag embedded inside a merged multi-quote line at its original position', () => {
    const raw = '"First that sword fanatic and now the Chosen of the Heartsoul Daolord," Caleb muttered, shaking his head. "Could this get any worse?"';
    const line = makeLine({ id: 1, characterId: 'caleb', text: 'First that sword fanatic and now the Chosen of the Heartsoul Daolord, Could this get any worse?', span: { start: 1, end: 132 } });
    const tag = makeLine({ id: 2, characterId: 'narrator', text: 'Caleb muttered, shaking his head.', span: { start: 72, end: 105 } });
    const tags = extractNarrationTags([line, tag]);

    expect(buildLineTextWithSpeechVerbTags(line, tags, raw)).toBe(
      'First that sword fanatic and now the Chosen of the Heartsoul Daolord, Caleb muttered, shaking his head. Could this get any worse?'
    );
  });

  it('appends an embedded tag at the end when raw chapter text is unavailable', () => {
    const line = makeLine({ id: 1, characterId: 'caleb', text: 'First that sword fanatic, Could this get any worse?', span: { start: 0, end: 51 } });
    const tag = makeLine({ id: 2, characterId: 'narrator', text: 'Caleb muttered, shaking his head', span: { start: 10, end: 42 } });
    const tags = extractNarrationTags([line, tag]);

    expect(buildLineTextWithSpeechVerbTags(line, tags, null)).toBe(
      'First that sword fanatic, Could this get any worse? Caleb muttered, shaking his head'
    );
  });

  it('handles tags on both sides of a line', () => {
    const raw = '"Caleb sighed." "Hello." "He added."';
    const before = makeLine({ id: 1, characterId: 'narrator', text: 'Caleb sighed.', span: { start: 1, end: 14 } });
    const line = makeLine({ id: 2, characterId: 'caleb', text: 'Hello.', span: { start: 17, end: 23 } });
    const after = makeLine({ id: 3, characterId: 'narrator', text: 'He added.', span: { start: 25, end: 34 } });
    const tags = extractNarrationTags([before, line, after]);

    expect(buildLineTextWithSpeechVerbTags(line, tags, raw)).toBe('Caleb sighed. Hello. He added');
  });

  it('returns the original text when there are no tags', () => {
    const line = makeLine({ id: 1, characterId: 'caleb', text: 'Hello.', span: { start: 0, end: 6 } });
    expect(buildLineTextWithSpeechVerbTags(line, [], 'Hello.')).toBe('Hello.');
  });

  it('ignores a tag separated from the line by more than punctuation', () => {
    const raw = 'Caleb muttered, shaking his head. The room fell silent for a while. "Hello."';
    const tag = makeLine({ id: 1, characterId: 'narrator', text: 'Caleb muttered, shaking his head.', span: { start: 0, end: 33 } });
    const line = makeLine({ id: 2, characterId: 'caleb', text: 'Hello.', span: { start: 69, end: 75 } });
    const tags = extractNarrationTags([tag, line]);

    expect(buildLineTextWithSpeechVerbTags(line, tags, raw)).toBe('Hello.');
  });
});
