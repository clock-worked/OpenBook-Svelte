import { addBookCharacterAlias } from '$lib/stores/bookCharacters';
import type {
  DialogueAiAssistLineResult,
  DialogueAiAssistSummary,
} from '$lib/services/apiContracts';
import {
  createDialogueAiAssistRequestId,
  getDialogueAiAssistStatus,
  runDialogueAiAssist,
} from '$lib/services/dialogueAi';
import {
  buildDialogueAiPayload,
  type DialogueAiAssistChapterInput,
  type DialogueAiAssistController,
  type DialogueAiAssistRunArgs,
} from '$lib/services/dialogueAiPayload';
import {
  createProviderAssistStore,
  type AiAssistBaseState,
  type AiAssistUiResult,
  type ProviderAssistApi,
} from '$lib/stores/providerAssist';

// Shared payload helpers live in the neutral payload module; re-exported here
// for existing import sites.
export {
  buildDialogueAiPayload,
  countReviewableDialogueLines,
  formatDialogueAiReasonCodes,
  formatDialogueAiToolTrace,
} from '$lib/services/dialogueAiPayload';
export type {
  DialogueAiAssistChapterInput,
  DialogueAiAssistController,
} from '$lib/services/dialogueAiPayload';

type DialogueAiAssistDisplayResult = AiAssistUiResult<DialogueAiAssistLineResult>;

type DialogueAiAssistState = AiAssistBaseState<
  DialogueAiAssistSummary,
  DialogueAiAssistLineResult
>;

const initialState: DialogueAiAssistState = {
  running: false,
  error: null,
  statusMessage: null,
  statusTone: 'info',
  summary: null,
  results: [],
  requestId: null,
  progress: null,
};

function summarizeAiRun(args: {
  summary: DialogueAiAssistSummary;
  reassignedCount: number;
  confirmedCount: number;
}): string {
  const parts = [
    `Scanned ${args.summary.scannedLines} lines`,
    `${args.reassignedCount} reassigned`,
    `${args.confirmedCount} confirmed`,
  ];

  if (args.summary.reviewCount > 0) parts.push(`${args.summary.reviewCount} review`);
  if (args.summary.aliasReviewCount > 0) parts.push(`${args.summary.aliasReviewCount} alias`);
  if (args.summary.newCharacterCount > 0) parts.push(`${args.summary.newCharacterCount} new character`);
  if (args.summary.errorCount > 0) parts.push(`${args.summary.errorCount} errors`);
  if ((args.summary.cacheHits ?? 0) > 0) parts.push(`${args.summary.cacheHits} cached calls`);
  if ((args.summary.modelCalls ?? 0) > 0) parts.push(`${args.summary.modelCalls} live calls`);

  return `AI assist complete. ${parts.join(', ')}.`;
}

/**
 * Gemini auto-apply: `keep_existing` dispositions confirm the current
 * assignment; `reassign_existing` dispositions are grouped by suggested
 * character and skipped when the suggestion already matches the line.
 */
