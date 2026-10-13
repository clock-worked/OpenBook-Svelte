import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/stores/bookState', async () => {
  const { writable } = await import('svelte/store');
  return {
    audioRoot: writable(null),
    bookRoot: writable(null),
    bookRootAbsolutePath: writable(null),
    voiceSamplesRoot: writable(null),
  };
});
vi.mock('$lib/stores/settings', async () => {
  const { writable } = await import('svelte/store');
  return { bookRootPathOverride: writable(null) };
});
vi.mock('../persistence', () => ({ storeProjectHandle: vi.fn() }));
vi.mock('../apiClient', () => ({
  API_ENDPOINTS: {},
  ApiClientError: class ApiClientError extends Error {},
  apiFetch: vi.fn(),
  apiPostJson: vi.fn(),
  apiPostVoid: vi.fn(),
  apiRequestJson: vi.fn(),
  toApiUrl: vi.fn((path: string) => path),
  toApiClientError: vi.fn(),
}));

import { audioRoot, bookRootAbsolutePath } from '$lib/stores/bookState';
import { bookRootPathOverride } from '$lib/stores/settings';
import { audioRootFor } from '../audio';
import { planAdvancedGeneration } from '../audioQueuePlanning';
import type {
  AdvancedBookPreview,
  PlannerVoiceInfo,
  PlannedCharacterBatch,
  PlannedChapterBatch,
  PlannedDialogueLine,
} from '../audioQueuePlanning';
import type { DialogueLine } from '$lib/types';

// === Fixtures ===

function makeLine(id: number, text: string): DialogueLine {
  return {
    id,
    characterId: null,
    text,
    span: null,
    metadata: { emotion: null, intensity: 1, pacing: null, prefix: null, customTags: {} },
    candidates: [],
    isConflict: false,
  };
}

function withContext(
  line: DialogueLine,
  chapterTitle: string,
  sourceFile: string,
  characterName: string,
): PlannedDialogueLine {
  return { ...line, chapterTitle, sourceFile, characterName };
}

const aliceVoice: PlannerVoiceInfo = {
  voiceId: 'v1',
  provider: 'elevenlabs',
  displayName: 'Alice Voice',
  characterId: 'alice',
};

function makePreview(
  characters: PlannedCharacterBatch[],
  chapterBatches: PlannedChapterBatch[] = [],
): AdvancedBookPreview {
  return {
    totalChapterCount: 2,
    parsedChapterCount: 2,
    skippedUnparsedCount: 0,
    totalMissingLines: characters.reduce((sum, batch) => sum + batch.lines.length, 0),
    missingVoiceLineCount: 0,
    missingVoiceCharacterCount: 0,
    characters,
    chapterBatches,
    chapterOverview: [],
  };
}

// === audioRootFor ===

describe('audioRootFor', () => {
  beforeEach(() => {
    audioRoot.set(null);
    bookRootAbsolutePath.set(null);
    bookRootPathOverride.set(null);
  });

  it('prefers the explicit audio root', () => {
    audioRoot.set('/roots/audio');
    bookRootAbsolutePath.set('/roots/book');
    bookRootPathOverride.set('/roots/override');

    expect(audioRootFor()).toBe('/roots/audio');
  });

  it('falls back to the book absolute path', () => {
    bookRootAbsolutePath.set('/roots/book');
    bookRootPathOverride.set('/roots/override');

    expect(audioRootFor()).toBe('/roots/book');
  });

  it('falls back to the settings book root override', () => {
    bookRootPathOverride.set('/roots/override');

    expect(audioRootFor()).toBe('/roots/override');
  });

  it('returns null when nothing is configured', () => {
    expect(audioRootFor()).toBeNull();
  });
});

// === planAdvancedGeneration ===

