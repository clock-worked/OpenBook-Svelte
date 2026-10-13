import { API_ENDPOINTS } from '$lib/services/apiClient';
import type {
  DialogueAiAssistStatus,
  LocalDialogueAiResponse,
} from '$lib/services/apiContracts';
import { createProviderClient } from '$lib/services/providerClient';
import {
  buildLocalDialogueAiRequest,
  type LocalDialogueAiRunArgs,
} from '$lib/services/dialogueAiPayload';

const client = createProviderClient<
  LocalDialogueAiRunArgs,
  LocalDialogueAiResponse,
  DialogueAiAssistStatus
>({
  endpoint: API_ENDPOINTS.localDialogueAi,
  statusEndpoint: (requestId) => API_ENDPOINTS.localDialogueAiStatus(requestId),
  idPrefix: 'local-dialogue-ai',
  buildPayload: buildLocalDialogueAiRequest,
});

export function createLocalDialogueAiRequestId(): string {
  return client.createRequestId();
}

export async function runLocalDialogueAi(
  args: LocalDialogueAiRunArgs
): Promise<LocalDialogueAiResponse> {
  return client.run(args);
}

export async function getLocalDialogueAiStatus(
  requestId: string
): Promise<DialogueAiAssistStatus> {
  return client.getStatus(requestId);
}
