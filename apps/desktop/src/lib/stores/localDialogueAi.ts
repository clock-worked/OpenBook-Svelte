import { derived, get, writable } from 'svelte/store';

import { conflictCursor } from '$lib/stores/selection';
import type {
  DialogueAiAssistStatus,
  LocalDialogueAiLineResult,
  LocalDialogueAiSummary,
} from '$lib/services/apiContracts';
import {
  createLocalDialogueAiRequestId,
  getLocalDialogueAiStatus,
  runLocalDialogueAi,
} from '$lib/services/localDialogueAi';
import {
  buildDialogueAiPayload,
  countReviewableDialogueLines,
  type DialogueAiAssistChapterInput,
  type DialogueAiAssistController,
} from '$lib/stores/dialogueAiAssist';

type LocalDialogueAiDisplayResult = LocalDialogueAiLineResult & {
  uiStatus?: 'pending' | 'applied' | 'error';
  uiMessage?: string | null;
};

export type LocalDialogueAiSettings = {
  contextWindow: number;
  includePreviousSpeaker: boolean;
  includeSpeakerContext: boolean;
};

type LocalDialogueAiState = {
  running: boolean;
  error: string | null;
  statusMessage: string | null;
  statusTone: 'info' | 'success' | 'error';
  summary: LocalDialogueAiSummary | null;
  results: LocalDialogueAiDisplayResult[];
  requestId: string | null;
  progress: DialogueAiAssistStatus | null;
  settings: LocalDialogueAiSettings;
};

const defaultSettings: LocalDialogueAiSettings = {
  contextWindow: 600,
  includePreviousSpeaker: true,
  includeSpeakerContext: true,
};

const initialState: LocalDialogueAiState = {
  running: false,
  error: null,
  statusMessage: null,
  statusTone: 'info',
  summary: null,
  results: [],
  requestId: null,
  progress: null,
  settings: defaultSettings,
};

const stateStore = writable<LocalDialogueAiState>(initialState);
let controller: DialogueAiAssistController | null = null;
let activeRunToken = 0;
let progressPollTimer: ReturnType<typeof setTimeout> | null = null;

export const localDialogueAiState = { subscribe: stateStore.subscribe };

export const localDialogueAiVisibleResults = derived(
  stateStore,
  (state) => state.results.filter((result) => {
    if (result.uiStatus === 'error') return true;
    if (result.uiStatus === 'applied') return false;
    return result.outcome !== 'keep_existing';
  })
);

function stopProgressPolling(): void {
  if (progressPollTimer !== null) {
    clearTimeout(progressPollTimer);
    progressPollTimer = null;
  }
}

async function pollStatus(requestId: string, runToken: number): Promise<void> {
  try {
    const progress = await getLocalDialogueAiStatus(requestId);
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

function updateResult(lineId: number, updates: Partial<LocalDialogueAiDisplayResult>): void {
  stateStore.update((state) => ({
    ...state,
    results: state.results.map((result) => result.lineId === lineId
      ? { ...result, ...updates }
      : result),
  }));
}

async function autoApplyLocalSuggestions(
  results: LocalDialogueAiDisplayResult[]
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

export function registerLocalDialogueAiController(nextController: DialogueAiAssistController): () => void {
  controller = nextController;
  return () => {
    if (controller === nextController) controller = null;
  };
}

export function updateLocalDialogueAiSettings(updates: Partial<LocalDialogueAiSettings>): void {
  stateStore.update((state) => ({
    ...state,
    settings: {
      ...state.settings,
      ...updates,
      contextWindow: Math.max(0, Math.min(8000, updates.contextWindow ?? state.settings.contextWindow)),
    },
  }));
}

export function resetLocalDialogueAiState(): void {
  activeRunToken += 1;
  stopProgressPolling();
  const settings = get(stateStore).settings;
  stateStore.set({ ...initialState, settings });
}

export async function runLocalDialogueAiForCurrentChapter(): Promise<void> {
  const chapterInput: DialogueAiAssistChapterInput | null = controller?.getChapterInput() ?? null;
  if (!controller || !chapterInput || get(stateStore).running) return;

  const requestId = createLocalDialogueAiRequestId();
  const startedAt = new Date().toISOString();
  const runToken = ++activeRunToken;
  const settings = get(stateStore).settings;
  const totalLines = countReviewableDialogueLines(chapterInput);

  stopProgressPolling();
  stateStore.set({
    ...initialState,
    settings,
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
    const response = await runLocalDialogueAi({
      requestId,
      chapterPath: chapterInput.chapterPath,
      chapterText: chapterInput.rawText,
      dialogue: buildDialogueAiPayload(chapterInput),
      contextWindow: settings.contextWindow,
      includePreviousSpeaker: settings.includePreviousSpeaker,
      includeSpeakerContext: settings.includeSpeakerContext,
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

    const { appliedCount, errorCount: applyErrorCount } = await autoApplyLocalSuggestions(
      get(stateStore).results
    );
    if (runToken !== activeRunToken) return;

    stateStore.update((state) => ({
      ...state,
      running: false,
      statusTone: summary.errorCount > 0 || applyErrorCount > 0 ? 'info' : 'success',
      statusMessage: `Local AI complete. Scanned ${summary.scannedLines} lines, automatically assigned ${appliedCount}, ${summary.unresolvedCount} unresolved${applyErrorCount > 0 ? `, ${applyErrorCount} assignment errors` : ''}.`,
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
      statusMessage: 'Local AI failed. Make sure LM Studio is running and the model is loaded.',
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

export async function applyLocalDialogueAiSuggestion(result: LocalDialogueAiDisplayResult): Promise<void> {
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

export function jumpToLocalDialogueAiResult(lineId: number): void {
  conflictCursor.set(lineId);
}
