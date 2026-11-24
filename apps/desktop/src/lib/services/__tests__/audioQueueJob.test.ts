import { describe, expect, it } from 'vitest';

import {
  applyQueueEvent,
  createQueueJob,
  emptyQueueState,
  isCancelRequested,
  nextQueuedJob,
  restoreQueueState,
  runningJob,
  serializeQueueState,
  type QueueEvent,
  type QueueJob,
  type QueueState,
} from '../audioQueueJob';
import {
  planAdvancedGeneration,
  planAdvancedPreview,
  planChapterCharacter,
  planWholeBookMissing,
  rebuildQueuedJobPayload,
  type AudioPlannerContext,
  type PlannedQueueJob,
  type PlannerVoiceInfo,
  type RebuildPayloadDeps,
} from '../audioQueuePlanning';
import type { PipelineProgressRecord } from '../audioPipelineProgress';
import type { ChapterStatus } from '$lib/stores/bookState';

// === Fake book environment (shared by the planner and the rebuild) ===

interface FakeLine {
  id: number;
  text: string;
  characterId: string | null;
}

function line(id: number, characterId: string | null, text = `line ${id} text`): FakeLine {
  return { id, characterId, text };
}

function makeEnv() {
  const chapters = [
    { title: 'Ch1', path: 'Ch1/chapter.txt', parsed: true },
    { title: 'Ch2', path: 'Ch2/chapter.txt', parsed: true },
  ] as unknown as ChapterStatus[];
  const scripts: Record<string, { lines: FakeLine[] }> = {
    Ch1: { lines: [line(1, 'ALICE'), line(2, 'BOB'), line(3, 'ALICE', 'a much longer alice line'), line(4, 'CARL'), line(5, null)] },
    Ch2: { lines: [line(1, 'ALICE'), line(2, 'ALICE'), line(3, 'BOB', 'bob in chapter two')] },
  };
  const names: Record<string, string> = { ALICE: 'Alice', BOB: 'Bob', CARL: 'Carl' };
  // ALICE and BOB share one speaker source; CARL has no voice.
  const voices: Record<string, PlannerVoiceInfo> = {
    ALICE: { voiceId: 'v1', provider: 'vibevoice', displayName: 'Narrator Voice', characterId: 'ALICE' },
    BOB: { voiceId: 'v1', provider: 'vibevoice', displayName: 'Narrator Voice', characterId: 'BOB' },
  };
  const present = new Set<string>();
  const audioKey = (chapterTitle: string, characterName: string, lineId: number) =>
    `${chapterTitle}|${characterName}|${lineId}`;
  let diskReads = 0;

  const readScript = async (title: string) => {
    diskReads += 1;
    const script = scripts[title];
    return script ? { lines: script.lines.map((entry) => ({ ...entry })) } : null;
  };
  const isLineAudioPresent = async (args: { chapterTitle: string; characterName: string; lineId: number }) =>
    present.has(audioKey(args.chapterTitle, args.characterName, args.lineId));
  const getVoice = (id: string) => voices[id] ?? null;

  const ctx: AudioPlannerContext = {
    chapters,
    batchTarget: 800,
    readScriptForChapter: readScript,
    isLineAudioPresent,
    resolveCharacterIdFromLine: (entry: any) => entry?.characterId ?? null,
    resolveCharacterName: (id) => names[id] ?? id,
    getVoice,
    getChapterSourceFile: (chapter) => chapter.path || `${chapter.title}/chapter.txt`,
  };

  const deps: RebuildPayloadDeps = {
    resolveChapterPath: (title) => (chapters as any[]).find((chapter) => chapter.title === title)?.path,
    resolveScript: readScript,
    resolveCharacterName: (id, fallback) => names[id] ?? fallback ?? id,
    getVoice,
    filterCharacterLines: (id, _name, script) => script.lines.filter((entry: FakeLine) => entry.characterId === id),
    isLineAudioPresent,
  };

  return {
    ctx,
    deps,
    scripts,
    voices,
    markPresent: (chapterTitle: string, characterName: string, lineId: number) =>
      present.add(audioKey(chapterTitle, characterName, lineId)),
    diskReads: () => diskReads,
  };
}

// === Job/state fixtures ===

