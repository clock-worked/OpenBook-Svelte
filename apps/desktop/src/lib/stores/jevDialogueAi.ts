import { derived, get, writable } from 'svelte/store';

import { conflictCursor } from '$lib/stores/selection';
import type {
  DialogueAiAssistStatus,
  JevVerifySummary,
  LocalDialogueAiLineResult,
  LocalDialogueAiSummary,
} from '$lib/services/apiContracts';
import {
  createJevDialogueAiRequestId,
  getJevDialogueAiStatus,
  runJevDialogueAi,
} from '$lib/services/jevDialogueAi';
import {
  buildDialogueAiPayload,
  countReviewableDialogueLines,
  type DialogueAiAssistChapterInput,
  type DialogueAiAssistController,
} from '$lib/stores/dialogueAiAssist';

type JevDialogueAiDisplayResult = LocalDialogueAiLineResult & {
  uiStatus?: 'pending' | 'applied' | 'error';
  uiMessage?: string | null;
};

type JevDialogueAiState = {
  running: boolean;
  error: string | null;
  statusMessage: string | null;
  statusTone: 'info' | 'success' | 'error';
  summary: LocalDialogueAiSummary | null;
  results: JevDialogueAiDisplayResult[];
  requestId: string | null;
  progress: DialogueAiAssistStatus | null;
};

const initialState: JevDialogueAiState = {
  running: false,
  error: null,
  statusMessage: null,
  statusTone: 'info',
  summary: null,
  results: [],
  requestId: null,
  progress: null,
};

const stateStore = writable<JevDialogueAiState>(initialState);
let controller: DialogueAiAssistController | null = null;
let activeRunToken = 0;
let progressPollTimer: ReturnType<typeof setTimeout> | null = null;

export const jevDialogueAiState = { subscribe: stateStore.subscribe };

export const jevDialogueAiVisibleResults = derived(
  stateStore,
  (state) => state.results.filter((result) => {
    if (result.uiStatus === 'error') return true;
    if (result.uiStatus === 'applied') return false;
    return result.outcome !== 'keep_existing';
  })
);

// Parse-time JEV verification summary (meta.jevVerify). Populated by the
// parser service after each parse; null when the parse did not include it.
export const jevVerifySummary = writable<JevVerifySummary | null>(null);

export function setJevVerifySummary(summary: JevVerifySummary | null): void {
  jevVerifySummary.set(summary);
}

export function resetJevVerifySummary(): void {
  jevVerifySummary.set(null);
}

function stopProgressPolling(): void {
  if (progressPollTimer !== null) {
    clearTimeout(progressPollTimer);
    progressPollTimer = null;
  }
}

async function pollStatus(requestId: string, runToken: number): Promise<void> {
  try {
    const progress = await getJevDialogueAiStatus(requestId);
    if (runToken !== activeRunToken) return;
    stateStore.update((state) => state.requestId === requestId ? { ...state, progress } : state);
    if (progress.status === 'queued' || progress.status === 'running') {
      progressPollTimer = setTimeout(() => void pollStatus(requestId, runToken), 350);
    }
  } catch {
    if (runToken !== activeRunToken) return;
    progressPollTimer = setTimeout(() => void pollStatus(requestId, runToken), 500);
  }
}

function updateResult(lineId: number, updates: Partial<JevDialogueAiDisplayResult>): void {
  stateStore.update((state) => ({
    ...state,
    results: state.results.map((result) => result.lineId === lineId
      ? { ...result, ...updates }
      : result),
  }));
}

