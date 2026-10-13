import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  apiPostJson: vi.fn(),
  apiRequestJson: vi.fn(),
}));

import { apiPostJson, apiRequestJson } from '../apiClient';
import { createProviderClient } from '../providerClient';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function makeAssistClient() {
  return createProviderClient<
    { name: string },
    { name: string },
    { ok: boolean },
    { status: string }
  >({
    endpoint: '/api/dialogue-ai-assist',
    statusEndpoint: (id) => `/api/dialogue-ai-assist-status/${encodeURIComponent(id)}`,
    idPrefix: 'dai',
    buildPayload: (args) => ({ name: args.name }),
  });
}

describe('createProviderClient', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.resetAllMocks();
  });

  it('mints a UUID request id when crypto.randomUUID is available', () => {
    const client = makeAssistClient();

    expect(client.createRequestId()).toMatch(UUID_RE);
  });

  it('falls back to a prefixed id when crypto.randomUUID is unavailable', () => {
    vi.stubGlobal('crypto', undefined);
    const client = makeAssistClient();

    const id = client.createRequestId();

    expect(id).toMatch(/^dai-\d+-[a-z0-9]{8}$/);
  });

  it('posts the built payload to the configured endpoint', async () => {
    vi.mocked(apiPostJson).mockResolvedValueOnce({ ok: true });
    const client = makeAssistClient();

    const response = await client.run({ name: 'Alice' });

    expect(apiPostJson).toHaveBeenCalledTimes(1);
    expect(apiPostJson).toHaveBeenCalledWith('/api/dialogue-ai-assist', { name: 'Alice' });
    expect(response).toEqual({ ok: true });
  });

  it('polls the status endpoint with a no-store cache and a _ts bust param', async () => {
    vi.mocked(apiRequestJson).mockResolvedValueOnce({ status: 'running' });
    const client = makeAssistClient();

    const response = await client.getStatus('req-1');

    expect(apiRequestJson).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(apiRequestJson).mock.calls[0];
    expect(url).toMatch(/^\/api\/dialogue-ai-assist-status\/req-1\?_ts=\d+$/);
    expect(init).toEqual({ cache: 'no-store' });
    expect(response).toEqual({ status: 'running' });
  });

  it('uses & as the separator when the status endpoint already has a query', async () => {
    vi.mocked(apiRequestJson).mockResolvedValueOnce({ status: 'done' });
    const client = createProviderClient<
      { a: number },
      { a: number },
      { status: string },
      { status: string }
    >({
      endpoint: '/api/status-run',
      statusEndpoint: (id) => `/api/status/${id}?run=1`,
      idPrefix: 'st',
      buildPayload: (args) => ({ a: args.a }),
    });

    await client.getStatus('req-2');

    const [url] = vi.mocked(apiRequestJson).mock.calls[0];
    expect(url).toMatch(/^\/api\/status\/req-2\?run=1&_ts=\d+$/);
  });
});
