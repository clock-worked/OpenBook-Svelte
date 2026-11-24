import { API_ENDPOINTS, apiRequestJson } from '$lib/services/apiClient';
import type { VibeVoiceCharacterProgressResponse } from '$lib/services/apiContracts';

/**
 * Live progress for the chunked character pipeline. The whole run (TTS +
 * WhisperX transcription + alignment + split + manifest + validation) happens
 * inside ONE synchronous backend request, so the only way the UI can show
 * what is happening is to poll the progress record the pipeline writes at
 * each phase boundary.
 *
 * The record is self-describing (py_services/audio/pipeline_progress.py):
 * the backend finds it and reports its scope, so this module is a dumb
 * poller and the display reads the record directly.
 */

/** Pipeline phases in run order; mirrors the BE `Phase` enum (shape vector). */
export const PIPELINE_PHASES = [
  'starting',
  'generating',
  'transcribing',
  'aligning',
  'splitting',
  'finalizing',
  'validating',
  'done',
  'failed',
  'unknown',
] as const;

export type PipelinePhase = (typeof PIPELINE_PHASES)[number];

export const PIPELINE_SCOPES = ['single', 'cross'] as const;
export type PipelineScope = (typeof PIPELINE_SCOPES)[number];

/** One normalized poll result: what the display reads. */
export interface PipelineProgressRecord {
  phase: PipelinePhase;
  scope: PipelineScope | null;
  detail: string | null;
  lineCount: number | null;
  /** True engine-chunk progress; null outside the generating phase. */
  chunkIndex: number | null;
  chunkTotal: number | null;
  startedAt: string | null;
  updatedAt: string | null;
}

function isPipelinePhase(value: unknown): value is PipelinePhase {
  return typeof value === 'string' && (PIPELINE_PHASES as readonly string[]).includes(value);
}

function intOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

/** Normalize a raw response into a record (unknown phases read as 'unknown'). */
export function toPipelineProgressRecord(
  raw: VibeVoiceCharacterProgressResponse,
): PipelineProgressRecord {
  const phase = isPipelinePhase(raw.phase) ? raw.phase : 'unknown';
  const generating = phase === 'generating';
  return {
    phase,
    scope: raw.scope === 'single' || raw.scope === 'cross' ? raw.scope : null,
    detail: stringOrNull(raw.detail),
    lineCount: intOrNull(raw.lineCount),
    chunkIndex: generating ? intOrNull(raw.chunkIndex) : null,
    chunkTotal: generating ? intOrNull(raw.chunkTotal) : null,
    startedAt: stringOrNull(raw.startedAt),
    updatedAt: stringOrNull(raw.updatedAt),
  };
}

/** Phase → human label for the UI. */
export function phaseLabel(phase: string | null | undefined, detail?: string | null): string {
  switch (phase) {
    case 'generating':
      return 'Generating speech…';
    case 'transcribing':
      return 'Transcribing (WhisperX)…';
    case 'aligning':
      return 'Aligning lines…';
    case 'splitting':
      return 'Splitting into line files…';
    case 'finalizing':
      return 'Writing manifests…';
    case 'validating':
      return 'Validating splits…';
    case 'done':
      return 'Done';
    case 'failed':
      return detail ? `Failed: ${detail}` : 'Failed';
    case 'starting':
    case 'unknown':
    case null:
    case undefined:
      return 'Starting…';
    default:
      return phase;
  }
}

/** Label for a record: the phase, plus true chunk progress while generating. */
export function progressLabel(record: PipelineProgressRecord): string {
  const label = phaseLabel(record.phase, record.detail);
  if (record.chunkIndex !== null && record.chunkTotal !== null && record.chunkTotal > 0) {
    return `${label} (chunk ${record.chunkIndex} of ${record.chunkTotal})`;
  }
  return label;
}

/**
 * Fetch the current pipeline progress for a character run. Returns null on
 * any error (the run may have just finished, or the backend may predate
 * this endpoint) — callers must tolerate null.
 */
export async function fetchCharacterPipelineProgress(params: {
  characterName: string;
  chapterTitle?: string;
  audioRoot?: string | null;
}): Promise<PipelineProgressRecord | null> {
  try {
    const raw = await apiRequestJson<VibeVoiceCharacterProgressResponse>(
      API_ENDPOINTS.vibevoiceCharacterProgress(params),
    );
    return raw && typeof raw === 'object' ? toPipelineProgressRecord(raw) : null;
  } catch {
    return null;
  }
}

/** Records whose startedAt predates the request by more than this are stale. */
export const STARTED_AT_SKEW_MS = 5000;

export interface CharacterPipelineProgressPollOptions {
  characterName: string;
  /** The chapter the run was requested for (the BE also checks the book batch). */
  chapterTitle?: string;
  audioRoot?: string | null;
  /**
   * Epoch ms of when the generation request was sent. Records whose
   * startedAt predates this (minus STARTED_AT_SKEW_MS) belong to an earlier
   * run and are ignored.
   */
  requestStart: number;
  /** Invoked when the phase changes OR chunk progress advances. */
  onProgress: (record: PipelineProgressRecord) => void;
  intervalMs?: number;
}

/**
 * Start polling the pipeline progress endpoint; returns a stop function.
 * Emits onProgress only when the phase or the chunk counter changes. All
 * poll errors are swallowed — no record is normal before the pipeline
 * starts writing. After stop, no further onProgress is emitted (an
 * in-flight poll that resolves late is dropped).
 */
export function startCharacterPipelineProgressPolling(
  opts: CharacterPipelineProgressPollOptions,
): () => void {
  const intervalMs = opts.intervalMs ?? 2000;
  let stopped = false;
  let inFlight = false;
  let lastPhase: PipelinePhase | null = null;
  let lastChunkIndex: number | null = null;

  const tick = async (): Promise<void> => {
    if (stopped || inFlight) return;
    inFlight = true;
    try {
      const record = await fetchCharacterPipelineProgress({
        characterName: opts.characterName,
        chapterTitle: opts.chapterTitle,
        audioRoot: opts.audioRoot,
      });
      if (stopped || !record) return;
      const startedMs = record.startedAt ? Date.parse(record.startedAt) : NaN;
      if (Number.isFinite(startedMs) && startedMs < opts.requestStart - STARTED_AT_SKEW_MS) return;
      if (record.phase !== lastPhase || record.chunkIndex !== lastChunkIndex) {
        lastPhase = record.phase;
        lastChunkIndex = record.chunkIndex;
        opts.onProgress(record);
      }
    } finally {
      inFlight = false;
    }
  };

  const timer = setInterval(() => {
    void tick();
  }, intervalMs);
  void tick();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
