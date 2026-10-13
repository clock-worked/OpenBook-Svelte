import { derived, get, writable, type Readable, type Writable } from 'svelte/store';

import type { DialogueAiAssistStatus } from '$lib/services/apiContracts';
import {
  countReviewableDialogueLines,
  type DialogueAiAssistChapterInput,
  type DialogueAiAssistController,
} from '$lib/services/dialogueAiPayload';
import type { ProviderClient } from '$lib/services/providerClient';

export type AiAssistTone = 'info' | 'success' | 'error';

/** A provider line result augmented with the panel's UI application state. */
export type AiAssistUiResult<TLineResult> = TLineResult & {
  uiStatus?: 'pending' | 'applied' | 'error';
  uiMessage?: string | null;
};

export interface AiAssistBaseState<TSummary, TLineResult> {
  running: boolean;
  error: string | null;
  statusMessage: string | null;
  statusTone: AiAssistTone;
  summary: TSummary | null;
  results: AiAssistUiResult<TLineResult>[];
  requestId: string | null;
  progress: DialogueAiAssistStatus | null;
}

/** Shape shared by the provider summary contracts the run loop relies on. */
export interface AiAssistSummaryShape {
  scannedLines: number;
  errorCount: number;
}

/** Shape shared by the provider response contracts the run loop relies on. */
export type AiAssistResponseShape<TSummary, TLineResult> = {
  summary: TSummary;
  results: TLineResult[];
};

/** Counts produced by the post-run auto-apply pass. */
export interface AiAssistApplyCounts {
  appliedCount: number;
  reassignedCount: number;
  confirmedCount: number;
  errorCount: number;
}

/** Store access handed to provider-specific auto-apply strategies. */
export interface ProviderAssistApi<TLineResult> {
  updateResult: (lineId: number, updates: Partial<AiAssistUiResult<TLineResult>>) => void;
  getController: () => DialogueAiAssistController | null;
}

export interface ProviderAssistConfig<
  TRunArgs,
  TSummary extends AiAssistSummaryShape,
  TLineResult,
  TState extends AiAssistBaseState<TSummary, TLineResult>
> {
  client: ProviderClient<
    TRunArgs,
    AiAssistResponseShape<TSummary, TLineResult>,
    DialogueAiAssistStatus
  >;
  initialState: TState;
  /** Which results the visible-results derived store surfaces. */
  visibleResultsFilter: (result: AiAssistUiResult<TLineResult>) => boolean;
  /** Builds the provider client's run arguments for a run. */
  buildRunArgs: (args: {
    requestId: string;
    chapterInput: DialogueAiAssistChapterInput;
    state: TState;
  }) => TRunArgs;
  /** Post-run auto-apply strategy (e.g. grouped suggestions, reassignments). */
  autoApply: (
    results: AiAssistUiResult<TLineResult>[],
    api: ProviderAssistApi<TLineResult>
  ) => Promise<Partial<AiAssistApplyCounts>>;
  completionTone: (summary: TSummary, counts: AiAssistApplyCounts) => AiAssistTone;
  completionMessage: (summary: TSummary, counts: AiAssistApplyCounts) => string;
  failureMessage: string;
  /** Provider-specific fields carried over when resetting state (e.g. local settings). */
  resetPreserve?: (state: TState) => Partial<TState>;
  /** Extra fields merged into progress when the run completes (e.g. logDirectory). */
  completedProgressPatch?: (
    summary: TSummary,
    progress: DialogueAiAssistStatus
  ) => Partial<DialogueAiAssistStatus>;
}

export interface ProviderAssistModule<TLineResult, TState> {
  stateStore: Writable<TState>;
  visibleResults: Readable<AiAssistUiResult<TLineResult>[]>;
  registerController: (nextController: DialogueAiAssistController) => () => void;
  resetState: () => void;
  runForCurrentChapter: () => Promise<void>;
  applyCharacterSuggestion: (
    result: AiAssistUiResult<TLineResult>,
    characterName: string | null
  ) => Promise<void>;
  updateResult: (lineId: number, updates: Partial<AiAssistUiResult<TLineResult>>) => void;
  getController: () => DialogueAiAssistController | null;
}

