import type { Voice, TtsProvider } from '$lib/types';
import { API_ENDPOINTS, apiRequestJson, toApiClientError } from './apiClient';
import type {
  ManifestData,
  ScanVoiceManifestsRequest,
  ScanVoiceManifestsResponse,
} from './apiContracts';

export type { ManifestData } from './apiContracts';

export async function scanVoiceManifests(audioRoot: string): Promise<ManifestData[]> {
  if (!audioRoot) return [];

  const payload: ScanVoiceManifestsRequest = { audioRoot };
  const data = await apiRequestJson<ScanVoiceManifestsResponse>(API_ENDPOINTS.scanVoiceManifests, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  return data.manifests || [];
}

/**
 * Discovers voices from manifest data provided by the Python backend.
 * Call the backend API to scan manifests and return voice information.
 */
export async function discoverVoicesFromManifests(audioRoot: string): Promise<Voice[]> {
  if (!audioRoot) return [];

  try {
    // Call Python backend to scan manifests
    console.log(`[voices] Requesting manifest scan from backend for:`, audioRoot);
    const manifestsData = await scanVoiceManifests(audioRoot);

    console.log(`[voices] Received ${manifestsData.length} manifest entries from backend`);

    const voiceMap = new Map<string, Voice>();

    // Process each manifest
    for (const manifestData of manifestsData) {
      try {
        // Extract voice information from v2.0 format
        if (manifestData.formatVersion === '2.0') {
          const { characterName, metadata, clips } = manifestData;
          const primaryVoiceId = metadata?.primaryVoiceId;
          const voiceIds = metadata?.voiceIds || {};
          const sources = metadata?.sources || {};

          // Process each unique voice in this manifest
          for (const [voiceId, clipCount] of Object.entries(voiceIds) as [string, number][]) {
            const provider = determineProvider(sources, clips, voiceId);
            const voiceKey = `${provider}-${voiceId}`;

            if (!voiceMap.has(voiceKey)) {
              voiceMap.set(voiceKey, {
                id: voiceKey,
                displayName: voiceId === primaryVoiceId
                  ? `${characterName} (Primary)`
                  : `${characterName} Voice`,
                provider: provider,
                providerVoiceId: voiceId,
                previewUrl: null,
                notes: `Discovered from ${characterName} manifest`,
                metadata: {
                  totalClips: clipCount,
                  usedByCharacters: [manifestData.characterId || characterName],
                  discoveredFrom: 'manifest',
                },
              });
            } else {
              // Update existing voice with additional character usage
              const existing = voiceMap.get(voiceKey)!;
              existing.metadata.totalClips = (existing.metadata.totalClips || 0) + clipCount;
              const usedBy = existing.metadata.usedByCharacters || [];
              if (!usedBy.includes(manifestData.characterId || characterName)) {
                existing.metadata.usedByCharacters = [...usedBy, manifestData.characterId || characterName];
              }
            }
          }
        }
      } catch (err) {
        console.warn(`[voices] Error processing manifest for ${manifestData.characterName}:`, err);
      }
    }

    return Array.from(voiceMap.values()).sort((a, b) =>
      (b.metadata.totalClips || 0) - (a.metadata.totalClips || 0)
    );
  } catch (error) {
    const apiError = toApiClientError(error);
    // Check if this is a connection error (backend not running)
    const isConnectionError = apiError.type === 'network';

    if (isConnectionError) {
      console.warn('[voices] Backend server not available. Voice discovery requires the Python backend to be running on localhost:8000');
      console.warn('[voices] You can still manually add voices using the "Add Voice" button');
    } else {
      console.error(`[voices] Error discovering voices (${apiError.type}):`, apiError.message);
    }
    // Return empty array instead of throwing - allows graceful fallback
    return [];
  }
}

function determineProvider(
  sources: Record<string, number>,
  clips: Array<{ voiceId?: string; voice_id?: string; provider?: string }> = [],
  voiceId: string
): TtsProvider {
  // Check clips array/provider hint first
  const clip = clips.find((entry) => entry.voiceId === voiceId || entry.voice_id === voiceId);
  if (clip?.provider) {
    const provider = clip.provider.toLowerCase();
    if (provider === 'vibevoice_local' || provider === 'vibevoice') return 'vibevoice_local';
  }

  // Fall back to source metadata hints
  const sourceKeys = Object.keys(sources);
  if (sourceKeys.includes('VibeVoice')) {
    return 'vibevoice_local';
  }

  return 'vibevoice_local';
}

/**
 * Generates a unique voice ID
 */
export function generateVoiceId(provider: TtsProvider, providerVoiceId: string): string {
  return `${provider}-${providerVoiceId}`;
}