const T0 = 1_000_000;

function plannedJob(overrides: Partial<PlannedQueueJob> = {}): PlannedQueueJob {
  return {
    jobIdBase: 'ALICE-missing',
    displayName: 'Alice',
    characterId: 'ALICE',
    characterName: 'Alice',
    lines: [
      { id: 1, text: 'abc', chapterTitle: 'Ch1' } as any,
      { id: 3, text: 'defgh', chapterTitle: 'Ch1' } as any,
    ],
    cumulativeCharacterCounts: [3, 8],
    totalCharacters: 8,
    voiceInfo: { voiceId: 'v1', provider: 'vibevoice', displayName: 'Voice', characterId: 'ALICE' },
    chapterTitle: 'Ch1',
    sourceFile: 'Ch1/chapter.txt',
    ...overrides,
  };
}

function job(id: string, overrides: Partial<PlannedQueueJob> = {}): QueueJob {
  return createQueueJob(plannedJob(overrides), { id, generationMode: 'missing', createdAt: T0 });
}

function apply(state: QueueState, ...events: QueueEvent[]): QueueState {
  let current = state;
  for (const event of events) {
    const result = applyQueueEvent(current, event);
    expect(result.accepted, `${event.type} should be accepted (${result.reason})`).toBe(true);
    current = result.state;
  }
  return current;
}

function rejected(state: QueueState, event: QueueEvent): boolean {
  const result = applyQueueEvent(state, event);
  if (!result.accepted) expect(result.state).toBe(state);
  return !result.accepted;
}

function find(state: QueueState, id: string): QueueJob {
  const found = state.items.find((item) => item.id === id);
  if (!found) throw new Error(`no job ${id}`);
  return found;
}

const generatingRecord: PipelineProgressRecord = {
  phase: 'generating',
  scope: 'single',
  detail: 'chunk 3/7',
  lineCount: 2,
  chunkIndex: 3,
  chunkTotal: 7,
  startedAt: null,
  updatedAt: null,
};

// === State machine ===

describe('queue state machine: enqueue', () => {
  it('starts a new run when nothing is active: finished jobs, run clock and overview reset', () => {
    let state = apply(
      emptyQueueState(),
      { type: 'enqueue', jobs: [job('a')], chapterOverview: [{ chapterTitle: 'Old', processableTotal: 9, missingVoiceTotal: 1 }], at: T0 },
      { type: 'finish', id: 'a', outcome: { kind: 'ok' }, at: T0 + 1 },
    );
    state = apply(state, {
      type: 'enqueue',
      jobs: [job('b')],
      chapterOverview: [{ chapterTitle: 'Ch1', processableTotal: 2, missingVoiceTotal: 0 }],
      at: T0 + 50,
    });
    expect(state.items.map((item) => item.id)).toEqual(['b']);
    expect(state.runStartedAt).toBe(T0 + 50);
    expect(Object.keys(state.chapterOverview)).toEqual(['Ch1']);
  });

  it('appends to an active run, keeping its clock and merging the overview', () => {
    let state = apply(emptyQueueState(), {
      type: 'enqueue',
      jobs: [job('a')],
      chapterOverview: [{ chapterTitle: 'Ch1', processableTotal: 2, missingVoiceTotal: 0 }],
      at: T0,
    });
    state = apply(state, {
      type: 'enqueue',
      jobs: [job('b'), job('c')],
      chapterOverview: [{ chapterTitle: 'Ch2', processableTotal: 3, missingVoiceTotal: 1 }],
      at: T0 + 10,
    });
    expect(state.items.map((item) => item.id)).toEqual(['a', 'b', 'c']);
    expect(state.runStartedAt).toBe(T0);
    expect(Object.keys(state.chapterOverview).sort()).toEqual(['Ch1', 'Ch2']);
  });

  it('rejects an empty enqueue, a duplicate id, and a job that is not queued', () => {
    const state = apply(emptyQueueState(), { type: 'enqueue', jobs: [job('a')], at: T0 });
    expect(rejected(state, { type: 'enqueue', jobs: [], at: T0 })).toBe(true);
    expect(rejected(state, { type: 'enqueue', jobs: [job('a')], at: T0 })).toBe(true);
    expect(rejected(state, { type: 'enqueue', jobs: [job('x'), job('x')], at: T0 })).toBe(true);
    expect(rejected(state, { type: 'enqueue', jobs: [{ ...job('y'), status: 'running' }], at: T0 })).toBe(true);
  });

  it('records the planned lines by reference, with their planned character', () => {
    const queued = job('a', {
      lines: [
        { id: 1, text: 'x', chapterTitle: 'Ch1', plannedCharacterId: 'ALICE' } as any,
        { id: 7, text: 'y', chapterTitle: 'Ch2', plannedCharacterId: 'BOB' } as any,
      ],
    });
    expect(queued.lineRefs).toEqual([
      { chapterTitle: 'Ch1', lineId: 1, characterId: 'ALICE' },
      { chapterTitle: 'Ch2', lineId: 7, characterId: 'BOB' },
    ]);
    expect(queued.status).toBe('queued');
    expect(queued.progress).toEqual({ linesDone: 0, charactersDone: 0, record: null });
  });
});

