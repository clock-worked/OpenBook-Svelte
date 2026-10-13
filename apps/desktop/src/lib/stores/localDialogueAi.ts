import {
  createLocalDialogueAiRequestId,
  getLocalDialogueAiStatus,
  runLocalDialogueAi,
} from '$lib/services/localDialogueAi';
import {
  buildDialogueAiPayload,
  type DialogueAiAssistController,
  type LocalDialogueAiRunArgs,
} from '$lib/services/dialogueAiPayload';
import {
  autoApplyGroupedSuggestions,
  createProviderAssistStore,
  type AiAssistBaseState,
  type AiAssistUiResult,
} from '$lib/stores/providerAssist';
import type {
  LocalDialogueAiLineResult,
  LocalDialogueAiSummary,
} from '$lib/services/apiContracts';

type LocalDialogueAiDisplayResult = AiAssistUiResult<LocalDialogueAiLineResult>;

export type LocalDialogueAiSettings = {
  contextWindow: number;
  includePreviousSpeaker: boolean;
  includeSpeakerContext: boolean;
};

type LocalDialogueAiState = AiAssistBaseState<
  LocalDialogueAiSummary,
  LocalDialogueAiLineResult
> & {
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

const assist = createProviderAssistStore<
  LocalDialogueAiRunArgs,
  LocalDialogueAiSummary,
  LocalDialogueAiLineResult,
  LocalDialogueAiState
>({
  client: {
    createRequestId: createLocalDialogueAiRequestId,
    run: (args) => runLocalDialogueAi(args),
    getStatus: getLocalDialogueAiStatus,
  },
  initialState,
  visibleResultsFilter: (result) => {
    if (result.uiStatus === 'error') return true;
    if (result.uiStatus === 'applied') return false;
    return result.outcome !== 'keep_existing';
  },
  buildRunArgs: ({ requestId, chapterInput, state }) => ({
    requestId,
    chapterPath: chapterInput.chapterPath,
    chapterText: chapterInput.rawText,
    dialogue: buildDialogueAiPayload(chapterInput),
    contextWindow: state.settings.contextWindow,
    includePreviousSpeaker: state.settings.includePreviousSpeaker,
    includeSpeakerContext: state.settings.includeSpeakerContext,
  }),
  autoApply: (results, api) => autoApplyGroupedSuggestions(results, api),
  completionTone: (summary, counts) =>
    summary.errorCount > 0 || counts.errorCount > 0 ? 'info' : 'success',
  completionMessage: (summary, counts) =>
    `Local AI complete. Scanned ${summary.scannedLines} lines, automatically assigned ${counts.appliedCount}, ${summary.unresolvedCount} unresolved${counts.errorCount > 0 ? `, ${counts.errorCount} assignment errors` : ''}.`,
  failureMessage: 'Local AI failed. Make sure LM Studio is running and the model is loaded.',
  resetPreserve: (state) => ({ settings: state.settings }),
});

export const localDialogueAiState = { subscribe: assist.stateStore.subscribe };

export const localDialogueAiVisibleResults = assist.visibleResults;

export function registerLocalDialogueAiController(
  nextController: DialogueAiAssistController
): () => void {
  return assist.registerController(nextController);
}

export function updateLocalDialogueAiSettings(updates: Partial<LocalDialogueAiSettings>): void {
  assist.stateStore.update((state) => ({
    ...state,
    settings: {
      ...state.settings,
      ...updates,
      contextWindow: Math.max(0, Math.min(8000, updates.contextWindow ?? state.settings.contextWindow)),
    },
  }));
}

export function resetLocalDialogueAiState(): void {
  assist.resetState();
}

export function runLocalDialogueAiForCurrentChapter(): Promise<void> {
  return assist.runForCurrentChapter();
}

export async function applyLocalDialogueAiSuggestion(
  result: LocalDialogueAiDisplayResult
): Promise<void> {
  if (result.outcome !== 'suggestion') return;
  await assist.applyCharacterSuggestion(
    result,
    result.suggestedCharacterName?.trim() || null
  );
}