/**
 * Auto-applies grouped "suggestion" results (JEV + local providers):
 * lines are grouped by suggested character and assigned in one call per
 * group; a failing group marks its lines as errors without aborting.
 */
export function autoApplyGroupedSuggestions<
  TLineResult extends {
    outcome?: string | null;
    suggestedCharacterName?: string | null;
  }
>(
  results: AiAssistUiResult<TLineResult>[],
  api: ProviderAssistApi<TLineResult>
): Promise<{ appliedCount: number; errorCount: number }> {
  const active = api.getController();
  if (!active) return Promise.resolve({ appliedCount: 0, errorCount: 0 });

  const groupedAssignments = new Map<string, number[]>();
  for (const result of results) {
    const characterName = result.suggestedCharacterName?.trim();
    if (result.outcome !== 'suggestion' || !characterName) continue;
    const lineIds = groupedAssignments.get(characterName) ?? [];
    lineIds.push(result.lineId);
    groupedAssignments.set(characterName, lineIds);
  }

  let appliedCount = 0;
  let errorCount = 0;
  const pending = (async () => {
    for (const [characterName, lineIds] of groupedAssignments.entries()) {
      try {
        await active.ensureAndAssignCharacters(lineIds, characterName);
        appliedCount += lineIds.length;
        for (const lineId of lineIds) {
          api.updateResult(lineId, {
            uiStatus: 'applied',
            uiMessage: `Applied ${characterName} automatically.`,
          });
        }
      } catch (error) {
        errorCount += lineIds.length;
        const message = error instanceof Error ? error.message : String(error);
        for (const lineId of lineIds) {
          api.updateResult(lineId, {
            uiStatus: 'error',
            uiMessage: message,
          });
        }
      }
    }
    return { appliedCount, errorCount };
  })();

  return pending;
}

/**
 * Builds the shared run/poll/reset machinery for a speaker-attribution
 * provider store. Provider differences (visible-result filtering, run
 * arguments, auto-apply strategy, status messages, reset extras) are
 * supplied as configuration.
 */
export function createProviderAssistStore<
  TRunArgs,
  TSummary extends AiAssistSummaryShape,
  TLineResult,
  TState extends AiAssistBaseState<TSummary, TLineResult>
