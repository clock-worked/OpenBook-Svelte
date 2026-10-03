import { API_ENDPOINTS, apiPostJson, apiRequestJson } from '$lib/services/apiClient';
import type { DialogueJson } from '$lib/types';
import type {
  DialogueAiAssistStatus,
  LocalDialogueAiRequest,
  LocalDialogueAiResponse,
} from '$lib/services/apiContracts';

// JEV runs with fixed backend parameters (no server configuration):
// 1500-char context window, scene roster capped at 51 characters + "None".
export const JEV_FIXED_CONTEXT_WINDOW = 1500;

export function createJevDialogueAiRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `jev-dialogue-ai-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function runJevDialogueAi(args: {
  requestId?: string;
  chapterPath: string;
  chapterText: string;
  dialogue: DialogueJson;
  maxLines?: number | null;
}): Promise<LocalDialogueAiResponse> {
  const payload: LocalDialogueAiRequest = {
    request_id: args.requestId,
    chapter_path: args.chapterPath,
    chapter_text: args.chapterText,
    dialogue: args.dialogue,
    context_window: JEV_FIXED_CONTEXT_WINDOW,
    include_previous_speaker: true,
    include_speaker_context: true,
    max_lines: args.maxLines,
  };
  return apiPostJson<LocalDialogueAiResponse, LocalDialogueAiRequest>(
    API_ENDPOINTS.jevDialogueAi,
    payload
  );
}

export async function getJevDialogueAiStatus(
  requestId: string
): Promise<DialogueAiAssistStatus> {
  const endpoint = API_ENDPOINTS.jevDialogueAiStatus(requestId);
  const separator = endpoint.includes('?') ? '&' : '?';
  return apiRequestJson<DialogueAiAssistStatus>(
    `${endpoint}${separator}_ts=${Date.now()}`,
    { cache: 'no-store' }
  );
}
