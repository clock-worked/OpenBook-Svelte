// ============================================================================
// Vitest service-layer matrix — parser.ts (resolved-catalog wiring).
//
// The regression under test (TASK 3): the parser builds its parser options
// from a RESOLVED character catalog (v3 characters/ folder first, legacy
// characters.json fallback — chapterCharacterRepository.resolveBookCharacters),
// never from a raw path. Consequences pinned here:
//   - a v3-folder book sends a NON-EMPTY character_catalog with
//     closed_world_characters: true (previously the catalog was empty and
//     the flag was dropped);
//   - v3 books never rewrite characters.json (the folder stays the single
//     source of truth, IN-2) and the folder files stay byte-identical;
//   - v2 books without a catalog still bootstrap parsed names into
//     characters.json;
//   - v2 books WITH a catalog keep unknown parsed names provisional.
//
// I/O is stubbed by the in-memory fs mock (helpers/inMemoryFs.ts); the
// backend HTTP client and the stores are mocked below.
// ============================================================================

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { writable } from 'svelte/store';
import { inMemoryFs } from './helpers/inMemoryFs';
import { makeCharacterFile } from './helpers/fixtures';

vi.mock('$lib/services/fs', async () => {
  const mod = await import('./helpers/inMemoryFs');
  return mod.inMemoryFs;
});

// The parse endpoint is the only one this matrix exercises (the chapter text
// comes from the fs mock, so readText is never hit).
vi.mock('$lib/services/apiClient', () => ({
  API_ENDPOINTS: {
    parse: '/api/parse',
    readText: '/api/read-text',
  },
  apiPostJson: vi.fn(),
  toApiClientError: (error: unknown) => ({
    type: 'network',
    message: String((error as { message?: string })?.message ?? error),
  }),
}));

// Pure re-implementations of the two map builders (stores/characters.ts:379-409)
// so the parser's map logic runs against the real shapes without loading the
// full store graph.
vi.mock('$lib/stores/characters', () => ({
  buildNameToIdMap: (charactersArray: Array<{ id?: string; name?: string; aliases?: unknown }>) => {
    const map = new Map<string, string>();
    for (const char of charactersArray) {
      if (char.id && char.name) map.set(String(char.name).toLowerCase(), char.id);
    }
    for (const char of charactersArray) {
      if (!char.id || !Array.isArray(char.aliases)) continue;
      for (const alias of char.aliases) {
        const key = String(alias ?? '').toLowerCase().trim();
        if (key && !map.has(key)) map.set(key, char.id);
      }
    }
    return map;
  },
  buildIdToNameMap: (charactersArray: Array<{ id?: string; name?: string }>) => {
    const map = new Map<string, string>();
    for (const char of charactersArray) {
      if (char.id && char.name) map.set(String(char.id).toLowerCase(), char.name);
    }
    return map;
  },
}));

vi.mock('$lib/stores/settings', () => ({
  parserHints: writable({}),
}));

vi.mock('$lib/stores/jevDialogueAi', () => ({
  setJevVerifySummary: vi.fn(),
}));

import type { Line } from '$lib/types';
import { API_ENDPOINTS, apiPostJson } from '$lib/services/apiClient';
import { setJevVerifySummary } from '$lib/stores/jevDialogueAi';
import { runParserForChapter } from '../parser';

function textLine(id: number, text: string, speaker: string, line_type: 'narration' | 'dialogue'): Line {
  return {
    id,
    segments: [{ type: 'text', value: text }],
    speaker,
    line_type,
    is_suggestion: false,
    suggestions: [],
  };
}