async function autoApplyGeminiResults(
  results: DialogueAiAssistDisplayResult[],
  api: ProviderAssistApi<DialogueAiAssistLineResult>
): Promise<{ reassignedCount: number; confirmedCount: number }> {
  const active = api.getController();
  if (!active) return { reassignedCount: 0, confirmedCount: 0 };

  const chapterInput = active.getChapterInput();
  if (!chapterInput) return { reassignedCount: 0, confirmedCount: 0 };

  const linesById = new Map(
    chapterInput.normalizedScript.lines.map((line) => [line.id, line] as const)
  );
  const groupedAssignments = new Map<string, number[]>();
  let confirmedCount = 0;

  for (const result of results) {
    if (result.disposition !== 'auto_apply') continue;

    if (result.action === 'keep_existing') {
      confirmedCount += 1;
      api.updateResult(result.lineId, {
        uiStatus: 'applied',
        uiMessage: 'Confirmed existing assignment.',
      });
      continue;
    }

    if (result.action !== 'reassign_existing') continue;
    if (!result.suggestedCharacterId) continue;

    const suggestedName = result.suggestedCharacterName?.trim();
    if (!suggestedName) continue;

    const currentLine = linesById.get(result.lineId);
    const currentName = currentLine?.characterName?.trim()
      || result.currentCharacterName?.trim()
      || null;

    if (currentName === suggestedName) {
      confirmedCount += 1;
      api.updateResult(result.lineId, {
        uiStatus: 'applied',
        uiMessage: 'Suggested character already matched the current assignment.',
      });
      continue;
    }

    const lineIds = groupedAssignments.get(suggestedName) ?? [];
    lineIds.push(result.lineId);
    groupedAssignments.set(suggestedName, lineIds);
  }

  let reassignedCount = 0;
  for (const [characterName, lineIds] of groupedAssignments.entries()) {
    await active.ensureAndAssignCharacters(lineIds, characterName);
    reassignedCount += lineIds.length;

    for (const lineId of lineIds) {
      api.updateResult(lineId, {
        uiStatus: 'applied',
        uiMessage: `Applied ${characterName} automatically.`,
      });
    }
  }

  return { reassignedCount, confirmedCount };
}

const assist = createProviderAssistStore<
  DialogueAiAssistRunArgs,
  DialogueAiAssistSummary,
  DialogueAiAssistLineResult,
  DialogueAiAssistState
>({
  client: {
    createRequestId: createDialogueAiAssistRequestId,
    run: (args) => runDialogueAiAssist(args),
    getStatus: getDialogueAiAssistStatus,
  },
  initialState,
  visibleResultsFilter: (result) => {
    if (result.uiStatus === 'error') return true;
    if (result.uiStatus === 'applied') return false;
    return result.disposition !== 'auto_apply';
  },
  buildRunArgs: ({ requestId, chapterInput }) => ({
    requestId,
    chapterPath: chapterInput.chapterPath,
    chapterText: chapterInput.rawText,
    dialogue: buildDialogueAiPayload(chapterInput),
  }),
  autoApply: (results, api) => autoApplyGeminiResults(results, api),
  completionTone: (summary) => (summary.errorCount > 0 ? 'info' : 'success'),
  completionMessage: (summary, counts) =>
    summarizeAiRun({
      summary,
      reassignedCount: counts.reassignedCount,
      confirmedCount: counts.confirmedCount,
    }),
  failureMessage: 'AI assist failed.',
  completedProgressPatch: (summary, progress) => ({
    logDirectory: summary.logDirectory ?? progress.logDirectory,
  }),
});

export const dialogueAiAssistState = {
  subscribe: assist.stateStore.subscribe,
};

export const dialogueAiAssistVisibleResults = assist.visibleResults;

export function registerDialogueAiAssistController(
  nextController: DialogueAiAssistController
): () => void {
  return assist.registerController(nextController);
}

export function resetDialogueAiAssistState(): void {
  assist.resetState();
}

export function runDialogueAiAssistForCurrentChapter(): Promise<void> {
  return assist.runForCurrentChapter();
}

export async function applyDialogueAiCharacterSuggestion(
  result: DialogueAiAssistDisplayResult
): Promise<void> {
  await assist.applyCharacterSuggestion(
    result,
    result.suggestedCharacterName?.trim() || null
  );
}

export async function applyDialogueAiAliasSuggestion(
  result: DialogueAiAssistDisplayResult
): Promise<void> {
  const aliasProposal = result.aliasToAdd;
  if (!aliasProposal) return;

  try {
    await addBookCharacterAlias(aliasProposal.characterName, aliasProposal.alias);
    assist.updateResult(result.lineId, {
      uiStatus: 'applied',
      uiMessage: `Added alias ${aliasProposal.alias} to ${aliasProposal.characterName}.`,
    });
  } catch (error) {
    assist.updateResult(result.lineId, {
      uiStatus: 'error',
      uiMessage: error instanceof Error ? error.message : String(error),
    });
  }
}
