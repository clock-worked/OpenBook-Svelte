import { apiPostJson, apiRequestJson } from '$lib/services/apiClient';

/**
 * Shared HTTP client shape for the speaker-attribution provider endpoints
 * (Gemini assist, JEV, local LM Studio). Each provider differs only in its
 * endpoints, request-id prefix, and request payload — everything else
 * (request id minting, POST, no-store status polling) is identical.
 */
export interface ProviderClientOptions<
  TRunArgs,
  TRequest,
  TResponse,
  TStatus
> {
  /** POST endpoint used to start a run. */
  endpoint: string;
  /** Resolves the status endpoint for a given request id. */
  statusEndpoint: (requestId: string) => string;
  /** Prefix for fallback request ids when `crypto.randomUUID` is unavailable. */
  idPrefix: string;
  /** Builds the request payload from run arguments. */
  buildPayload: (args: TRunArgs) => TRequest;
}

export interface ProviderClient<TRunArgs, TResponse, TStatus> {
  createRequestId: () => string;
  run: (args: TRunArgs) => Promise<TResponse>;
  getStatus: (requestId: string) => Promise<TStatus>;
}

export function createProviderClient<
  TRunArgs,
  TRequest,
  TResponse,
  TStatus
>(
  options: ProviderClientOptions<TRunArgs, TRequest, TResponse, TStatus>
): ProviderClient<TRunArgs, TResponse, TStatus> {
  const { endpoint, statusEndpoint, idPrefix, buildPayload } = options;

  return {
    createRequestId() {
      if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
      }
      return `${idPrefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    },
    run(args: TRunArgs) {
      return apiPostJson<TResponse, TRequest>(endpoint, buildPayload(args));
    },
    getStatus(requestId: string) {
      const base = statusEndpoint(requestId);
      const separator = base.includes('?') ? '&' : '?';
      return apiRequestJson<TStatus>(
        `${base}${separator}_ts=${Date.now()}`,
        { cache: 'no-store' }
      );
    },
  };
}
