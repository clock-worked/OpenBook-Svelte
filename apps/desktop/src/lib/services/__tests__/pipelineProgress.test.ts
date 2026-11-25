// The pipeline progress poller (services/audioPipelineProgress.ts).
//
// Phase/scope names must agree with the BE record owner
// (py_services/audio/pipeline_progress.py) on tests/pipeline_progress_vectors.json,
// which the Python side asserts in py_services/test_pipeline_progress.py.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiRequestJson = vi.fn();
vi.mock('$lib/services/apiClient', () => ({
  API_ENDPOINTS: {
    vibevoiceCharacterProgress: (params: { characterName: string; chapterTitle?: string }) =>
      `progress?c=${params.characterName}&t=${params.chapterTitle ?? ''}`,
  },
  apiRequestJson: (url: string) => apiRequestJson(url),
}));

import {
  PIPELINE_PHASES,
  PIPELINE_SCOPES,
  STARTED_AT_SKEW_MS,
  progressLabel,
  startCharacterPipelineProgressPolling,
  toPipelineProgressRecord,
  type PipelineProgressRecord,
} from '../audioPipelineProgress';

interface ShapeVectors {
  phases: string[];
  scopes: string[];
  recordKeys: string[];
}

const vectors: ShapeVectors = JSON.parse(
  readFileSync(
    fileURLToPath(new URL('../../../../../../tests/pipeline_progress_vectors.json', import.meta.url)),
    'utf-8',
  ),
);

const REQUEST_START = Date.parse('2026-11-18T12:00:00Z');
const iso = (offsetMs: number) => new Date(REQUEST_START + offsetMs).toISOString();

function raw(fields: Record<string, unknown>) {
  return { phase: 'unknown', startedAt: iso(0), ...fields };
}

describe('shape vector parity', () => {
  it('phases match the BE enum, in run order', () => {
    expect([...PIPELINE_PHASES]).toEqual(vectors.phases);
  });
  it('scopes match', () => {
    expect([...PIPELINE_SCOPES]).toEqual(vectors.scopes);
  });
  it('a normalized record has exactly the shared keys', () => {
    expect(Object.keys(toPipelineProgressRecord({ phase: 'done' })).sort()).toEqual(
      [...vectors.recordKeys].sort(),
    );
  });
});

describe('toPipelineProgressRecord', () => {
  it('reads scope from the record', () => {
    expect(toPipelineProgressRecord({ phase: 'aligning', scope: 'cross' }).scope).toBe('cross');
    expect(toPipelineProgressRecord({ phase: 'aligning' }).scope).toBeNull();
  });
  it('unknown phase strings read as unknown', () => {
    expect(toPipelineProgressRecord({ phase: 'warming-up' }).phase).toBe('unknown');
  });
  it('chunk counts only count while generating', () => {
    const gen = toPipelineProgressRecord({ phase: 'generating', chunkIndex: 3, chunkTotal: 7 });
    expect([gen.chunkIndex, gen.chunkTotal]).toEqual([3, 7]);
    const later = toPipelineProgressRecord({ phase: 'transcribing', chunkIndex: 7, chunkTotal: 7 });
    expect([later.chunkIndex, later.chunkTotal]).toEqual([null, null]);
  });
});

describe('progressLabel', () => {
  it('shows true chunk progress while generating', () => {
    const record = toPipelineProgressRecord({ phase: 'generating', chunkIndex: 3, chunkTotal: 7 });
    expect(progressLabel(record)).toBe('Generating speech… (chunk 3 of 7)');
  });
  it('is the phase label otherwise', () => {
    expect(progressLabel(toPipelineProgressRecord({ phase: 'failed', detail: 'boom' }))).toBe(
      'Failed: boom',
    );
  });
});

describe('startCharacterPipelineProgressPolling', () => {
  let seen: PipelineProgressRecord[];

  beforeEach(() => {
    vi.useFakeTimers();
    apiRequestJson.mockReset();
    seen = [];
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const start = (intervalMs = 1000) =>
    startCharacterPipelineProgressPolling({
      characterName: 'Rashid',
      chapterTitle: 'Ch 1',
      requestStart: REQUEST_START,
      intervalMs,
      onProgress: (record) => seen.push(record),
    });

  it('asks by character and chapter only (no scope derivation)', async () => {
    apiRequestJson.mockResolvedValue(raw({ phase: 'starting' }));
    const stop = start();
    await vi.advanceTimersByTimeAsync(0);
    stop();
    expect(apiRequestJson).toHaveBeenCalledWith('progress?c=Rashid&t=Ch 1');
  });

  it('drops records from an earlier run (startedAt before the skew buffer)', async () => {
    apiRequestJson
      .mockResolvedValueOnce(raw({ phase: 'done', startedAt: iso(-STARTED_AT_SKEW_MS - 1) }))
      .mockResolvedValueOnce(raw({ phase: 'starting', startedAt: iso(-STARTED_AT_SKEW_MS + 1) }));
    const stop = start();
    await vi.advanceTimersByTimeAsync(0);
    expect(seen).toEqual([]);
    await vi.advanceTimersByTimeAsync(1000);
    stop();
    expect(seen.map((r) => r.phase)).toEqual(['starting']);
  });

  it('emits only when the phase or chunk counter changes', async () => {
    const sequence = [
      raw({ phase: 'generating', chunkIndex: 1, chunkTotal: 3 }),
      raw({ phase: 'generating', chunkIndex: 1, chunkTotal: 3 }),
      raw({ phase: 'generating', chunkIndex: 2, chunkTotal: 3 }),
      raw({ phase: 'transcribing', chunkIndex: 3, chunkTotal: 3 }),
      raw({ phase: 'transcribing', chunkIndex: 3, chunkTotal: 3 }),
    ];
    for (const entry of sequence) apiRequestJson.mockResolvedValueOnce(entry);
    const stop = start();
    await vi.advanceTimersByTimeAsync(4000);
    stop();
    expect(seen.map((r) => [r.phase, r.chunkIndex])).toEqual([
      ['generating', 1],
      ['generating', 2],
      ['transcribing', null],
    ]);
  });

  it('swallows poll errors and keeps polling', async () => {
    apiRequestJson
      .mockRejectedValueOnce(new Error('404'))
      .mockResolvedValueOnce(raw({ phase: 'aligning', scope: 'cross' }));
    const stop = start();
    await vi.advanceTimersByTimeAsync(1000);
    stop();
    expect(seen.map((r) => [r.phase, r.scope])).toEqual([['aligning', 'cross']]);
  });

  it('never overlaps polls (in-flight guard)', async () => {
    let resolveSlow: (value: unknown) => void = () => {};
    apiRequestJson.mockImplementationOnce(() => new Promise((resolve) => (resolveSlow = resolve)));
    const stop = start();
    await vi.advanceTimersByTimeAsync(3000);
    expect(apiRequestJson).toHaveBeenCalledTimes(1);
    resolveSlow(raw({ phase: 'starting' }));
    await vi.advanceTimersByTimeAsync(0);
    stop();
    expect(seen.map((r) => r.phase)).toEqual(['starting']);
  });

  it('emits nothing after stop, even for a poll already in flight', async () => {
    let resolveSlow: (value: unknown) => void = () => {};
    apiRequestJson.mockImplementationOnce(() => new Promise((resolve) => (resolveSlow = resolve)));
    const stop = start();
    await vi.advanceTimersByTimeAsync(0);
    stop();
    resolveSlow(raw({ phase: 'done' }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(seen).toEqual([]);
    expect(apiRequestJson).toHaveBeenCalledTimes(1);
  });
});
