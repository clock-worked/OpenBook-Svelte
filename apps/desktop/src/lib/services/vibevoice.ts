import { API_ENDPOINTS, apiRequestJson } from './apiClient';

export async function listVoiceSamples(samplesRoot: string): Promise<string[]> {
  const data = await apiRequestJson<{ samples?: string[] }>(API_ENDPOINTS.listVoiceSamples, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ samples_root: samplesRoot }),
  });

  return data.samples || [];
}
