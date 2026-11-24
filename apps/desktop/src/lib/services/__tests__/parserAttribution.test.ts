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

describe('convertLineToDialogueLine — shared policy spec, frontend edge', () => {
  function lowConfidenceLine(decisionTrace: any) {
    const line = makeLine({
      candidates: [
        { name: 'Rashid', confidence: 0.6, reasons: ['parser_match'] },
        { name: 'Sergeant Jaha', confidence: 0.5, reasons: [] },
      ],
      decisionTrace,
    });
    line.is_suggestion = true;
    return line;
  }

  it('asserts a first-person decision from the wire ruleCategory', () => {
    const line = lowConfidenceLine({
      selectedCandidate: 'Rashid',
      selectedReasons: ['parser_match', 'first_person_override'],
      ruleCategory: 'FIRST_PERSON',
    });
    const [nameMap, idMap] = makeMaps();
    const result = convertLineToDialogueLine(line, 1, nameMap, idMap, 0, [line]);

    expect(result.characterId).toBe('YY3GE4HN');
    expect(result.attribution?.resolutionStatus).toBe('auto');
    expect(result.attribution?.decisionTrace?.ruleCategory).toBe('FIRST_PERSON');
  });

  it('trusts the wire ruleCategory over the selected reasons', () => {
    const line = lowConfidenceLine({
      selectedCandidate: 'Rashid',
      selectedReasons: ['first_person_override'],
      ruleCategory: 'HARD',
    });
    const [nameMap, idMap] = makeMaps();
    const result = convertLineToDialogueLine(line, 1, nameMap, idMap, 0, [line]);

    expect(result.characterId).toBeNull();
    expect(result.attribution?.resolutionStatus).toBe('unknown');
  });

  it('falls back to the legacy first-person rules on a trace without ruleCategory', () => {
    const line = lowConfidenceLine({
      selectedCandidate: 'Rashid',
      selectedReasons: ['0p_protagonist_first_person'],
    });
    const [nameMap, idMap] = makeMaps();
    const result = convertLineToDialogueLine(line, 1, nameMap, idMap, 0, [line]);

    expect(result.characterId).toBe('YY3GE4HN');
  });

  it('un-asserts a contested parser assert below the contested threshold', () => {
    const line = makeLine({
      candidates: [
        { name: 'Rashid', confidence: 0.8, reasons: ['parser_match'] },
        { name: 'Sergeant Jaha', confidence: 0.4, reasons: [] },
      ],
      decisionTrace: { selectedCandidate: 'Rashid', selectedReasons: ['parser_match'], ruleCategory: 'LOCAL_EVIDENCE' },
    });
    const [nameMap, idMap] = makeMaps();
    nameMap.set('sergeant jaha', 'JAHA0001');
    idMap.set('JAHA0001', 'Sergeant Jaha');
    const result = convertLineToDialogueLine(line, 1, nameMap, idMap, 0, [line]);

    expect(result.characterId).toBeNull();
    expect(result.isConflict).toBe(true);
  });
});
