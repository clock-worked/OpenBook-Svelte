import type { GenerateAudioCharacterOptions } from '$lib/services/audio';
import type { PipelineProgressRecord } from '$lib/services/audioPipelineProgress';
import type { PlannedQueueJob } from '$lib/services/audioQueuePlanning';

/**
 * The audio queue job model (candidate 8, ADR 0020).
 *
 * One `QueueJob` type is the shape everywhere a queued job lives: in memory
 * (the store), in localStorage (the persisted view drops `plan`), and after
 * a restart (the disk rebuild re-resolves `plan` from `lineRefs`). The legal
 * transitions live here, in {@link applyQueueEvent}; the store applies them
 * and rejects anything this module rejects. Pure: no stores, no I/O.
 */

export type QueueJobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled';

export type QueueGenerationMode = 'missing' | 'full';

/** Identity of one planned line: enough to find it again on disk. */
export interface QueueLineRef {
  chapterTitle: string;
  lineId: number;
  /** The character the planner attributed the line to. */
  characterId: string;
}

/**
 * Job progress. `linesDone` counts finished lines only (the pipeline is one
 * request, so it moves 0 → total); `record` is candidate 7's live pipeline
 * progress record (phase + true engine-chunk counters) while running.
 */
export interface QueueJobProgress {
  linesDone: number;
  charactersDone: number;
  record: PipelineProgressRecord | null;
}

export interface QueueJob {
  id: string;
  status: QueueJobStatus;
  /** Planner identity the id was built from (kept so a rebuild matches). */
  jobIdBase: string;
  /** Speaking character the request is made for (speaker-source: primary). */
  characterId: string;
  characterName: string;
  /** Queue row label. */
  displayName: string;
  chapterTitle: string;
  sourceFile: string;
  generationMode: QueueGenerationMode;
  /** The planned lines, by reference (persisted; drives the rebuild). */
  lineRefs: QueueLineRef[];
  generationOptions?: GenerateAudioCharacterOptions;
  totalLines: number;
  totalCharacters: number;
  progress: QueueJobProgress;
  createdAt: number;
  startedAt?: number;
  finishedAt?: number;
  errors?: string[];
  cancelRequested?: boolean;
  /**
   * The resolved payload (lines, voice, character counts). In memory only:
   * never persisted; null after a restart until the disk rebuild fills it.
   */
  plan: PlannedQueueJob | null;
}

export interface AudioQueueChapterOverview {
  chapterTitle: string;
  processableTotal: number;
  missingVoiceTotal: number;
}

export interface QueueState {
  items: QueueJob[];
  runStartedAt: number | null;
  chapterOverview: Record<string, AudioQueueChapterOverview>;
}

export type QueueJobOutcome =
  | { kind: 'ok' }
  | { kind: 'fail'; errors: string[] }
  | { kind: 'cancel' };

export type QueueEvent =
  | { type: 'enqueue'; jobs: QueueJob[]; chapterOverview?: AudioQueueChapterOverview[]; at: number }
  | { type: 'start'; id: string; plan: PlannedQueueJob; at: number }
  | { type: 'progress'; id: string; linesDone?: number; record?: PipelineProgressRecord | null }
  | { type: 'finish'; id: string; outcome: QueueJobOutcome; at: number }
  | { type: 'cancel'; id?: string; at: number }
  | { type: 'remove'; id: string }
  | { type: 'clearFinished' };

export interface QueueTransition {
  state: QueueState;
  accepted: boolean;
  reason?: string;
}

export function emptyQueueState(): QueueState {
  return { items: [], runStartedAt: null, chapterOverview: {} };
}

export function isActiveStatus(status: QueueJobStatus): boolean {
  return status === 'queued' || status === 'running';
}

function toCount(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(0, Math.floor(numeric));
}

function zeroProgress(): QueueJobProgress {
  return { linesDone: 0, charactersDone: 0, record: null };
}

function lineRefsFromPlan(planned: PlannedQueueJob): QueueLineRef[] {
  const refs: QueueLineRef[] = [];
  for (const line of planned.lines) {
    const lineId = Number((line as any)?.id);
    if (!Number.isFinite(lineId)) continue;
    const chapterTitle =
      typeof line.chapterTitle === 'string' && line.chapterTitle.trim() ? line.chapterTitle.trim() : planned.chapterTitle;
    const characterId =
      typeof line.plannedCharacterId === 'string' && line.plannedCharacterId
        ? line.plannedCharacterId
        : planned.characterId;
    refs.push({ chapterTitle, lineId, characterId });
  }
  return refs;
}

