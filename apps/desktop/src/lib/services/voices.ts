import type { Voice, TtsProvider } from '$lib/types';
import { API_ENDPOINTS, apiPostJson, toApiClientError } from './apiClient';
import type {
  ManifestData,
  ScanVoiceManifestsRequest,
  ScanVoiceManifestsResponse,
} from './apiContracts';

export type { ManifestData } from './apiContracts';

export async function scanVoiceManifests(audioRoot: string): Promise<ManifestData[]> {
  if (!audioRoot) return [];

  const payload: ScanVoiceManifestsRequest = { audioRoot };
  const data = await apiPostJson<ScanVoiceManifestsResponse, ScanVoiceManifestsRequest>(
    API_ENDPOINTS.scanVoiceManifests,
    payload,
  );

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
          const { characterName, metadata } = manifestData;
          const primaryVoiceId = metadata?.primaryVoiceId;
          const voiceIds = metadata?.voiceIds || {};

          // Process each unique voice in this manifest. Every manifest voice
          // is VibeVoice-local: the former determineProvider hint-walking
          // returned 'vibevoice_local' on every path, so it collapsed to this
          // constant.
          for (const [voiceId, clipCount] of Object.entries(voiceIds) as [string, number][]) {
            const provider: TtsProvider = 'vibevoice_local';
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

/**
 * Generates a unique voice ID
 */
export function generateVoiceId(provider: TtsProvider, providerVoiceId: string): string {
  return `${provider}-${providerVoiceId}`;
}

