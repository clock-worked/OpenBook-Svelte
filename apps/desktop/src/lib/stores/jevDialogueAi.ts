import { writable } from 'svelte/store';
import type {
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
  type DialogueAiAssistController,
  type JevDialogueAiRunArgs,
} from '$lib/services/dialogueAiPayload';
import {
  autoApplyGroupedSuggestions,
  createProviderAssistStore,
  type AiAssistBaseState,
  type AiAssistUiResult,
} from '$lib/stores/providerAssist';

type JevDialogueAiDisplayResult = AiAssistUiResult<LocalDialogueAiLineResult>;

type JevDialogueAiState = AiAssistBaseState<
  LocalDialogueAiSummary,
  LocalDialogueAiLineResult
>;

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

const assist = createProviderAssistStore<
  JevDialogueAiRunArgs,
  LocalDialogueAiSummary,
  LocalDialogueAiLineResult,
  JevDialogueAiState
>({
  client: {
    createRequestId: createJevDialogueAiRequestId,
    run: (args) => runJevDialogueAi(args),
    getStatus: getJevDialogueAiStatus,
  },
  initialState,
  visibleResultsFilter: (result) => {
    if (result.uiStatus === 'error') return true;
    if (result.uiStatus === 'applied') return false;
    return result.outcome !== 'keep_existing';
  },
  buildRunArgs: ({ requestId, chapterInput }) => ({
    requestId,
    chapterPath: chapterInput.chapterPath,
    chapterText: chapterInput.rawText,
    dialogue: buildDialogueAiPayload(chapterInput),
  }),
  autoApply: (results, api) => autoApplyGroupedSuggestions(results, api),
  completionTone: (summary, counts) =>
    summary.errorCount > 0 || counts.errorCount > 0 ? 'info' : 'success',
  completionMessage: (summary, counts) =>
    `JEV complete. Scanned ${summary.scannedLines} lines, automatically assigned ${counts.appliedCount}, ${summary.unresolvedCount} unresolved${counts.errorCount > 0 ? `, ${counts.errorCount} assignment errors` : ''}.`,
  failureMessage:
    'JEV assist failed. No assignments were changed. If the message mentions a missing API key, set VERCEL_JEV_API_KEY on the backend and try again.',
});

export const jevDialogueAiState = { subscribe: assist.stateStore.subscribe };

export const jevDialogueAiVisibleResults = assist.visibleResults;

// Parse-time JEV verification summary (meta.jevVerify). Populated by the
// parser service after each parse; null when the parse did not include it.
export const jevVerifySummary = writable<JevVerifySummary | null>(null);

export function setJevVerifySummary(summary: JevVerifySummary | null): void {
  jevVerifySummary.set(summary);
}

export function resetJevVerifySummary(): void {
  jevVerifySummary.set(null);
}

export function registerJevDialogueAiController(
  nextController: DialogueAiAssistController
): () => void {
  return assist.registerController(nextController);
}

export function resetJevDialogueAiState(): void {
  assist.resetState();
}

export function runJevDialogueAiForCurrentChapter(): Promise<void> {
  return assist.runForCurrentChapter();
}

export async function applyJevDialogueAiSuggestion(
  result: JevDialogueAiDisplayResult
): Promise<void> {
  if (result.outcome !== 'suggestion') return;
  await assist.applyCharacterSuggestion(
    result,
    result.suggestedCharacterName?.trim() || null
  );
}
