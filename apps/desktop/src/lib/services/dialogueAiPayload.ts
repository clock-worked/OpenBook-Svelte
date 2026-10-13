import type { DialogueJson } from '$lib/types';
import type { UnifiedLine } from '$lib/components/chapter/tools/types';
import { normalizeCharacterKey } from '$lib/services/characterDomain';
import type {
  DialogueAiAssistRequest,
  LocalDialogueAiRequest,
} from '$lib/services/apiContracts';

/** Stats block carried on the parsed dialogue document (see DialogueJson). */
export type DialogueAiAssistScriptStats = NonNullable<DialogueJson['stats']>;

export type DialogueAiAssistChapterInput = {
  chapterPath: string;
  chapterTitle: string;
  rawText: string;
  normalizedScript: { lines: UnifiedLine[]; stats: DialogueAiAssistScriptStats | null };
};

export type DialogueAiAssistController = {
  getChapterInput: () => DialogueAiAssistChapterInput | null;
  ensureAndAssignCharacters: (lineIds: number[], characterName: string) => Promise<void>;
};

/**
 * Builds the shared `dialogue` payload sent to every speaker-attribution
 * provider from the chapter's normalized script.
 */
export function buildDialogueAiPayload(input: DialogueAiAssistChapterInput): DialogueJson {
  return {
    formatVersion: '2.0',
    chapterId: input.chapterTitle,
    lines: input.normalizedScript.lines.map((line) => ({
      id: line.id,
      characterId: line.characterName
        ? normalizeCharacterKey(line.characterName)
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
        characterId: normalizeCharacterKey(candidate.name),
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

// === Per-provider request payloads ===

export type DialogueAiAssistRunArgs = {
  requestId?: string;
  chapterPath: string;
  chapterText: string;
  dialogue: DialogueJson;
  autoApplyThreshold?: number;
  maxLines?: number | null;
  dryRun?: boolean;
};

export function buildDialogueAiAssistRequest(
  args: DialogueAiAssistRunArgs
): DialogueAiAssistRequest {
  return {
    request_id: args.requestId,
    chapter_path: args.chapterPath,
    chapter_text: args.chapterText,
    dialogue: args.dialogue,
    auto_apply_threshold: args.autoApplyThreshold,
    max_lines: args.maxLines,
    dry_run: args.dryRun,
  };
}

// JEV runs with fixed backend parameters (no server configuration):
// 1500-char context window, scene roster capped at 51 characters + "None".
export const JEV_FIXED_CONTEXT_WINDOW = 1500;

export type JevDialogueAiRunArgs = {
  requestId?: string;
  chapterPath: string;
  chapterText: string;
  dialogue: DialogueJson;
  maxLines?: number | null;
};

export function buildJevDialogueAiRequest(
  args: JevDialogueAiRunArgs
): LocalDialogueAiRequest {
  return {
    request_id: args.requestId,
    chapter_path: args.chapterPath,
    chapter_text: args.chapterText,
    dialogue: args.dialogue,
    context_window: JEV_FIXED_CONTEXT_WINDOW,
    include_previous_speaker: true,
    include_speaker_context: true,
    max_lines: args.maxLines,
  };
}

export type LocalDialogueAiRunArgs = {
  requestId?: string;
  chapterPath: string;
  chapterText: string;
  dialogue: DialogueJson;
  contextWindow: number;
  includePreviousSpeaker: boolean;
  includeSpeakerContext: boolean;
  maxLines?: number | null;
};

export function buildLocalDialogueAiRequest(
  args: LocalDialogueAiRunArgs
): LocalDialogueAiRequest {
  return {
    request_id: args.requestId,
    chapter_path: args.chapterPath,
    chapter_text: args.chapterText,
    dialogue: args.dialogue,
    context_window: args.contextWindow,
    include_previous_speaker: args.includePreviousSpeaker,
    include_speaker_context: args.includeSpeakerContext,
    max_lines: args.maxLines,
  };
}
