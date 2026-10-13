import { API_ENDPOINTS } from '$lib/services/apiClient';
import type {
  DialogueAiAssistStatus,
  LocalDialogueAiResponse,
} from '$lib/services/apiContracts';
import { createProviderClient } from '$lib/services/providerClient';
import {
  buildJevDialogueAiRequest,
  type JevDialogueAiRunArgs,
} from '$lib/services/dialogueAiPayload';

// JEV runs with fixed backend parameters (no server configuration):
// 1500-char context window, scene roster capped at 51 characters + "None".
export { JEV_FIXED_CONTEXT_WINDOW } from '$lib/services/dialogueAiPayload';

const client = createProviderClient<
  JevDialogueAiRunArgs,
  LocalDialogueAiResponse,
  DialogueAiAssistStatus
>({
  endpoint: API_ENDPOINTS.jevDialogueAi,
  statusEndpoint: (requestId) => API_ENDPOINTS.jevDialogueAiStatus(requestId),
  idPrefix: 'jev-dialogue-ai',
  buildPayload: buildJevDialogueAiRequest,
});

export function createJevDialogueAiRequestId(): string {
  return client.createRequestId();
}

export async function runJevDialogueAi(
  args: JevDialogueAiRunArgs
): Promise<LocalDialogueAiResponse> {
  return client.run(args);
}

export async function getJevDialogueAiStatus(
  requestId: string
): Promise<DialogueAiAssistStatus> {
  return client.getStatus(requestId);
}
