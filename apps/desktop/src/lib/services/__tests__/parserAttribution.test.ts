import { describe, expect, it } from 'vitest';
import { convertLineToDialogueLine } from '../parser';

function makeLine(attribution: any) {
  return {
    line_type: 'dialogue',
    speaker: 'Rashid',
    segments: [{ type: 'text', value: 'Hold the line.' }],
    is_suggestion: false,
    attribution,
  } as any;
}

function makeMaps(): [Map<string, string>, Map<string, string>] {
  return [new Map([['rashid', 'YY3GE4HN']]), new Map([['YY3GE4HN', 'Rashid']])];
}

describe('convertLineToDialogueLine — audit fields carry-through', () => {
  it('carries legacyRule and jevVerification from the parser attribution', () => {
    const line = makeLine({
      parserBackend: 'booknlp',
      legacyRule: '4b_tag_continuation',
      jevVerification: {
        called: true,
        cacheHit: false,
        choice: 'Sergeant Jaha',
        confidence: 0.8,
        heuristicSpeaker: 'Rashid',
      },
      candidates: [{ name: 'Rashid', confidence: 0.9, reasons: ['parser_match'] }],
      decisionTrace: { selectedCandidate: 'Rashid', selectedReasons: ['parser_match'] },
    });
    const [nameMap, idMap] = makeMaps();
    const result = convertLineToDialogueLine(line, 1, nameMap, idMap, 0, [line]);

    expect(result.attribution?.legacyRule).toBe('4b_tag_continuation');
    expect(result.attribution?.jevVerification).toEqual({
      called: true,
      cacheHit: false,
      choice: 'Sergeant Jaha',
      confidence: 0.8,
      heuristicSpeaker: 'Rashid',
    });
  });

  it('leaves the audit fields null when the parser sends none', () => {
    const line = makeLine({
      candidates: [{ name: 'Rashid', confidence: 0.9 }],
    });
    const [nameMap, idMap] = makeMaps();
    const result = convertLineToDialogueLine(line, 1, nameMap, idMap, 0, [line]);

    expect(result.attribution?.legacyRule).toBeNull();
    expect(result.attribution?.jevVerification).toBeNull();
  });
});