/** A new queued job from a planner job. */
export function createQueueJob(
  planned: PlannedQueueJob,
  opts: { id: string; generationMode: QueueGenerationMode; createdAt: number },
): QueueJob {
  return {
    id: opts.id,
    status: 'queued',
    jobIdBase: planned.jobIdBase,
    characterId: planned.characterId,
    characterName: planned.characterName,
    displayName: planned.displayName,
    chapterTitle: planned.chapterTitle,
    sourceFile: planned.sourceFile,
    generationMode: opts.generationMode,
    lineRefs: lineRefsFromPlan(planned),
    generationOptions: planned.generationOptions,
    totalLines: planned.lines.length,
    totalCharacters: planned.totalCharacters,
    progress: zeroProgress(),
    createdAt: opts.createdAt,
    plan: planned,
  };
}

// === Selectors ===

export function nextQueuedJob(state: QueueState): QueueJob | null {
  return state.items.find((item) => item.status === 'queued') ?? null;
}

export function runningJob(state: QueueState): QueueJob | null {
  return state.items.find((item) => item.status === 'running') ?? null;
}

export function isCancelRequested(state: QueueState, id: string): boolean {
  return Boolean(state.items.find((item) => item.id === id)?.cancelRequested);
}

// === State machine ===

function reject(state: QueueState, reason: string): QueueTransition {
  return { state, accepted: false, reason };
}

function replaceJob(state: QueueState, job: QueueJob): QueueState {
  return { ...state, items: state.items.map((item) => (item.id === job.id ? job : item)) };
}

function mergeOverview(
  base: Record<string, AudioQueueChapterOverview>,
  entries: AudioQueueChapterOverview[] | undefined,
): Record<string, AudioQueueChapterOverview> {
  if (!entries?.length) return base;
  const next = { ...base };
  for (const entry of entries) {
    const title = String(entry.chapterTitle || '').trim();
    if (!title) continue;
    next[title] = {
      chapterTitle: title,
      processableTotal: toCount(entry.processableTotal),
      missingVoiceTotal: toCount(entry.missingVoiceTotal),
    };
  }
  return next;
}

/**
 * Applies one transition. Illegal transitions are rejected (`accepted:
 * false`, state unchanged):
 *
 * - enqueue: at least one job, all `queued`, no id already in the queue.
 *   When nothing is active the finished jobs, run clock and chapter
 *   overview are reset first (a new run).
 * - start: queued → running, only while no other job runs (one runner).
 * - progress: running only.
 * - finish: queued | running → completed | failed | canceled.
 * - cancel: queued → canceled; running → cancel requested (the runner
 *   finishes it). Without an id, applies to every active job.
 * - remove: queued only.
 * - clearFinished: always; drops finished jobs.
 */