describe('queue state machine: start / progress / finish', () => {
  const queued = () => apply(emptyQueueState(), { type: 'enqueue', jobs: [job('a'), job('b')], at: T0 });

  it('start: queued -> running with its plan; totals follow the plan', () => {
    const rebuiltPlan = plannedJob({ lines: [{ id: 3, text: 'defgh' } as any], cumulativeCharacterCounts: [5], totalCharacters: 5 });
    const state = apply(queued(), { type: 'start', id: 'a', plan: rebuiltPlan, at: T0 + 5 });
    const running = find(state, 'a');
    expect(running.status).toBe('running');
    expect(running.startedAt).toBe(T0 + 5);
    expect(running.plan).toBe(rebuiltPlan);
    expect([running.totalLines, running.totalCharacters]).toEqual([1, 5]);
    expect(runningJob(state)?.id).toBe('a');
    expect(nextQueuedJob(state)?.id).toBe('b');
  });

  it('start: one runner — a second start is rejected while a job runs', () => {
    const state = apply(queued(), { type: 'start', id: 'a', plan: plannedJob(), at: T0 });
    expect(rejected(state, { type: 'start', id: 'b', plan: plannedJob(), at: T0 })).toBe(true);
    expect(rejected(state, { type: 'start', id: 'a', plan: plannedJob(), at: T0 })).toBe(true);
    expect(rejected(state, { type: 'start', id: 'missing', plan: plannedJob(), at: T0 })).toBe(true);
  });

  it('progress: finished lines map to characters through the plan; the record is the live progress', () => {
    let state = apply(queued(), { type: 'start', id: 'a', plan: plannedJob(), at: T0 });
    state = apply(state, { type: 'progress', id: 'a', record: generatingRecord });
    expect(find(state, 'a').progress).toEqual({ linesDone: 0, charactersDone: 0, record: generatingRecord });
    state = apply(state, { type: 'progress', id: 'a', linesDone: 1 });
    expect(find(state, 'a').progress).toEqual({ linesDone: 1, charactersDone: 3, record: generatingRecord });
    state = apply(state, { type: 'progress', id: 'a', linesDone: 99 });
    expect(find(state, 'a').progress.linesDone).toBe(2);
    expect(find(state, 'a').progress.charactersDone).toBe(8);
  });

  it('progress: rejected on a job that is not running', () => {
    expect(rejected(queued(), { type: 'progress', id: 'a', linesDone: 1 })).toBe(true);
    const done = apply(
      queued(),
      { type: 'start', id: 'a', plan: plannedJob(), at: T0 },
      { type: 'finish', id: 'a', outcome: { kind: 'ok' }, at: T0 + 1 },
    );
    expect(rejected(done, { type: 'progress', id: 'a', record: generatingRecord })).toBe(true);
  });

  it('finish ok: completed with full progress; the plan and record are dropped', () => {
    const state = apply(
      queued(),
      { type: 'start', id: 'a', plan: plannedJob(), at: T0 },
      { type: 'progress', id: 'a', record: generatingRecord },
      { type: 'finish', id: 'a', outcome: { kind: 'ok' }, at: T0 + 9 },
    );
    const done = find(state, 'a');
    expect(done.status).toBe('completed');
    expect(done.finishedAt).toBe(T0 + 9);
    expect(done.progress).toEqual({ linesDone: 2, charactersDone: 8, record: null });
    expect(done.plan).toBeNull();
  });

  it('finish fail / cancel, from running or straight from queued (rebuild outcomes)', () => {
    let state = apply(
      queued(),
      { type: 'start', id: 'a', plan: plannedJob(), at: T0 },
      { type: 'finish', id: 'a', outcome: { kind: 'fail', errors: ['boom'] }, at: T0 + 1 },
      { type: 'finish', id: 'b', outcome: { kind: 'cancel' }, at: T0 + 2 },
    );
    expect(find(state, 'a').status).toBe('failed');
    expect(find(state, 'a').errors).toEqual(['boom']);
    expect(find(state, 'b').status).toBe('canceled');
    // A finished job cannot finish again.
    expect(rejected(state, { type: 'finish', id: 'a', outcome: { kind: 'ok' }, at: T0 })).toBe(true);
    state = apply(state, { type: 'enqueue', jobs: [job('c')], at: T0 + 3 });
    state = apply(state, { type: 'finish', id: 'c', outcome: { kind: 'ok' }, at: T0 + 4 });
    expect(find(state, 'c').status).toBe('completed');
  });
});

