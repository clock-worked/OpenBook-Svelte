import type { CharacterVoiceMeta } from '$lib/types';

const ELEVENLABS_API_BASE = 'https://api.elevenlabs.io/v1';

export interface ElevenLabsVoiceResponse {
  voice_id: string;
  name: string;
  preview_url?: string | null;
  samples?: Array<{
    sample_id: string;
    file_name: string;
    preview_url?: string;
    url?: string;
    audio_url?: string;
  }>;
}

function resolvePreviewUrl(data: ElevenLabsVoiceResponse): string | null {
  if (data.preview_url) return data.preview_url;
  const sample = data.samples?.find((s) => !!(s.preview_url || s.audio_url || s.url));
  return sample?.preview_url ?? sample?.audio_url ?? sample?.url ?? null;
}

export async function fetchElevenLabsVoiceMeta(voiceId: string, apiKey: string): Promise<CharacterVoiceMeta> {
  if (!voiceId?.trim()) {
    throw new Error('Voice ID is required for ElevenLabs lookup');
  }
  if (!apiKey?.trim()) {
    throw new Error('API key is required for ElevenLabs lookup');
  }

  const url = `${ELEVENLABS_API_BASE}/voices/${encodeURIComponent(voiceId.trim())}`;
  const res = await fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      'xi-api-key': apiKey,
    },
  });

  if (!res.ok) {
    const message = await res.text().catch(() => res.statusText);
    throw new Error(`ElevenLabs lookup failed (${res.status}): ${message}`);
  }

  const data = (await res.json()) as ElevenLabsVoiceResponse;
  const previewUrl = resolvePreviewUrl(data);

  const meta: CharacterVoiceMeta = {
    name: data.name,
    previewUrl,
    fetchedAt: new Date().toISOString(),
    provider: 'elevenlabs',
  };

  return meta;
}

export async function fetchElevenLabsVoices(apiKey: string): Promise<ElevenLabsVoiceResponse[]> {
    if (!apiKey?.trim()) {
        throw new Error('API key is required to fetch ElevenLabs voices');
    }
    const url = `${ELEVENLABS_API_BASE}/voices`;
    const res = await fetch(url, {
        method: 'GET',
        headers: {
            Accept: 'application/json',
            'xi-api-key': apiKey,
        },
    });
    if (!res.ok) {
        const message = await res.text().catch(() => res.statusText);
        throw new Error(`ElevenLabs voices fetch failed (${res.status}): ${message}`);
    }
    const data = await res.json();
    return data.voices || [];
}

export async function synthesizeElevenLabsAudio(
    text: string,
    voiceId: string,
    apiKey: string
): Promise<ArrayBuffer | null> {
    if (!apiKey?.trim() || !voiceId?.trim() || !text?.trim()) {
        return null;
    }
    const url = `${ELEVENLABS_API_BASE}/text-to-speech/${encodeURIComponent(voiceId.trim())}`;
    const res = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Accept: 'audio/mpeg',
            'xi-api-key': apiKey,
        },
        body: JSON.stringify({
            text: text,
            model_id: 'eleven_monolingual_v1',
        }),
    });
    if (!res.ok) {
        console.error('ElevenLabs synthesis failed', await res.text());
        return null;
    }
    return await res.arrayBuffer();
}