export function applyQueueEvent(state: QueueState, event: QueueEvent): QueueTransition {
  switch (event.type) {
    case 'enqueue': {
      if (event.jobs.length === 0) return reject(state, 'nothing to enqueue');
      const ids = new Set(state.items.map((item) => item.id));
      for (const job of event.jobs) {
        if (job.status !== 'queued') return reject(state, `job ${job.id} is not queued`);
        if (ids.has(job.id)) return reject(state, `job ${job.id} is already in the queue`);
        ids.add(job.id);
      }
      const hasActiveWork = state.items.some((item) => isActiveStatus(item.status));
      const base: QueueState = hasActiveWork
        ? state
        : { items: [], runStartedAt: event.at, chapterOverview: {} };
      return {
        accepted: true,
        state: {
          ...base,
          items: [...base.items, ...event.jobs],
          chapterOverview: mergeOverview(base.chapterOverview, event.chapterOverview),
        },
      };
    }

    case 'start': {
      const job = state.items.find((item) => item.id === event.id);
      if (!job) return reject(state, `unknown job ${event.id}`);
      if (job.status !== 'queued') return reject(state, `cannot start a ${job.status} job`);
      const other = runningJob(state);
      if (other) return reject(state, `job ${other.id} is already running`);
      return {
        accepted: true,
        state: replaceJob(state, {
          ...job,
          status: 'running',
          startedAt: event.at,
          finishedAt: undefined,
          // A rebuilt plan may find fewer missing lines than were queued.
          totalLines: event.plan.lines.length,
          totalCharacters: event.plan.totalCharacters,
          progress: zeroProgress(),
          plan: event.plan,
        }),
      };
    }

    case 'progress': {
      const job = state.items.find((item) => item.id === event.id);
      if (!job) return reject(state, `unknown job ${event.id}`);
      if (job.status !== 'running') return reject(state, `cannot report progress on a ${job.status} job`);
      let { linesDone, charactersDone, record } = job.progress;
      if (event.linesDone !== undefined) {
        linesDone = Math.min(toCount(event.linesDone), job.totalLines);
        const cumulative = job.plan?.cumulativeCharacterCounts ?? [];
        const bounded = Math.min(linesDone, cumulative.length);
        charactersDone = bounded > 0 ? cumulative[bounded - 1] : 0;
      }
      if (event.record !== undefined) record = event.record;
      return {
        accepted: true,
        state: replaceJob(state, { ...job, progress: { linesDone, charactersDone, record } }),
      };
    }

    case 'finish': {
      const job = state.items.find((item) => item.id === event.id);
      if (!job) return reject(state, `unknown job ${event.id}`);
      if (!isActiveStatus(job.status)) return reject(state, `job ${job.id} is already ${job.status}`);
      const outcome = event.outcome;
      const finished: QueueJob = {
        ...job,
        finishedAt: event.at,
        plan: null,
        progress: { ...job.progress, record: null },
      };
      if (outcome.kind === 'ok') {
        finished.status = 'completed';
        finished.progress = { linesDone: job.totalLines, charactersDone: job.totalCharacters, record: null };
      } else if (outcome.kind === 'fail') {
        finished.status = 'failed';
        finished.errors = [...outcome.errors];
      } else {
        finished.status = 'canceled';
      }
      return { accepted: true, state: replaceJob(state, finished) };
    }

    case 'cancel': {
      if (event.id !== undefined) {
        const job = state.items.find((item) => item.id === event.id);
        if (!job) return reject(state, `unknown job ${event.id}`);
        if (!isActiveStatus(job.status)) return reject(state, `job ${job.id} is already ${job.status}`);
      }
      return {
        accepted: true,
        state: {
          ...state,
          items: state.items.map((item) => {
            if (event.id !== undefined && item.id !== event.id) return item;
            if (item.status === 'queued') {
              return { ...item, status: 'canceled', cancelRequested: true, finishedAt: event.at, plan: null };
            }
            if (item.status === 'running') return { ...item, cancelRequested: true };
            return item;
          }),
        },
      };
    }

    case 'remove': {
      const job = state.items.find((item) => item.id === event.id);
      if (!job) return reject(state, `unknown job ${event.id}`);
      if (job.status !== 'queued') return reject(state, `cannot remove a ${job.status} job`);
      return { accepted: true, state: { ...state, items: state.items.filter((item) => item.id !== event.id) } };
    }

    case 'clearFinished': {
      const items = state.items.filter((item) => isActiveStatus(item.status));
      const hasActive = items.length > 0;
      return {
        accepted: true,
        state: {
          items,
          runStartedAt: hasActive ? state.runStartedAt : null,
          chapterOverview: hasActive ? state.chapterOverview : {},
        },
      };
    }
  }
}

// === Persistence view ===

export const QUEUE_STORAGE_VERSION = 2;

/** The persisted view: the same jobs, minus the in-memory `plan`. */
export function serializeQueueState(state: QueueState): string {
  return JSON.stringify({
    version: QUEUE_STORAGE_VERSION,
    runStartedAt: state.runStartedAt,
    chapterOverview: state.chapterOverview,
    items: state.items.map(({ plan: _plan, ...rest }) => rest),
  });
}

function normalizeStatus(value: unknown): QueueJobStatus {
  return value === 'queued' || value === 'running' || value === 'completed' || value === 'failed' || value === 'canceled'
    ? value
    : 'queued';
}

function normalizeLineRefs(value: unknown): QueueLineRef[] {
  if (!Array.isArray(value)) return [];
  const refs: QueueLineRef[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') continue;
    const raw = entry as Record<string, unknown>;
    const chapterTitle = String(raw.chapterTitle ?? '').trim();
    const characterId = String(raw.characterId ?? '').trim();
    const lineId = Number(raw.lineId);
    if (!chapterTitle || !characterId || !Number.isFinite(lineId)) continue;
    refs.push({ chapterTitle, characterId, lineId });
  }
  return refs;
}

