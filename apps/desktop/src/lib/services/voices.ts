import type { Voice, TtsProvider } from '$lib/types';

/**
 * Discovers voices from manifest data provided by the Python backend.
 * Call the backend API to scan manifests and return voice information.
 */
export async function discoverVoicesFromManifests(audioRoot: string): Promise<Voice[]> {
  if (!audioRoot) return [];

  try {
    // Call Python backend to scan manifests
    console.log(`[voices] Requesting manifest scan from backend for:`, audioRoot);
    
    const response = await fetch('http://127.0.0.1:8010/api/scan-voice-manifests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audioRoot }),
    });

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();
    const manifestsData: ManifestData[] = data.manifests || [];
    
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
    // Check if this is a connection error (backend not running)
    const isConnectionError = error instanceof TypeError && 
      (error.message.includes('Failed to fetch') || 
       error.message.includes('ERR_CONNECTION_REFUSED') ||
       error.message.includes('NetworkError'));
    
    if (isConnectionError) {
      console.warn('[voices] Backend server not available. Voice discovery requires the Python backend to be running on localhost:8000');
      console.warn('[voices] You can still manually add voices using the "Add Voice" button');
    } else {
      console.error('[voices] Error discovering voices:', error);
    }
    // Return empty array instead of throwing - allows graceful fallback
    return [];
  }
}

interface ManifestData {
  formatVersion: string;
  characterId?: string;
  characterName: string;
  metadata?: {
    primaryVoiceId?: string;
    voiceIds?: Record<string, number>;
    sources?: Record<string, number>;
  };
  clips?: Array<{
    voiceId?: string;
    voice_id?: string;
    provider?: string;
  }>;
}

function determineProvider(sources: Record<string, number>, clips: any[], voiceId: string): TtsProvider {
  // Check sources metadata first
  const sourceKeys = Object.keys(sources);
  if (sourceKeys.includes('ElevenLabs')) return 'elevenlabs';
  if (sourceKeys.includes('Chirp3')) return 'chirp3';
  if (sourceKeys.includes('VibeVoice')) return 'vibevoice_local';

  // Check clips array
  const clip = clips?.find(c => c.voiceId === voiceId || c.voice_id === voiceId);
  if (clip?.provider) {
    const provider = clip.provider.toLowerCase();
    // Only return valid providers
    if (provider === 'elevenlabs' || provider === 'chirp3' || provider === 'vibevoice_local') {
      return provider as TtsProvider;
    }
  }

  // Default to elevenlabs if voice ID looks like an ElevenLabs ID
  if (voiceId && voiceId.length > 15) {
    return 'elevenlabs';
  }

  return 'elevenlabs';
}


/**
 * Fetches voice metadata from ElevenLabs API
 */
export async function enrichVoiceWithElevenLabsData(
  voice: Voice,
  apiKey: string
): Promise<Voice> {
  if (voice.provider !== 'elevenlabs') return voice;

  try {
    const response = await fetch(
      `https://api.elevenlabs.io/v1/voices/${voice.providerVoiceId}`,
      {
        headers: { 'xi-api-key': apiKey },
      }
    );

    if (!response.ok) {
      throw new Error(`ElevenLabs API error: ${response.status}`);
    }

    const data = await response.json();
    
    return {
      ...voice,
      displayName: data.name || voice.displayName,
      previewUrl: data.preview_url || voice.previewUrl,
      metadata: {
        ...voice.metadata,
        gender: inferGender(data.labels?.gender),
        accent: data.labels?.accent,
        tags: data.labels ? Object.keys(data.labels) : voice.metadata.tags,
        discoveredFrom: 'elevenlabs',
      },
    };
  } catch (error) {
    console.error('[voices] Error enriching voice with ElevenLabs data:', error);
    return voice;
  }
}

function inferGender(label?: string): 'M' | 'F' | 'U' {
  if (!label) return 'U';
  const lower = label.toLowerCase();
  if (lower.includes('male') && !lower.includes('female')) return 'M';
  if (lower.includes('female')) return 'F';
  return 'U';
}

/**
 * Generates a unique voice ID
 */
export function generateVoiceId(provider: TtsProvider, providerVoiceId: string): string {
  return `${provider}-${providerVoiceId}`;
}

