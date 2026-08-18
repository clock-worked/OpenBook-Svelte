import { derived, get, writable } from 'svelte/store';

import type { UnifiedLine } from '$lib/components/chapter/tools/types';
import { conflictCursor } from '$lib/stores/selection';
import { addBookCharacterAlias } from '$lib/stores/bookCharacters';
import type {
  DialogueAiAssistLineResult,
  DialogueAiAssistStatus,
  DialogueAiAssistSummary,
} from '$lib/services/apiContracts';
import {
  createDialogueAiAssistRequestId,
  getDialogueAiAssistStatus,
  runDialogueAiAssist,
} from '$lib/services/dialogueAi';

type DialogueAiAssistDisplayResult = DialogueAiAssistLineResult & {
  uiStatus?: 'pending' | 'applied' | 'error';
  uiMessage?: string | null;
};

type DialogueAiAssistTone = 'info' | 'success' | 'error';

export type DialogueAiAssistChapterInput = {
  chapterPath: string;
  chapterTitle: string;
  rawText: string;
  normalizedScript: { lines: UnifiedLine[]; stats: any };
};

export type DialogueAiAssistController = {
  getChapterInput: () => DialogueAiAssistChapterInput | null;
  ensureAndAssignCharacters: (lineIds: number[], characterName: string) => Promise<void>;
};

type DialogueAiAssistState = {
  running: boolean;
  error: string | null;
  statusMessage: string | null;
  statusTone: DialogueAiAssistTone;
  summary: DialogueAiAssistSummary | null;
  results: DialogueAiAssistDisplayResult[];
  requestId: string | null;
  progress: DialogueAiAssistStatus | null;
};

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

const dialogueAiAssistStateStore = writable<DialogueAiAssistState>(initialState);

let controller: DialogueAiAssistController | null = null;
let activeRunToken = 0;
let progressPollTimer: ReturnType<typeof setTimeout> | null = null;

export const dialogueAiAssistState = {
  subscribe: dialogueAiAssistStateStore.subscribe,
};

export const dialogueAiAssistVisibleResults = derived(
  dialogueAiAssistStateStore,
  (state) => state.results.filter((result) => {
    if (result.uiStatus === 'error') return true;
    if (result.uiStatus === 'applied') return false;
    return result.disposition !== 'auto_apply';
  })
);

function stopProgressPolling(): void {
  if (progressPollTimer !== null) {
    clearTimeout(progressPollTimer);
    progressPollTimer = null;
  }
}

function updateAiAssistResult(
  lineId: number,
  updates: Partial<DialogueAiAssistDisplayResult>
): void {
  dialogueAiAssistStateStore.update((state) => ({
    ...state,
    results: state.results.map((result) => (
      result.lineId === lineId ? { ...result, ...updates } : result
    )),
  }));
}

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

export function buildDialogueAiPayload(input: DialogueAiAssistChapterInput) {
  return {
    formatVersion: '2.0' as const,
    chapterId: input.chapterTitle,
    lines: input.normalizedScript.lines.map((line) => ({
      id: line.id,
      characterId: line.characterName
        ? line.characterName.toLowerCase().trim().replace(/\s+/g, '_')
        : null,
      text: line.text,
      span: line.span,
      metadata: {
        emotion: null,
        intensity: 1,
        pacing: null,
        prefix: null,
        customTags: {},
      },
      candidates: (line.candidates || []).map((candidate) => ({
        characterId: candidate.name.toLowerCase().trim().replace(/\s+/g, '_'),
        confidence: candidate.confidence,
      })),
      isConflict: line.isConflict,
      isReturning: !!line.isReturning,
      attribution: line.attribution
        ? {
            confidence: line.attribution.confidence,
            topCandidateConfidence: line.attribution.topCandidateConfidence,
            marginToSecond: line.attribution.marginToSecond,
            misattributionRisk: line.attribution.misattributionRisk,
            resolutionStatus: line.attribution.resolutionStatus,
            thresholdUsed: line.attribution.thresholdUsed,
            sourceAlias: line.attribution.sourceAlias,
            sourceCandidates: line.attribution.sourceCandidates,
            sourceDescriptors: line.attribution.sourceDescriptors,
            contextGender: line.attribution.contextGender,
            contextGenderCue: line.attribution.contextGenderCue,
            genderConflict: line.attribution.genderConflict,
            parserBackend: line.attribution.parserBackend,
            decisionTrace: line.attribution.decisionTrace,
            candidates: (line.attribution.candidates || []).map((candidate) => ({
              characterId: candidate.characterId ?? null,
              name: candidate.name,
              confidence: candidate.confidence,
              reasons: candidate.reasons,
            })),
          }
        : undefined,
    })),
    stats: {
      totalLines:
        input.normalizedScript.stats?.totalLines ?? input.normalizedScript.lines.length,
      conflicts:
        input.normalizedScript.stats?.conflicts
        ?? input.normalizedScript.lines.filter((line) => line.isConflict).length,
      characterBreakdown: input.normalizedScript.stats?.characterBreakdown ?? {},
    },
  };
}