describe('queue state machine: cancel / remove / clearFinished', () => {
  it('cancel all: queued jobs are canceled, the running job is asked to stop', () => {
    let state = apply(
      emptyQueueState(),
      { type: 'enqueue', jobs: [job('a'), job('b')], at: T0 },
      { type: 'start', id: 'a', plan: plannedJob(), at: T0 },
      { type: 'cancel', at: T0 + 1 },
    );
    expect(find(state, 'a').status).toBe('running');
    expect(isCancelRequested(state, 'a')).toBe(true);
    expect(find(state, 'b').status).toBe('canceled');
    expect(nextQueuedJob(state)).toBeNull();
    // The runner then finishes the running job as canceled.
    state = apply(state, { type: 'finish', id: 'a', outcome: { kind: 'cancel' }, at: T0 + 2 });
    expect(find(state, 'a').status).toBe('canceled');
  });

  it('cancel one: rejected for a finished or unknown job', () => {
    const state = apply(
      emptyQueueState(),
      { type: 'enqueue', jobs: [job('a'), job('b')], at: T0 },
      { type: 'cancel', id: 'b', at: T0 },
    );
    expect(find(state, 'a').status).toBe('queued');
    expect(find(state, 'b').status).toBe('canceled');
    expect(rejected(state, { type: 'cancel', id: 'b', at: T0 })).toBe(true);
    expect(rejected(state, { type: 'cancel', id: 'nope', at: T0 })).toBe(true);
  });

  it('remove: queued only', () => {
    const state = apply(
      emptyQueueState(),
      { type: 'enqueue', jobs: [job('a'), job('b')], at: T0 },
      { type: 'start', id: 'a', plan: plannedJob(), at: T0 },
    );
    expect(rejected(state, { type: 'remove', id: 'a' })).toBe(true);
    expect(apply(state, { type: 'remove', id: 'b' }).items.map((item) => item.id)).toEqual(['a']);
  });

  it('clearFinished keeps active jobs; with none left, the run resets', () => {
    let state = apply(
      emptyQueueState(),
      { type: 'enqueue', jobs: [job('a'), job('b')], chapterOverview: [{ chapterTitle: 'Ch1', processableTotal: 4, missingVoiceTotal: 0 }], at: T0 },
      { type: 'finish', id: 'a', outcome: { kind: 'ok' }, at: T0 },
      { type: 'clearFinished' },
    );
    expect(state.items.map((item) => item.id)).toEqual(['b']);
    expect(state.runStartedAt).toBe(T0);
    state = apply(state, { type: 'finish', id: 'b', outcome: { kind: 'ok' }, at: T0 }, { type: 'clearFinished' });
    expect(state).toEqual(emptyQueueState());
  });
});

// === Persistence view ===