async function autoApplyJevSuggestions(
  results: JevDialogueAiDisplayResult[]
): Promise<{ appliedCount: number; errorCount: number }> {
  if (!controller) return { appliedCount: 0, errorCount: 0 };
  const activeController = controller;

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
  for (const [characterName, lineIds] of groupedAssignments.entries()) {
    try {
      await activeController.ensureAndAssignCharacters(lineIds, characterName);
      appliedCount += lineIds.length;
      for (const lineId of lineIds) {
        updateResult(lineId, {
          uiStatus: 'applied',
          uiMessage: `Applied ${characterName} automatically.`,
        });
      }
    } catch (error) {
      errorCount += lineIds.length;
      const message = error instanceof Error ? error.message : String(error);
      for (const lineId of lineIds) {
        updateResult(lineId, {
          uiStatus: 'error',
          uiMessage: message,
        });
      }
    }
  }

  return { appliedCount, errorCount };
}

export function registerJevDialogueAiController(nextController: DialogueAiAssistController): () => void {
  controller = nextController;
  return () => {
    if (controller === nextController) controller = null;
  };
}

export function resetJevDialogueAiState(): void {
  activeRunToken += 1;
  stopProgressPolling();
  stateStore.set(initialState);
}

export async function runJevDialogueAiForCurrentChapter(): Promise<void> {
  const chapterInput: DialogueAiAssistChapterInput | null = controller?.getChapterInput() ?? null;
  if (!controller || !chapterInput || get(stateStore).running) return;

  const requestId = createJevDialogueAiRequestId();
  const startedAt = new Date().toISOString();
  const runToken = ++activeRunToken;
  const totalLines = countReviewableDialogueLines(chapterInput);

  stopProgressPolling();
  stateStore.set({
    ...initialState,
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
  });
  void pollStatus(requestId, runToken);

  try {
    const response = await runJevDialogueAi({
      requestId,
      chapterPath: chapterInput.chapterPath,
      chapterText: chapterInput.rawText,
      dialogue: buildDialogueAiPayload(chapterInput),
    });
    if (runToken !== activeRunToken) return;

    const summary = response.summary;
    stateStore.update((state) => ({
      ...state,
      summary,
      results: response.results.map((result) => ({
        ...result,
        uiStatus: 'pending',
        uiMessage: null,
      })),
    }));

    const { appliedCount, errorCount: applyErrorCount } = await autoApplyJevSuggestions(
      get(stateStore).results
    );
    if (runToken !== activeRunToken) return;

    stateStore.update((state) => ({
      ...state,
      running: false,
      statusTone: summary.errorCount > 0 || applyErrorCount > 0 ? 'info' : 'success',
      statusMessage: `JEV complete. Scanned ${summary.scannedLines} lines, automatically assigned ${appliedCount}, ${summary.unresolvedCount} unresolved${applyErrorCount > 0 ? `, ${applyErrorCount} assignment errors` : ''}.`,
      progress: state.progress ? {
        ...state.progress,
        status: 'completed',
        processedLines: summary.scannedLines,
        totalLines: summary.scannedLines,
        progressRatio: summary.scannedLines > 0 ? 1 : 0,
        updatedAt: new Date().toISOString(),
      } : state.progress,
    }));
  } catch (error) {
    if (runToken !== activeRunToken) return;
    const message = error instanceof Error ? error.message : String(error);
    stateStore.update((state) => ({
      ...state,
      running: false,
      error: message,
      statusTone: 'error',
      statusMessage: 'JEV assist failed. No assignments were changed. If the message mentions a missing API key, set VERCEL_JEV_API_KEY on the backend and try again.',
      progress: state.progress ? {
        ...state.progress,
        status: 'failed',
        error: message,
        updatedAt: new Date().toISOString(),
      } : state.progress,
    }));
  } finally {
    if (runToken === activeRunToken) stopProgressPolling();
  }
}

export async function applyJevDialogueAiSuggestion(result: JevDialogueAiDisplayResult): Promise<void> {
  const characterName = result.suggestedCharacterName?.trim();
  if (!controller || !characterName || result.outcome !== 'suggestion') return;
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

export function jumpToJevDialogueAiResult(lineId: number): void {
  conflictCursor.set(lineId);
}