describe('planAdvancedGeneration', () => {
  it('character mode: one job per selected character with cross-chapter lines', () => {
    const lines = [
      withContext(makeLine(1, 'ab'), 'Ch1', 'Ch1/chapter.txt', 'Alice'),
      withContext(makeLine(2, 'abcd'), 'Ch2', 'Ch2/chapter.txt', 'Alice'),
      withContext(makeLine(3, 'x'), 'Ch1', 'Ch1/chapter.txt', 'Alice'),
    ];
    const preview = makePreview([
      {
        characterId: 'alice',
        characterName: 'Alice',
        voiceInfo: aliceVoice,
        lines,
        totalCharacters: 7,
        chapterCount: 2,
        chapterBatchCount: 2,
        underTargetChapterBatchCount: 0,
      },
    ]);

    const plan = planAdvancedGeneration(preview, {
      mode: 'character',
      selectedCharacterIds: new Set(['alice']),
      generationOptions: { useFillerForShortBatch: true },
    });

    expect(plan.jobsQueued).toBe(1);
    expect(plan.linesQueued).toBe(3);

    const [job] = plan.jobs;
    expect(job.jobIdBase).toBe('advanced-character-alice');
    expect(job.displayName).toBe('Alice');
    expect(job.characterId).toBe('alice');
    // Chapter/source resolve from the first queued line.
    expect(job.chapterTitle).toBe('Ch1');
    expect(job.sourceFile).toBe('Ch1/chapter.txt');
    expect(job.cumulativeCharacterCounts).toEqual([2, 6, 7]);
    expect(job.totalCharacters).toBe(7);
    expect(job.generationOptions).toEqual({ useFillerForShortBatch: true });

    // Per-chapter totals count each of the character's lines in that chapter.
    expect(plan.chapterProcessableTotals.get('Ch1')).toBe(2);
    expect(plan.chapterProcessableTotals.get('Ch2')).toBe(1);
  });

  it('character mode: skips unselected characters', () => {
    const preview = makePreview([
      {
        characterId: 'alice',
        characterName: 'Alice',
        voiceInfo: aliceVoice,
        lines: [withContext(makeLine(1, 'ab'), 'Ch1', 'Ch1/chapter.txt', 'Alice')],
        totalCharacters: 2,
        chapterCount: 1,
        chapterBatchCount: 1,
        underTargetChapterBatchCount: 0,
      },
    ]);

    const plan = planAdvancedGeneration(preview, {
      mode: 'character',
      selectedCharacterIds: new Set<string>(),
    });

    expect(plan.jobs).toEqual([]);
    expect(plan.jobsQueued).toBe(0);
    expect(plan.linesQueued).toBe(0);
  });

  it('chapter mode: one job per chapter batch with the batch chapter/source', () => {
    const chapterBatches: PlannedChapterBatch[] = [
      {
        chapterTitle: 'Ch2',
        sourceFile: 'Ch2/chapter.txt',
        characterId: 'alice',
        characterName: 'Alice',
        voiceInfo: aliceVoice,
        lines: [withContext(makeLine(4, 'abcd'), 'Ch2', 'Ch2/chapter.txt', 'Alice')],
        totalCharacters: 4,
      },
      {
        chapterTitle: 'Ch1',
        sourceFile: 'Ch1/chapter.txt',
        characterId: 'alice',
        characterName: 'Alice',
        voiceInfo: aliceVoice,
        lines: [
          withContext(makeLine(5, 'abc'), 'Ch1', 'Ch1/chapter.txt', 'Alice'),
          withContext(makeLine(6, 'de'), 'Ch1', 'Ch1/chapter.txt', 'Alice'),
        ],
        totalCharacters: 5,
      },
    ];
    const preview = makePreview([], chapterBatches);

    const plan = planAdvancedGeneration(preview, {
      mode: 'chapter',
      selectedCharacterIds: new Set(['alice']),
    });

    expect(plan.jobsQueued).toBe(2);
    expect(plan.linesQueued).toBe(3);
    expect(plan.jobs.map((job) => job.jobIdBase)).toEqual([
      'advanced-chapter-Ch2-alice',
      'advanced-chapter-Ch1-alice',
    ]);
    expect(plan.jobs[0].chapterTitle).toBe('Ch2');
    expect(plan.jobs[0].sourceFile).toBe('Ch2/chapter.txt');
    expect(plan.jobs[1].chapterTitle).toBe('Ch1');
    expect(plan.jobs[1].sourceFile).toBe('Ch1/chapter.txt');
    expect(plan.jobs.every((job) => job.displayName === 'Alice')).toBe(true);
    expect(plan.chapterProcessableTotals.get('Ch2')).toBe(1);
    expect(plan.chapterProcessableTotals.get('Ch1')).toBe(2);
  });

  it('speaker-source mode: groups characters sharing a voice into one job', () => {
    const sharedVoice: PlannerVoiceInfo = {
      voiceId: 'v-123',
      provider: 'elevenlabs',
      displayName: 'Narrator Voice',
      characterId: 'alice',
    };
    const preview = makePreview([
      {
        characterId: 'alice',
        characterName: 'Alice',
        voiceInfo: sharedVoice,
        lines: [withContext(makeLine(1, 'ab'), 'Ch1', 'Ch1/chapter.txt', 'Alice')],
        totalCharacters: 2,
        chapterCount: 1,
        chapterBatchCount: 1,
        underTargetChapterBatchCount: 0,
      },
      {
        characterId: 'bob',
        characterName: 'Bob',
        voiceInfo: sharedVoice,
        lines: [withContext(makeLine(2, 'abc'), 'Ch1', 'Ch1/chapter.txt', 'Bob')],
        totalCharacters: 3,
        chapterCount: 1,
        chapterBatchCount: 1,
        underTargetChapterBatchCount: 0,
      },
    ]);

    const plan = planAdvancedGeneration(preview, {
      mode: 'speaker-source',
      selectedCharacterIds: new Set(['alice', 'bob']),
    });

    expect(plan.jobsQueued).toBe(1);
    expect(plan.linesQueued).toBe(2);

    const [job] = plan.jobs;
    // provider::voiceId sanitized to [a-zA-Z0-9_-] (each colon → one underscore)
    expect(job.jobIdBase).toBe('advanced-speaker-source-elevenlabs__v-123');
    expect(job.displayName).toBe('Narrator Voice (speaker source)');
    // Primary character is the first selected character with the voice.
    expect(job.characterId).toBe('alice');
    expect(job.characterName).toBe('Alice');
    expect(job.lines.map((line) => line.characterName)).toEqual(['Alice', 'Bob']);
    expect(plan.chapterProcessableTotals.get('Ch1')).toBe(2);
  });
});