describe('queue persistence', () => {
  it('persists the jobs without their in-memory plan', () => {
    const state = apply(emptyQueueState(), { type: 'enqueue', jobs: [job('a')], at: T0 });
    const raw = JSON.parse(serializeQueueState(state));
    expect(raw.version).toBe(2);
    expect(raw.items[0].plan).toBeUndefined();
    expect(raw.items[0].lineRefs).toHaveLength(2);
  });

  it('restore: a running job is queued again with progress reset; a pending cancel becomes canceled', () => {
    const state = apply(
      emptyQueueState(),
      { type: 'enqueue', jobs: [job('a'), job('b')], at: T0 },
      { type: 'start', id: 'a', plan: plannedJob(), at: T0 + 1 },
      { type: 'progress', id: 'a', linesDone: 1, record: generatingRecord },
    );
    const restored = restoreQueueState(serializeQueueState(state), T0 + 100);
    const a = find(restored, 'a');
    expect(a.status).toBe('queued');
    expect(a.progress).toEqual({ linesDone: 0, charactersDone: 0, record: null });
    expect(a.startedAt).toBeUndefined();
    expect(a.plan).toBeNull();
    expect(restored.runStartedAt).toBe(T0);

    const canceling = apply(state, { type: 'cancel', id: 'a', at: T0 + 2 });
    expect(find(restoreQueueState(serializeQueueState(canceling), T0 + 100), 'a').status).toBe('canceled');
  });

  it('restore reads a legacy v1 queue (no line refs)', () => {
    const v1 = JSON.stringify({
      items: [
        {
          id: 'ALICE-missing-1', characterId: 'ALICE', characterName: 'Alice', chapterTitle: 'Ch1',
          generationMode: 'missing', total: 4, current: 2, totalCharacters: 40, processedCharacters: 20,
          status: 'running', createdAt: T0, startedAt: T0 + 1,
        },
        { id: 'BOB-full-1', characterId: 'BOB', characterName: 'Bob', chapterTitle: 'Ch2', generationMode: 'full', total: 3, current: 3, totalCharacters: 30, processedCharacters: 30, status: 'completed', createdAt: T0, finishedAt: T0 + 5 },
      ],
      runStartedAt: T0,
      chapterOverview: { Ch1: { chapterTitle: 'Ch1', processableTotal: 4, missingVoiceTotal: 0 } },
    });
    const restored = restoreQueueState(v1, T0 + 100);
    const alice = find(restored, 'ALICE-missing-1');
    expect(alice).toMatchObject({
      status: 'queued', displayName: 'Alice', totalLines: 4, totalCharacters: 40, lineRefs: [],
      jobIdBase: 'ALICE-rebuild', sourceFile: 'Ch1/chapter.txt', plan: null,
    });
    expect(alice.progress.linesDone).toBe(0);
    expect(find(restored, 'BOB-full-1')).toMatchObject({ status: 'completed', generationMode: 'full', progress: { linesDone: 3 } });
    expect(restored.chapterOverview.Ch1.processableTotal).toBe(4);
  });

  it('restore of nothing or garbage is an empty queue', () => {
    expect(restoreQueueState(null, T0)).toEqual(emptyQueueState());
    expect(restoreQueueState('{not json', T0)).toEqual(emptyQueueState());
  });
});

// === Restart-rebuild: store reload + plan miss -> disk rebuild gives the same job ===

/** Plan -> queue -> persist -> restore -> rebuild from disk. */
async function survivesRestart(env: ReturnType<typeof makeEnv>, planned: PlannedQueueJob, mode: 'missing' | 'full' = 'missing') {
  const queued = createQueueJob(planned, { id: `${planned.jobIdBase}-${T0}-0`, generationMode: mode, createdAt: T0 });
  const before = apply(emptyQueueState(), { type: 'enqueue', jobs: [queued], at: T0 });
  const after = restoreQueueState(serializeQueueState(before), T0 + 1000);
  const restored = find(after, queued.id);
  expect(restored.plan).toBeNull();
  const rebuilt = await rebuildQueuedJobPayload(env.deps, restored);
  return { queued, restored, rebuilt };
}

