import { API_ENDPOINTS, apiPostJson, toApiUrl } from './apiClient';
import type { ListVoiceSamplesRequest, ListVoiceSamplesResponse } from './apiContracts';

export async function listVoiceSamples(samplesRoot: string): Promise<string[]> {
  const payload: ListVoiceSamplesRequest = { samples_root: samplesRoot };
  const data = await apiPostJson<ListVoiceSamplesResponse, ListVoiceSamplesRequest>(
    API_ENDPOINTS.listVoiceSamples,
    payload,
  );

  return data.samples || [];
}

export function getVoiceSamplePreviewUrl(samplesRoot: string, sampleFile: string): string {
  return toApiUrl(API_ENDPOINTS.voiceSamplePreview(sampleFile, samplesRoot));
}