function mockParseResponse(script: Line[], characters: string[]): void {
  vi.mocked(apiPostJson).mockImplementation(async (endpoint: string) => {
    if (endpoint === API_ENDPOINTS.parse) {
      return { script, characters, meta: { version: '3.2', jevVerify: null } };
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  });
}

function lastParseOptions(): Record<string, any> {
  const call = vi.mocked(apiPostJson).mock.calls.find((entry) => entry[0] === API_ENDPOINTS.parse);
  expect(call).toBeDefined();
  return (call![1] as { parser_options: Record<string, any> }).parser_options;
}

beforeEach(() => {
  inMemoryFs.__reset();
  vi.clearAllMocks();
});

describe('parser.runParserForChapter (resolved catalog)', () => {
  it('v3-folder book: the parse payload carries a NON-EMPTY character_catalog (closed world) and every character file stays byte-identical', async () => {
    inMemoryFs.__seed({
      'Ch1/chapter.txt': 'Alice said "We should leave" and packed her bag.',
      'characters/Alice.json': makeCharacterFile({ guid: 'AB12CD34', title: 'Alice', gender: 'Female' }),
      // A decoy legacy store that MUST NOT be read as the catalog nor rewritten.
      'characters.json': {
        formatVersion: '2.0',
        characters: [
          {
            guid: null, id: 'bob', name: 'Bob', gender: 'Male', aliases: [],
            color: null, notes: '', firstAppearance: null, stats: { totalLines: 0, chapterCount: 0 },
          },
        ],
      },
    });
    const before = inMemoryFs.__snapshot();

    mockParseResponse([
      textLine(1, 'Alice said "We should leave" and packed her bag.', 'Alice', 'narration'),
      textLine(2, 'We should leave.', 'Alice', 'dialogue'),
    ], ['Alice']);

    const result = await runParserForChapter({ input: 'Ch1/chapter.txt' });

    expect(result.ok).toBe(true);
    expect(result.chapter).toBe('Ch1');
    expect(result.numLines).toBe(2);
    expect(result.scriptPath).toBe('Ch1/dialogue.json');

    // The regression: the RESOLVED v3 catalog is what the backend receives.
    const parserOptions = lastParseOptions();
    expect(parserOptions.closed_world_characters).toBe(true);
    expect(parserOptions.character_catalog).toEqual([
      expect.objectContaining({ characterId: 'AB12CD34', name: 'Alice' }),
    ]);

    // IN-2: a v3 book never touches characters.json; the folder is untouched.
    const after = inMemoryFs.__snapshot();
    expect(after['characters.json']).toBe(before['characters.json']);
    expect(after['characters/Alice.json']).toBe(before['characters/Alice.json']);

    // Dialogue written; the Alice line resolved to the folder's GUID.
    const dialogue = inMemoryFs.__readJson('Ch1/dialogue.json');
    expect(dialogue.chapterId).toBe('Ch1');
    expect(dialogue.lines[0].characterId).toBe('narrator');
    expect(dialogue.lines[1].characterId).toBe('AB12CD34');

    expect(setJevVerifySummary).toHaveBeenCalledWith(null);
  });

  it('empty v2 book: parsed names are bootstrapped into characters.json (2.0) and no closed-world flag is sent', async () => {
    inMemoryFs.__seed({
      'Ch2/chapter.txt': 'Bob called "Tomorrow then" and checked the platform.',
      'characters.json': { formatVersion: '2.0', characters: [] },
    });

    mockParseResponse([
      textLine(1, 'Bob called "Tomorrow then" and checked the platform.', 'Bob', 'narration'),
      textLine(2, 'Tomorrow then.', 'Bob', 'dialogue'),
    ], ['Alice', 'Bob']);

    const result = await runParserForChapter({ input: 'Ch2/chapter.txt' });

    expect(result.ok).toBe(true);

    // Empty catalog -> base options pass through unchanged (no closed world).
    const parserOptions = lastParseOptions();
    expect(parserOptions.closed_world_characters).toBeUndefined();
    expect(parserOptions.character_catalog).toBeUndefined();
    expect(parserOptions.parser_backend).toBe('legacy');

    // Both parsed names bootstrapped into the rewritten central store.
    const store = inMemoryFs.__readJson('characters.json');
    expect(store.formatVersion).toBe('2.0');
    expect(store.characters).toEqual([
      {
        guid: null, id: 'alice', name: 'Alice', gender: 'Unknown', aliases: [],
        color: null, notes: '', firstAppearance: 'Ch2', stats: { totalLines: 0, chapterCount: 0 },
      },
      {
        guid: null, id: 'bob', name: 'Bob', gender: 'Unknown', aliases: [],
        color: null, notes: '', firstAppearance: 'Ch2', stats: { totalLines: 0, chapterCount: 0 },
      },
    ]);

    // Dialogue resolves the speaker to the freshly bootstrapped id.
    const dialogue = inMemoryFs.__readJson('Ch2/dialogue.json');
    expect(dialogue.lines[1].characterId).toBe('bob');
  });

  it('v2 book with a catalog: an unknown parsed name stays provisional (no bootstrap, characters.json byte-identical, dialogue abstains)', async () => {
    inMemoryFs.__seed({
      'Ch3/chapter.txt': 'Alice waited "It is not your fault" and watched the door.',
      'characters.json': {
        formatVersion: '2.0',
        characters: [
          {
            guid: null, id: 'alice', name: 'Alicia', gender: 'Female', aliases: [],
            color: null, notes: '', firstAppearance: 'Ch1', stats: { totalLines: 4, chapterCount: 2 },
          },
        ],
      },
    });
    const before = inMemoryFs.__snapshot();

    mockParseResponse([
      textLine(1, 'Alice waited "It is not your fault" and watched the door.', 'Alice', 'narration'),
      textLine(2, 'It is not your fault.', 'Alice', 'dialogue'),
    ], ['Alice']);

    const result = await runParserForChapter({ input: 'Ch3/chapter.txt' });

    expect(result.ok).toBe(true);

    // Closed-world options built from the legacy catalog.
    const parserOptions = lastParseOptions();
    expect(parserOptions.closed_world_characters).toBe(true);
    expect(parserOptions.character_catalog).toEqual([
      expect.objectContaining({ characterId: 'alice', name: 'Alicia' }),
    ]);

    // "Alice" is not in the catalog (name/alias "alicia") -> it stays
    // provisional: the central store is NOT rewritten...
    const after = inMemoryFs.__snapshot();
    expect(after['characters.json']).toBe(before['characters.json']);

    // ...and the dialogue line abstains rather than inventing an id.
    const dialogue = inMemoryFs.__readJson('Ch3/dialogue.json');
    expect(dialogue.lines[1].characterId).toBeNull();
    expect(dialogue.lines[1].attribution.resolutionStatus).toBe('unknown');
  });
});
