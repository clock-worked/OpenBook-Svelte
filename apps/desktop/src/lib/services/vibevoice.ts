import type { Voice } from '$lib/types';
import { API_ENDPOINTS, apiPostJson, toApiUrl } from './apiClient';
import type {
  ListVoiceSamplesRequest,
  ListVoiceSamplesResponse,
  SaveVoiceSampleMetadataRequest,
  SaveVoiceSampleMetadataResponse,
  VoiceSampleEntry,
} from './apiContracts';
import { generateVoiceId } from './voices';

export function normalizeVoiceTags(tags: string[]): string[] {
  const normalized: string[] = [];
  for (const tag of tags) {
    const cleaned = tag.trim().toLowerCase();
    if (!cleaned || normalized.includes(cleaned)) continue;
    normalized.push(cleaned);
  }
  return normalized;
}

export async function listVoiceSamples(samplesRoot: string): Promise<VoiceSampleEntry[]> {
  const payload: ListVoiceSamplesRequest = { samples_root: samplesRoot };
  const data = await apiPostJson<ListVoiceSamplesResponse, ListVoiceSamplesRequest>(
    API_ENDPOINTS.listVoiceSamples,
    payload,
  );

  return data.samples || [];
}

export async function saveVoiceSampleMetadata(
  samplesRoot: string,
  sampleFile: string,
  displayName: string,
  tags: string[],
): Promise<VoiceSampleEntry> {
  const payload: SaveVoiceSampleMetadataRequest = {
    samples_root: samplesRoot,
    sample_file: sampleFile,
    display_name: displayName.trim(),
    tags: normalizeVoiceTags(tags),
  };
  const data = await apiPostJson<SaveVoiceSampleMetadataResponse, SaveVoiceSampleMetadataRequest>(
    API_ENDPOINTS.saveVoiceSampleMetadata,
    payload,
  );

  return data.sample || {
    sample_file: sampleFile,
    display_name: displayName.trim(),
    tags: normalizeVoiceTags(tags),
  };
}

function fallbackDisplayName(sampleFile: string): string {
  return sampleFile.replace(/\.[^.]+$/, '') || sampleFile;
}

export function getVoiceSamplePreviewUrl(samplesRoot: string, sampleFile: string): string {
  return toApiUrl(API_ENDPOINTS.voiceSamplePreview(sampleFile, samplesRoot));
}

export function createVoiceFromSample(
  samplesRoot: string,
  sample: VoiceSampleEntry,
  existing?: Voice,
): Voice {
  return {
    id: existing?.id || generateVoiceId('vibevoice_local', sample.sample_file),
    displayName: sample.display_name?.trim() || existing?.displayName || fallbackDisplayName(sample.sample_file),
    provider: 'vibevoice_local',
    providerVoiceId: sample.sample_file,
    previewUrl: getVoiceSamplePreviewUrl(samplesRoot, sample.sample_file),
    notes: existing?.notes || '',
    metadata: {
      ...existing?.metadata,
      tags: normalizeVoiceTags(sample.tags || []),
      imageUrl: existing?.metadata.imageUrl || null,
      discoveredFrom: 'sample',
    },
  };
}