/**
 * One persisted job (v2, or a legacy v1 `AudioQueueItem`) → a `QueueJob`.
 * A job that was running when the app stopped is queued again (progress
 * reset) — or canceled, if a cancel had been requested.
 */
function restoreJob(value: unknown, now: number): QueueJob | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const id = String(raw.id ?? '').trim();
  const characterId = String(raw.characterId ?? '').trim();
  const chapterTitle = String(raw.chapterTitle ?? '').trim();
  if (!id || !characterId || !chapterTitle) return null;

  const legacy = raw.progress === undefined;
  const storedStatus = normalizeStatus(raw.status);
  const cancelRequested = Boolean(raw.cancelRequested);
  const wasRunning = storedStatus === 'running';
  const status: QueueJobStatus = wasRunning ? (cancelRequested ? 'canceled' : 'queued') : storedStatus;
  const characterName = String(raw.characterName ?? characterId);
  const totalLines = toCount(legacy ? raw.total : raw.totalLines);
  const totalCharacters = toCount(raw.totalCharacters);
  const storedProgress = (raw.progress ?? {}) as Record<string, unknown>;

  let progress: QueueJobProgress;
  if (status === 'queued') {
    progress = zeroProgress();
  } else if (status === 'completed') {
    progress = { linesDone: totalLines, charactersDone: totalCharacters, record: null };
  } else {
    progress = {
      linesDone: toCount(legacy ? raw.current : storedProgress.linesDone),
      charactersDone: toCount(legacy ? raw.processedCharacters : storedProgress.charactersDone),
      record: null,
    };
  }

  const startedAt = Number(raw.startedAt);
  const finishedAt = Number(raw.finishedAt);
  const generationOptions =
    raw.generationOptions && typeof raw.generationOptions === 'object'
      ? (raw.generationOptions as GenerateAudioCharacterOptions)
      : undefined;

  return {
    id,
    status,
    jobIdBase: String(raw.jobIdBase ?? '').trim() || `${characterId}-rebuild`,
    characterId,
    characterName,
    // Legacy items stored the row label in characterName.
    displayName: String(raw.displayName ?? characterName),
    chapterTitle,
    sourceFile: String(raw.sourceFile ?? '').trim() || `${chapterTitle}/chapter.txt`,
    generationMode: raw.generationMode === 'full' ? 'full' : 'missing',
    lineRefs: normalizeLineRefs(raw.lineRefs),
    ...(generationOptions ? { generationOptions } : {}),
    totalLines,
    totalCharacters,
    progress,
    createdAt: toCount(raw.createdAt, now),
    startedAt: status === 'queued' || !Number.isFinite(startedAt) ? undefined : startedAt,
    finishedAt: status === 'canceled' && wasRunning ? now : Number.isFinite(finishedAt) ? finishedAt : undefined,
    errors: Array.isArray(raw.errors) ? raw.errors.map((entry) => String(entry)) : undefined,
    cancelRequested,
    plan: null,
  };
}

/**
 * Restores the persisted view (v2, or a legacy v1 payload). Every restored
 * job has `plan: null`; the runner rebuilds it from disk before starting.
 * Malformed input yields an empty queue.
 */
export function restoreQueueState(raw: string | null | undefined, now: number): QueueState {
  if (!raw) return emptyQueueState();
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const items = Array.isArray(parsed.items)
      ? parsed.items.map((item) => restoreJob(item, now)).filter((item): item is QueueJob => Boolean(item))
      : [];
    const hasActive = items.some((item) => isActiveStatus(item.status));
    const overviewEntries = Object.entries((parsed.chapterOverview ?? {}) as Record<string, unknown>).map(
      ([title, overview]) => {
        const source = (overview ?? {}) as Partial<AudioQueueChapterOverview>;
        return {
          chapterTitle: String(source.chapterTitle ?? title ?? ''),
          processableTotal: toCount(source.processableTotal),
          missingVoiceTotal: toCount(source.missingVoiceTotal),
        };
      },
    );
    return {
      items,
      runStartedAt: hasActive ? toCount(parsed.runStartedAt, now) : null,
      chapterOverview: mergeOverview({}, overviewEntries),
    };
  } catch {
    return emptyQueueState();
  }
}