describe('restart rebuild', () => {
  it('a single-chapter job rebuilds to the same plan, and the restored job equals the queued one', async () => {
    const env = makeEnv();
    env.markPresent('Ch1', 'Alice', 1);
    const planned = await planChapterCharacter(env.ctx, {
      chapter: { title: 'Ch1', path: 'Ch1/chapter.txt' },
      script: env.scripts.Ch1,
      characterId: 'ALICE',
      missingOnly: true,
    });
    const { queued, restored, rebuilt } = await survivesRestart(env, planned.job!);
    expect(rebuilt.payload).toEqual(planned.job);
    expect({ ...restored, plan: queued.plan }).toEqual(queued);
  });

  it('a whole-book job keeps its lines from every chapter', async () => {
    const env = makeEnv();
    const plan = await planWholeBookMissing(env.ctx);
    const alice = plan.jobs.find((entry) => entry.characterId === 'ALICE')!;
    expect(new Set(alice.lines.map((entry) => entry.chapterTitle))).toEqual(new Set(['Ch1', 'Ch2']));
    const { rebuilt } = await survivesRestart(env, alice);
    expect(rebuilt.payload).toEqual(alice);
  });

  it('a speaker-source job keeps every character it mixes, and its generation options', async () => {
    const env = makeEnv();
    const preview = await planAdvancedPreview(env.ctx);
    const plan = planAdvancedGeneration(preview, {
      mode: 'speaker-source',
      selectedCharacterIds: new Set(['ALICE', 'BOB']),
      generationOptions: { useFillerForShortBatch: true, fillerText: 'Same speaker.', generatorOverride: 'chatterbox_base' } as any,
    });
    expect(plan.jobs).toHaveLength(1);
    const mixed = plan.jobs[0];
    expect(new Set(mixed.lines.map((entry) => entry.plannedCharacterId))).toEqual(new Set(['ALICE', 'BOB']));
    const { rebuilt } = await survivesRestart(env, mixed);
    expect(rebuilt.payload).toEqual(mixed);
  });

  it('a full (overwrite) job rebuilds every line, audio or not', async () => {
    const env = makeEnv();
    env.markPresent('Ch1', 'Alice', 1);
    env.markPresent('Ch1', 'Alice', 3);
    const planned = await planChapterCharacter(env.ctx, {
      chapter: { title: 'Ch1', path: 'Ch1/chapter.txt' },
      script: env.scripts.Ch1,
      characterId: 'ALICE',
      missingOnly: false,
    });
    const { rebuilt } = await survivesRestart(env, planned.job!, 'full');
    expect(rebuilt.payload).toEqual(planned.job);
  });

  it('drops lines that got audio or changed speaker meanwhile; nothing left means complete', async () => {
    const env = makeEnv();
    const plan = await planWholeBookMissing(env.ctx);
    const alice = plan.jobs.find((entry) => entry.characterId === 'ALICE')!;
    env.markPresent('Ch2', 'Alice', 1);
    env.scripts.Ch1.lines[0].characterId = 'BOB';
    const { rebuilt } = await survivesRestart(env, alice);
    expect(rebuilt.payload!.lines.map((entry) => `${entry.chapterTitle}:${entry.id}`)).toEqual(['Ch1:3', 'Ch2:2']);
    expect(rebuilt.payload!.totalCharacters).toBe('a much longer alice line'.length + 'line 2 text'.length);

    env.markPresent('Ch1', 'Alice', 3);
    env.markPresent('Ch2', 'Alice', 2);
    expect((await survivesRestart(env, alice)).rebuilt).toEqual({ payload: null, markComplete: true });
  });

  it('fails the job when its character lost its voice', async () => {
    const env = makeEnv();
    const plan = await planWholeBookMissing(env.ctx);
    const alice = plan.jobs.find((entry) => entry.characterId === 'ALICE')!;
    delete env.voices.ALICE;
    const { rebuilt } = await survivesRestart(env, alice);
    expect(rebuilt).toEqual({ payload: null, error: 'No voice assigned to Alice.' });
  });

  it('a legacy job (no line refs) rebuilds from its chapter and character', async () => {
    const env = makeEnv();
    const legacy = restoreQueueState(
      JSON.stringify({ items: [{ id: 'old', characterId: 'ALICE', characterName: 'Alice', chapterTitle: 'Ch2', total: 2, status: 'queued', createdAt: T0 }] }),
      T0,
    ).items[0];
    const rebuilt = await rebuildQueuedJobPayload(env.deps, legacy);
    expect(rebuilt.payload!.lines.map((entry) => entry.id)).toEqual([1, 2]);
    expect(rebuilt.payload!.jobIdBase).toBe('ALICE-rebuild');
  });
});

