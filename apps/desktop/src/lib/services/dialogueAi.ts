import { API_ENDPOINTS } from '$lib/services/apiClient';
import type {
  DialogueAiAssistResponse,
  DialogueAiAssistStatus,
} from '$lib/services/apiContracts';
import { createProviderClient } from '$lib/services/providerClient';
import {
  buildDialogueAiAssistRequest,
  type DialogueAiAssistRunArgs,
} from '$lib/services/dialogueAiPayload';

const client = createProviderClient<
  DialogueAiAssistRunArgs,
  DialogueAiAssistResponse,
  DialogueAiAssistStatus
>({
  endpoint: API_ENDPOINTS.dialogueAiAssist,
  statusEndpoint: (requestId) => API_ENDPOINTS.dialogueAiAssistStatus(requestId),
  idPrefix: 'dialogue-ai',
  buildPayload: buildDialogueAiAssistRequest,
});

export function createDialogueAiAssistRequestId(): string {
  return client.createRequestId();
}

export async function runDialogueAiAssist(
  args: DialogueAiAssistRunArgs
): Promise<DialogueAiAssistResponse> {
  return client.run(args);
}

export async function getDialogueAiAssistStatus(
  requestId: string
): Promise<DialogueAiAssistStatus> {
  return client.getStatus(requestId);
}
