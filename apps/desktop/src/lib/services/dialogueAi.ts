import { API_ENDPOINTS, apiPostJson, apiRequestJson } from '$lib/services/apiClient';
import type { DialogueJson } from '$lib/types';
import type {
  DialogueAiAssistRequest,
  DialogueAiAssistResponse,
  DialogueAiAssistStatus,
} from '$lib/services/apiContracts';

export function createDialogueAiAssistRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }

  return `dialogue-ai-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function runDialogueAiAssist(args: {
  requestId?: string;
  chapterPath: string;
  chapterText: string;
  dialogue: DialogueJson;
  autoApplyThreshold?: number;
  maxLines?: number | null;
  dryRun?: boolean;
}): Promise<DialogueAiAssistResponse> {
  const payload: DialogueAiAssistRequest = {
    request_id: args.requestId,
    chapter_path: args.chapterPath,
    chapter_text: args.chapterText,
    dialogue: args.dialogue,
    auto_apply_threshold: args.autoApplyThreshold,
    max_lines: args.maxLines,
    dry_run: args.dryRun,
  };

  return apiPostJson<DialogueAiAssistResponse, DialogueAiAssistRequest>(API_ENDPOINTS.dialogueAiAssist, payload);
}

export async function getDialogueAiAssistStatus(
  requestId: string
): Promise<DialogueAiAssistStatus> {
  const separator = API_ENDPOINTS.dialogueAiAssistStatus(requestId).includes('?') ? '&' : '?';
  return apiRequestJson<DialogueAiAssistStatus>(
    `${API_ENDPOINTS.dialogueAiAssistStatus(requestId)}${separator}_ts=${Date.now()}`,
    { cache: 'no-store' }
  );
}