>(
  config: ProviderAssistConfig<TRunArgs, TSummary, TLineResult, TState>
): ProviderAssistModule<TLineResult, TState> {
  const { client, initialState } = config;

  const stateStore = writable<TState>(initialState);
  let controller: DialogueAiAssistController | null = null;
  let activeRunToken = 0;
  let progressPollTimer: ReturnType<typeof setTimeout> | null = null;

  const visibleResults = derived(
    stateStore,
    (state) => state.results.filter(config.visibleResultsFilter)
  );

  function stopProgressPolling(): void {
    if (progressPollTimer !== null) {
      clearTimeout(progressPollTimer);
      progressPollTimer = null;
    }
  }

  function updateResult(
    lineId: number,
    updates: Partial<AiAssistUiResult<TLineResult>>
  ): void {
    stateStore.update((state) => ({
      ...state,
      results: state.results.map((result) => (
        result.lineId === lineId ? { ...result, ...updates } : result
      )),
    }) as TState);
  }

  function getController(): DialogueAiAssistController | null {
    return controller;
  }

  function registerController(
    nextController: DialogueAiAssistController
  ): () => void {
    controller = nextController;

    return () => {
      if (controller === nextController) {
        controller = null;
      }
    };
  }

  async function pollStatus(requestId: string, runToken: number): Promise<void> {
    try {
      const progress = await client.getStatus(requestId);

      if (runToken !== activeRunToken) return;

      stateStore.update(
        (state) =>
          (state.requestId === requestId ? { ...state, progress } : state) as TState
      );

      if (progress.status === 'queued' || progress.status === 'running') {
        progressPollTimer = setTimeout(() => {
          void pollStatus(requestId, runToken);
        }, 350);
      }
    } catch {
      if (runToken !== activeRunToken) return;

      progressPollTimer = setTimeout(() => {
        void pollStatus(requestId, runToken);
      }, 500);
    }
  }

  function resetState(): void {
    activeRunToken += 1;
    stopProgressPolling();
    stateStore.set({
      ...initialState,
      ...config.resetPreserve?.(get(stateStore)),
    } as TState);
  }

  async function runForCurrentChapter(): Promise<void> {
    const chapterInput = controller?.getChapterInput() ?? null;
    if (!controller || !chapterInput) return;
    if (get(stateStore).running) return;

    const requestId = client.createRequestId();
    const startedAt = new Date().toISOString();
    const runToken = ++activeRunToken;
    const totalLines = countReviewableDialogueLines(chapterInput);

    stopProgressPolling();
    stateStore.set({
      ...initialState,
      ...config.resetPreserve?.(get(stateStore)),
      running: true,
      requestId,
      progress: {
        requestId,
        status: 'queued',
        processedLines: 0,
        totalLines,
        progressRatio: 0,
        startedAt,
        updatedAt: startedAt,
        chapterPath: chapterInput.chapterPath,
        error: null,
        logDirectory: null,
      },
    } as TState);

    void pollStatus(requestId, runToken);

    try {
      const response = await client.run(
        config.buildRunArgs({ requestId, chapterInput, state: get(stateStore) })
      );

      if (runToken !== activeRunToken) return;

      const summary = response.summary;
      stateStore.update((state) => ({
        ...state,
        summary,
        results: response.results.map((result) => ({
          ...result,
          uiStatus: 'pending' as const,
          uiMessage: null,
        })),
      }) as TState);

      const rawCounts = await config.autoApply(get(stateStore).results, {
        updateResult,
        getController,
      });

      if (runToken !== activeRunToken) return;

      const counts: AiAssistApplyCounts = {
        appliedCount: 0,
        reassignedCount: 0,
        confirmedCount: 0,
        errorCount: 0,
        ...rawCounts,
      };

      stateStore.update((state) => ({
        ...state,
        running: false,
        statusTone: config.completionTone(summary, counts),
        statusMessage: config.completionMessage(summary, counts),
        progress: state.progress
          ? {
              ...state.progress,
              status: 'completed' as const,
              processedLines: summary.scannedLines,
              totalLines: summary.scannedLines,
              progressRatio: summary.scannedLines > 0 ? 1 : 0,
              updatedAt: new Date().toISOString(),
              ...config.completedProgressPatch?.(summary, state.progress),
            }
          : state.progress,
      }) as TState);
    } catch (error) {
      if (runToken !== activeRunToken) return;

      const message = error instanceof Error ? error.message : String(error);
      stateStore.update((state) => ({
        ...state,
        running: false,
        error: message,
        statusTone: 'error' as const,
        statusMessage: config.failureMessage,
        progress: state.progress
          ? {
              ...state.progress,
              status: 'failed' as const,
              error: message,
              updatedAt: new Date().toISOString(),
            }
          : state.progress,
      }) as TState);
    } finally {
      if (runToken === activeRunToken) {
        stopProgressPolling();
      }
    }
  }

  async function applyCharacterSuggestion(
    result: AiAssistUiResult<TLineResult>,
    characterName: string | null
  ): Promise<void> {
    if (!controller || !characterName) return;

    try {
      await controller.ensureAndAssignCharacters([result.lineId], characterName);
      updateResult(result.lineId, {
        uiStatus: 'applied',
        uiMessage: `Applied ${characterName}.`,
      });
    } catch (error) {
      updateResult(result.lineId, {
        uiStatus: 'error',
        uiMessage: error instanceof Error ? error.message : String(error),
      });
    }
  }

  return {
    stateStore,
    visibleResults,
    registerController,
    resetState,
    runForCurrentChapter,
    applyCharacterSuggestion,
    updateResult,
    getController,
  };
}
