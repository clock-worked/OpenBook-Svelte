import { API_ENDPOINTS, apiRequestJson } from '$lib/services/apiClient';
import type { VibeVoiceCharacterProgressResponse } from '$lib/services/apiContracts';

/**
 * Live progress for the chunked character pipeline. The whole run (TTS +
 * WhisperX transcription + alignment + split + manifest + validation) happens
 * inside ONE synchronous backend request, so the only way the UI can show
 * what is happening is to poll the progress.json the pipeline writes at each
 * phase boundary.
 */

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

/**
 * Phase → overall percent. Monotonic buckets: speech generation is the long
 * pole, so it owns the biggest span; the WhisperX/split/validate tail gets
 * the remainder. The bar therefore moves while the run is in flight.
 */
export function phasePercent(phase: string | null | undefined): number {
  switch (phase) {
    case 'generating':
      return 35;
    case 'transcribing':
      return 75;
    case 'aligning':
      return 85;
    case 'splitting':
      return 90;
    case 'finalizing':
      return 94;
    case 'validating':
      return 97;
    case 'done':
      return 100;
    case 'failed':
      return 100;
    default:
      return 5;
  }
}

/**
 * Fetch the current pipeline progress for a character run. Returns null on
 * any error (the file may not exist yet, the run may have just finished, or
 * the backend may predate this endpoint) — callers must tolerate null.
 */
export async function fetchCharacterPipelineProgress(params: {
  characterName: string;
  chapterTitle?: string;
  crossChapter?: boolean;
  audioRoot?: string | null;
}): Promise<VibeVoiceCharacterProgressResponse | null> {
  try {
    return await apiRequestJson<VibeVoiceCharacterProgressResponse>(
      API_ENDPOINTS.vibevoiceCharacterProgress(params),
    );
  } catch {
    return null;
  }
}

export interface CharacterPipelineProgressPollOptions {
  characterName: string;
  chapterTitle?: string;
  crossChapter?: boolean;
  audioRoot?: string | null;
  /**
   * Epoch ms of when the generation request was sent. Progress entries whose
   * startedAt predates this (minus a small skew buffer) belong to an earlier
   * run and are ignored.
   */
  requestStart: number;
  /**
   * Invoked when the phase changes OR chunk progress advances within the
   * generating phase. chunkIndex/chunkTotal are null outside the generating
   * phase.
   */
  onProgress: (
    phase: string,
    detail: string | null,
    chunkIndex: number | null,
    chunkTotal: number | null,
  ) => void;
  intervalMs?: number;
}

/**
 * Start polling the pipeline progress endpoint; returns a stop function.
 * Emits onProgress only when the phase or the chunk counter changes. All
 * poll errors are swallowed — a missing file is normal before the pipeline
 * starts writing.
 */
export function startCharacterPipelineProgressPolling(
  opts: CharacterPipelineProgressPollOptions,
): () => void {
  const intervalMs = opts.intervalMs ?? 2000;
  let stopped = false;
  let inFlight = false;
  let lastPhase: string | null = null;
  let lastChunkIndex: number | null = null;

  const tick = async (): Promise<void> => {
    if (stopped || inFlight) return;
    inFlight = true;
    try {
      const progress = await fetchCharacterPipelineProgress({
        characterName: opts.characterName,
        chapterTitle: opts.chapterTitle,
        crossChapter: opts.crossChapter,
        audioRoot: opts.audioRoot,
      });
      if (!progress) return;
      const startedMs = progress.startedAt ? Date.parse(progress.startedAt) : NaN;
      if (Number.isFinite(startedMs) && startedMs < opts.requestStart - 5000) return;
      const phase = typeof progress.phase === 'string' ? progress.phase : 'unknown';
      const chunkIndex = typeof progress.chunkIndex === 'number' ? progress.chunkIndex : null;
      const chunkTotal = typeof progress.chunkTotal === 'number' ? progress.chunkTotal : null;
      if (phase !== lastPhase || chunkIndex !== lastChunkIndex) {
        lastPhase = phase;
        lastChunkIndex = chunkIndex;
        opts.onProgress(phase, progress.detail ?? null, chunkIndex, chunkTotal);
      }
    } finally {
      inFlight = false;
    }
  };

  const timer = window.setInterval(() => {
    void tick();
  }, intervalMs);
  void tick();

  return () => {
    stopped = true;
    window.clearInterval(timer);
  };
}
