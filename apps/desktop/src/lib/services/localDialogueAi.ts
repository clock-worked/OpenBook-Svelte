import { API_ENDPOINTS, apiPostJson, apiRequestJson } from '$lib/services/apiClient';
import type { DialogueJson } from '$lib/types';
import type {
  DialogueAiAssistStatus,
  LocalDialogueAiRequest,
  LocalDialogueAiResponse,
} from '$lib/services/apiContracts';

export function createLocalDialogueAiRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `local-dialogue-ai-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function runLocalDialogueAi(args: {
  requestId?: string;
  chapterPath: string;
  chapterText: string;
  dialogue: DialogueJson;
  contextWindow: number;
  includePreviousSpeaker: boolean;
  includeSpeakerContext: boolean;
  maxLines?: number | null;
}): Promise<LocalDialogueAiResponse> {
  const payload: LocalDialogueAiRequest = {
    request_id: args.requestId,
    chapter_path: args.chapterPath,
    chapter_text: args.chapterText,
    dialogue: args.dialogue,
    context_window: args.contextWindow,
    include_previous_speaker: args.includePreviousSpeaker,
    include_speaker_context: args.includeSpeakerContext,
    max_lines: args.maxLines,
  };
  return apiPostJson<LocalDialogueAiResponse, LocalDialogueAiRequest>(
    API_ENDPOINTS.localDialogueAi,
    payload
  );
}

export async function getLocalDialogueAiStatus(
  requestId: string
): Promise<DialogueAiAssistStatus> {
  const endpoint = API_ENDPOINTS.localDialogueAiStatus(requestId);
  const separator = endpoint.includes('?') ? '&' : '?';
  return apiRequestJson<DialogueAiAssistStatus>(
    `${endpoint}${separator}_ts=${Date.now()}`,
    { cache: 'no-store' }
  );
}