// === Planner parity: the single-chapter cases the panel's shadow planner handled ===

describe('planChapterCharacter (formerly the panel shadow planner)', () => {
  const chapter = { title: 'Ch1', path: 'Ch1/chapter.txt' };

  it('missing-only: the character\'s lines without audio, in script order, from the given script', async () => {
    const env = makeEnv();
    env.markPresent('Ch1', 'Alice', 1);
    const readsBefore = env.diskReads();
    const { job: planned } = await planChapterCharacter(env.ctx, {
      chapter, script: env.scripts.Ch1, characterId: 'ALICE', missingOnly: true,
    });
    expect(env.diskReads()).toBe(readsBefore);
    expect(planned).toMatchObject({
      jobIdBase: 'ALICE-missing',
      displayName: 'Alice',
      characterId: 'ALICE',
      characterName: 'Alice',
      chapterTitle: 'Ch1',
      sourceFile: 'Ch1/chapter.txt',
      voiceInfo: env.voices.ALICE,
    });
    expect(planned!.lines.map((entry) => entry.id)).toEqual([3]);
    expect(planned!.cumulativeCharacterCounts).toEqual(['a much longer alice line'.length]);
  });

  it('full: every line of the character, audio or not', async () => {
    const env = makeEnv();
    env.markPresent('Ch1', 'Alice', 1);
    const { job: planned } = await planChapterCharacter(env.ctx, {
      chapter, script: env.scripts.Ch1, characterId: 'ALICE', missingOnly: false,
    });
    expect(planned!.jobIdBase).toBe('ALICE-full');
    expect(planned!.lines.map((entry) => entry.id)).toEqual([1, 3]);
    expect(planned!.totalCharacters).toBe('line 1 text'.length + 'a much longer alice line'.length);
  });

  it('stamps lines with queue context like the other planners', async () => {
    const env = makeEnv();
    const { job: planned } = await planChapterCharacter(env.ctx, {
      chapter, script: env.scripts.Ch1, characterId: 'BOB', missingOnly: true,
    });
    expect(planned!.lines).toEqual([
      { id: 2, text: 'line 2 text', characterId: 'BOB', chapterTitle: 'Ch1', sourceFile: 'Ch1/chapter.txt', characterName: 'Bob', chosenSpeaker: 'Bob', plannedCharacterId: 'BOB' },
    ]);
  });

  it('agrees with the whole-book planner on the same chapter', async () => {
    const env = makeEnv();
    env.markPresent('Ch1', 'Alice', 3);
    const single = await planChapterCharacter(env.ctx, { chapter, script: env.scripts.Ch1, characterId: 'ALICE', missingOnly: true });
    const book = await planWholeBookMissing({ ...env.ctx, chapters: [env.ctx.chapters[0]] });
    const bookAlice = book.jobs.find((entry) => entry.characterId === 'ALICE')!;
    expect(single.job!.lines).toEqual(bookAlice.lines);
  });

  it('reports why there is no job: no voice, no lines, nothing missing', async () => {
    const env = makeEnv();
    expect(await planChapterCharacter(env.ctx, { chapter, script: env.scripts.Ch1, characterId: 'CARL', missingOnly: true }))
      .toEqual({ job: null, reason: 'no-voice' });
    env.voices.DANA = { voiceId: 'v2', provider: 'vibevoice', displayName: 'D', characterId: 'DANA' };
    expect(await planChapterCharacter(env.ctx, { chapter, script: env.scripts.Ch1, characterId: 'DANA', missingOnly: true }))
      .toEqual({ job: null, reason: 'no-lines' });
    env.markPresent('Ch1', 'Bob', 2);
    expect(await planChapterCharacter(env.ctx, { chapter, script: env.scripts.Ch1, characterId: 'BOB', missingOnly: true }))
      .toEqual({ job: null, reason: 'nothing-missing' });
  });
});