export function countReviewableDialogueLines(input: DialogueAiAssistChapterInput): number {
  return input.normalizedScript.lines.filter((line) => {
    const characterName = String(line.characterName ?? '').trim().toLowerCase();
    return characterName.length > 0 && characterName !== 'narrator';
  }).length;
}

async function autoApplyAiResults(
  results: DialogueAiAssistDisplayResult[]
): Promise<{ reassignedCount: number; confirmedCount: number }> {
  if (!controller) return { reassignedCount: 0, confirmedCount: 0 };

  const chapterInput = controller.getChapterInput();
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
      updateAiAssistResult(result.lineId, {
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
      updateAiAssistResult(result.lineId, {
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
    await controller.ensureAndAssignCharacters(lineIds, characterName);
    reassignedCount += lineIds.length;

    for (const lineId of lineIds) {
      updateAiAssistResult(lineId, {
        uiStatus: 'applied',
        uiMessage: `Applied ${characterName} automatically.`,
      });
    }
  }

  return { reassignedCount, confirmedCount };
}

async function pollDialogueAiAssistStatus(
  requestId: string,
  runToken: number
): Promise<void> {
  try {
    const status = await getDialogueAiAssistStatus(requestId);

    if (runToken !== activeRunToken) return;

    dialogueAiAssistStateStore.update((state) => {
      if (state.requestId !== requestId) return state;
      return {
        ...state,
        progress: status,
      };
    });

    if (status.status === 'queued' || status.status === 'running') {
      progressPollTimer = setTimeout(() => {
        void pollDialogueAiAssistStatus(requestId, runToken);
      }, 350);
    }
  } catch {
    if (runToken !== activeRunToken) return;

    progressPollTimer = setTimeout(() => {
      void pollDialogueAiAssistStatus(requestId, runToken);
    }, 500);
  }
}

export function registerDialogueAiAssistController(
  nextController: DialogueAiAssistController
): () => void {
  controller = nextController;

  return () => {
    if (controller === nextController) {
      controller = null;
    }
  };
}

export function resetDialogueAiAssistState(): void {
  activeRunToken += 1;
  stopProgressPolling();
  dialogueAiAssistStateStore.set(initialState);
}

export async function runDialogueAiAssistForCurrentChapter(): Promise<void> {
  const chapterInput = controller?.getChapterInput() ?? null;
  if (!controller || !chapterInput) return;

  const currentState = get(dialogueAiAssistStateStore);
  if (currentState.running) return;

  const requestId = createDialogueAiAssistRequestId();
  const startedAt = new Date().toISOString();
  const runToken = ++activeRunToken;
  const totalReviewableLines = countReviewableDialogueLines(chapterInput);

  stopProgressPolling();
  dialogueAiAssistStateStore.set({
    ...initialState,
    running: true,
    requestId,
    progress: {
      requestId,
      status: 'queued',
      processedLines: 0,
      totalLines: totalReviewableLines,
      progressRatio: 0,
      startedAt,
      updatedAt: startedAt,
      chapterPath: chapterInput.chapterPath,
      error: null,
      logDirectory: null,
    },
  });

  void pollDialogueAiAssistStatus(requestId, runToken);

  try {
    const response = await runDialogueAiAssist({
      requestId,
      chapterPath: chapterInput.chapterPath,
      chapterText: chapterInput.rawText,
      dialogue: buildDialogueAiPayload(chapterInput),
    });

    if (runToken !== activeRunToken) return;

    dialogueAiAssistStateStore.update((state) => ({
      ...state,
      summary: response.summary,
      results: response.results.map((result) => ({
        ...result,
        uiStatus: 'pending',
        uiMessage: null,
      })),
    }));

    const { reassignedCount, confirmedCount } = await autoApplyAiResults(
      get(dialogueAiAssistStateStore).results
    );

    if (runToken !== activeRunToken) return;

    dialogueAiAssistStateStore.update((state) => ({
      ...state,
      running: false,
      statusTone: response.summary.errorCount > 0 ? 'info' : 'success',
      statusMessage: summarizeAiRun({
        summary: response.summary,
        reassignedCount,
        confirmedCount,
      }),
      progress: state.progress
        ? {
            ...state.progress,
            status: 'completed',
            processedLines: response.summary.scannedLines,
            totalLines: response.summary.scannedLines,
            progressRatio: response.summary.scannedLines > 0 ? 1 : 0,
            updatedAt: new Date().toISOString(),
            logDirectory: response.summary.logDirectory ?? state.progress.logDirectory,
          }
        : state.progress,
    }));
  } catch (error) {
    if (runToken !== activeRunToken) return;

    dialogueAiAssistStateStore.update((state) => ({
      ...state,
      running: false,
      error: error instanceof Error ? error.message : String(error),
      statusTone: 'error',
      statusMessage: 'AI assist failed.',
      progress: state.progress
        ? {
            ...state.progress,
            status: 'failed',
            error: error instanceof Error ? error.message : String(error),
            updatedAt: new Date().toISOString(),
          }
        : state.progress,
    }));
  } finally {
    if (runToken === activeRunToken) {
      stopProgressPolling();
    }
  }
}

export async function applyDialogueAiCharacterSuggestion(
  result: DialogueAiAssistDisplayResult
): Promise<void> {
  const characterName = result.suggestedCharacterName?.trim();
  if (!controller || !characterName) return;

  try {
    await controller.ensureAndAssignCharacters([result.lineId], characterName);
    updateAiAssistResult(result.lineId, {
      uiStatus: 'applied',
      uiMessage: `Applied ${characterName}.`,
    });
  } catch (error) {
    updateAiAssistResult(result.lineId, {
      uiStatus: 'error',
      uiMessage: error instanceof Error ? error.message : String(error),
    });
  }
}

export async function applyDialogueAiAliasSuggestion(
  result: DialogueAiAssistDisplayResult
): Promise<void> {
  const aliasProposal = result.aliasToAdd;
  if (!aliasProposal) return;

  try {
    await addBookCharacterAlias(aliasProposal.characterName, aliasProposal.alias);
    updateAiAssistResult(result.lineId, {
      uiStatus: 'applied',
      uiMessage: `Added alias ${aliasProposal.alias} to ${aliasProposal.characterName}.`,
    });
  } catch (error) {
    updateAiAssistResult(result.lineId, {
      uiStatus: 'error',
      uiMessage: error instanceof Error ? error.message : String(error),
    });
  }
}

export function jumpToDialogueAiResult(lineId: number): void {
  conflictCursor.set(lineId);
}

export function formatDialogueAiReasonCodes(reasonCodes: string[]): string {
  return reasonCodes.map((code) => code.replace(/_/g, ' ')).join(', ');
}

export function formatDialogueAiToolTrace(
  toolTrace: Array<{ tool: string; status: string; note?: string | null }>
): string {
  return toolTrace
    .map((entry) => (
      entry.note
        ? `${entry.tool} (${entry.status}: ${entry.note})`
        : `${entry.tool} (${entry.status})`
    ))
    .join(', ');
}
