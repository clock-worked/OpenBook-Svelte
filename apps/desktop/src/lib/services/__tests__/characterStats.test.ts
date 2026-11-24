// ============================================================================
// characterStats.ts — the one answer for character stats (candidate 5).
// ============================================================================

import { describe, expect, it } from 'vitest';

import type { Character } from '../../types';
import {
  chapterLineCounts,
  characterChapterCount,
  characterLineCount,
  characterStats,
} from '../characterStats';

function character(overrides: Partial<Character> & { name: string }): Character {
  return {
    guid: null,
    id: overrides.name.toLowerCase(),
    gender: 'Unknown',
    aliases: [],
    descriptors: [],
    color: null,
    notes: '',
    firstAppearance: null,
    stats: { totalLines: 0, chapterCount: 0 },
    ...overrides,
  } as Character;
}

describe('characterStats (book-wide answer)', () => {
  it('reads the accumulated stats on the record', () => {
    const alice = character({ name: 'Alice', stats: { totalLines: 12, chapterCount: 3 } });
    expect(characterStats(alice)).toEqual({ totalLines: 12, chapterCount: 3 });
    expect(characterLineCount(alice)).toBe(12);
    expect(characterChapterCount(alice)).toBe(3);
  });

  it('accumulated stats win over the legacy count mirrors', () => {
    const alice = character({ name: 'Alice', stats: { totalLines: 0, chapterCount: 0 }, count: 9, chapterCount: 2 });
    expect(characterStats(alice)).toEqual({ totalLines: 0, chapterCount: 0 });
  });

  it('falls back to the legacy mirrors only when stats are absent', () => {
    const legacy = { count: 7, chapterCount: 2 } as Partial<Character>;
    expect(characterStats(legacy)).toEqual({ totalLines: 7, chapterCount: 2 });
  });

  it('answers zero for missing, empty or negative values', () => {
    expect(characterStats(null)).toEqual({ totalLines: 0, chapterCount: 0 });
    expect(characterStats({})).toEqual({ totalLines: 0, chapterCount: 0 });
    expect(characterLineCount({ stats: { totalLines: -4, chapterCount: 1 } })).toBe(0);
  });
});

describe('chapterLineCounts (per-chapter view)', () => {
  const GA = 'AA01BB02';
  const roster = [
    character({ name: 'Alice', guid: GA, id: GA, aliases: ['Al'] }),
    character({ name: 'Bob', id: 'bob' }),
  ];

  it('resolves GUIDs, ids, aliases and names to the canonical name; aliases mirror it', () => {
    const counts = chapterLineCounts(
      [
        { characterId: GA },
        { characterId: GA.toLowerCase() },
        { chosenSpeaker: 'Al' },
        { characterId: 'bob' },
        { chosenSpeaker: 'BOB' },
        { characterId: null, chosenSpeaker: null },
      ],
      roster,
    );
    expect(counts.get('Alice')).toBe(3);
    expect(counts.get('Al')).toBe(3);
    expect(counts.get('Bob')).toBe(2);
  });

  it('keeps unknown identities under their raw key and handles empty input', () => {
    expect(chapterLineCounts([{ chosenSpeaker: 'Stranger' }], roster).get('Stranger')).toBe(1);
    expect(chapterLineCounts(null, roster).size).toBe(0);
  });
});
