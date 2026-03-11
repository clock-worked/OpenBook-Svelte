import { get } from 'svelte/store';
import { audioRoot, bookRoot, bookRootAbsolutePath, voiceSamplesRoot } from '$lib/stores/bookState';
import { bookRootPathOverride } from '$lib/stores/settings';
import type { DialogueLine, CharactersJson } from '$lib/types';
import { readCentralCharacters, readJsonRelative } from '$lib/services/fs';
import { API_ENDPOINTS, ApiClientError, apiFetch, apiPostJson, apiPostVoid, toApiUrl } from '$lib/services/apiClient';
import type {
  BackendErrorPayload,
  ChapterVoiceAssignmentPayload,
  DeleteAudioLineRequest,
  DeleteCharacterAudioRequest,
  GenerateAudioChapterRequest,
  GenerateAudioChapterResponse,
  GenerateAudioLineRequest,
  GenerateAudioLineSuccessResponse,
  ReadFileAbsoluteRequest,
  ReconcileAudioManifestRequest,
  ReconcileAudioManifestResponse,
} from '$lib/services/apiContracts';

export interface AudioManifest {
  formatVersion: string;
  characterId: string;
  characterName: string;
  metadata: {
    totalClips: number;
    chapters: string[];
    sources: Record<string, number>;
    voiceIds: Record<string, number>;
    primaryVoiceId: string | null;
    lastUpdated: string;
  };
  clips: AudioClip[];
}

export interface AudioClip {
  id: number;
  characterId: string;
  characterName: string;
  text: string;
  audioFile: string;
  chapter: string;
  sourceFile: string;
  voiceId: string;
  provider: string;
}

const manifestCache = new Map<string, AudioManifest | null>();
const manifestRequestCache = new Map<string, Promise<AudioManifest | null>>();
let absoluteReadApiAvailable: boolean | null = null;
let reconcileManifestApiAvailable: boolean | null = null;
let hasWarnedMissingAudioRoot = false;

function getBackendErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiClientError) {
    const details = error.details as BackendErrorPayload | string | undefined;
    if (typeof details === 'string' && details.trim()) {
      return details;
    }
    if (details && typeof details === 'object') {
      const payload = details as BackendErrorPayload;
      const detailMessage = payload.detail || payload.error || payload.message;
      if (detailMessage) return detailMessage;
    }
    return error.message || fallback;
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  return fallback;
}

function getManifestCacheKey(chapterTitle: string, characterName: string): string {
  return `${chapterTitle}::${characterName.toLowerCase()}`;
}

function invalidateManifestCache(chapterTitle: string, characterName: string): void {
  const key = getManifestCacheKey(chapterTitle, characterName);
  manifestCache.delete(key);
  manifestRequestCache.delete(key);
}

export function clearAllManifestCache(): void {
  manifestCache.clear();
  manifestRequestCache.clear();
}

/**
 * Get character display name from character ID by reading characters.json
 * Falls back to the ID if not found (to handle legacy cases)
 */
export async function getCharacterNameFromId(characterId: string): Promise<string> {
  const root = get(bookRoot);
  if (!root) {
    console.warn('[audio] Book root not set, using characterId as-is:', characterId);
    return characterId;
  }

  try {
    const charactersData = await readCentralCharacters(root);
    if (!charactersData || !charactersData.characters) {
      console.warn('[audio] No characters.json found, using characterId as-is:', characterId);
      return characterId;
    }

    // Find character by ID (case-insensitive match on both id and name)
    const charIdLower = characterId.toLowerCase();
    const char = charactersData.characters.find((c: any) =>
      c.id?.toLowerCase() === charIdLower || c.name?.toLowerCase() === charIdLower
    );

    if (char && char.name) {
      return char.name;
    }

    // Fallback: capitalize first letter if not found
    console.warn('[audio] Character not found in characters.json:', characterId, '- using capitalized version');
    return characterId.charAt(0).toUpperCase() + characterId.slice(1);
  } catch (err) {
    console.error('[audio] Error reading characters.json:', err);
    // Fallback: capitalize first letter
    return characterId.charAt(0).toUpperCase() + characterId.slice(1);
  }
}

/**
 * Construct the HTTP URL for an audio file served by the backend
 */
export function getAudioPath(
  chapterTitle: string,
  characterName: string,
  lineId: number,
  bustCache: boolean = false
): string {
  // Use HTTP endpoint to serve audio files instead of file:// paths
  // The backend will handle file system access
  const baseUrl = toApiUrl(API_ENDPOINTS.audioLine(chapterTitle, characterName, lineId));
  const resolvedRoot = get(audioRoot) || get(bookRootAbsolutePath) || get(bookRootPathOverride);

  const params = new URLSearchParams();
  if (resolvedRoot) {
    params.set('root', resolvedRoot);
  }

  // Add cache-busting parameter to force fresh audio
  if (bustCache) {
    params.set('t', String(Date.now()));
  }

  const query = params.toString();
  return query ? `${baseUrl}?${query}` : baseUrl;
}

/**
 * Construct the file system path for a character's manifest
 */
export function getManifestPath(
  chapterTitle: string,
  characterName: string
): string {
  const root = get(audioRoot) || get(bookRootAbsolutePath);
  if (!root) {
    return '';
  }
  return `${root}/${chapterTitle}/audio_lines/${characterName}/manifest.json`;
}

/**
 * Read a character's audio manifest
 * Note: This tries to read from filesystem using absolute path
 */
export async function readManifest(
  chapterTitle: string,
  characterName: string
): Promise<AudioManifest | null> {
  const cacheKey = getManifestCacheKey(chapterTitle, characterName);
  if (manifestCache.has(cacheKey)) {
    return manifestCache.get(cacheKey) ?? null;
  }

  const inFlightRequest = manifestRequestCache.get(cacheKey);
  if (inFlightRequest) {
    return inFlightRequest;
  }

  const request = (async (): Promise<AudioManifest | null> => {
    try {
      const relativePath = `${chapterTitle}/audio_lines/${characterName}/manifest.json`;
      const root = get(audioRoot) || get(bookRootAbsolutePath);
      if (!root) {
        const relativeManifest = await readJsonRelative<AudioManifest>(relativePath);
        if (relativeManifest) {
          return relativeManifest;
        }
        if (!hasWarnedMissingAudioRoot) {
          console.warn('[audio] Audio root not set; skipping manifest reads');
          hasWarnedMissingAudioRoot = true;
        }
        return null;
      }

      if (absoluteReadApiAvailable === false) {
        return await readJsonRelative<AudioManifest>(relativePath);
      }

      if (reconcileManifestApiAvailable !== false) {
        try {
          const reconcilePayload: ReconcileAudioManifestRequest = {
            chapter_title: chapterTitle,
            character_name: characterName,
            audio_root: root || null,
          };
          const reconcileData = await apiPostJson<
            ReconcileAudioManifestResponse,
            ReconcileAudioManifestRequest
          >(API_ENDPOINTS.reconcileAudioManifest, reconcilePayload);

          reconcileManifestApiAvailable = true;

          if (reconcileData?.manifest && typeof reconcileData.manifest === 'object') {
            return reconcileData.manifest as AudioManifest;
          }
        } catch (reconcileError) {
          if (reconcileError instanceof ApiClientError && reconcileError.status === 404) {
            reconcileManifestApiAvailable = false;
            console.warn('[audio] /api/reconcile-audio-manifest returned 404; disabling manifest reconciliation for this session');
          } else {
            console.warn('[audio] Manifest reconciliation failed; falling back to manifest read:', reconcileError);
          }
        }
      }

      // Get the full absolute path
      const manifestPath = `${root}/${chapterTitle}/audio_lines/${characterName}/manifest.json`;
      console.log('[audio] Reading manifest from:', manifestPath);

      // Try to read the file using Node fs (via backend) with absolute path
      const response = await apiFetch(API_ENDPOINTS.readFileAbsolute, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: manifestPath }),
      });

      if (response.ok) {
        absoluteReadApiAvailable = true;
        const data = await response.json() as AudioManifest;
        console.log('[audio] Manifest loaded for', characterName, '- clips:', data.clips?.length);
        return data;
      } else if (response.status === 404) {
        absoluteReadApiAvailable = false;
        console.warn('[audio] /api/read_file_absolute returned 404; disabling absolute manifest reads for this session');
        return await readJsonRelative<AudioManifest>(relativePath);
      } else {
        console.log('[audio] Manifest not found for', characterName, '- status:', response.status);
      }
      return null;
    } catch (error) {
      console.error('[audio] Error reading manifest:', error);
      return null;
    }
  })();

  manifestRequestCache.set(cacheKey, request);
  const result = await request;
  manifestRequestCache.delete(cacheKey);
  manifestCache.set(cacheKey, result);
  return result;
}

/**
 * Check if audio exists for a specific line
 */
export async function checkAudioExistsForLine(
  chapterTitle: string,
  characterName: string,
  lineId: number
): Promise<boolean> {
  const manifest = await readManifest(chapterTitle, characterName);
  if (!manifest) return false;

  return manifest.clips.some(clip =>
    clip.id === lineId && clip.chapter === chapterTitle
  );
}

/**
 * Check if any audio exists for a character in this chapter
 */
export async function checkAudioExistsForCharacter(
  chapterTitle: string,
  characterName: string
): Promise<{ exists: boolean; clipCount: number }> {
  const manifest = await readManifest(chapterTitle, characterName);
  if (!manifest) return { exists: false, clipCount: 0 };

  const chapterClips = manifest.clips.filter(clip => clip.chapter === chapterTitle);
  return {
    exists: chapterClips.length > 0,
    clipCount: chapterClips.length,
  };
}

export async function reconcileAudioManifestForCharacter(
  chapterTitle: string,
  characterName: string,
): Promise<{ success: boolean; removedCount: number; clipCount: number }> {
  try {
    const resolvedAudioRoot = get(audioRoot) || get(bookRootAbsolutePath) || get(bookRootPathOverride);
    const payload: ReconcileAudioManifestRequest = {
      chapter_title: chapterTitle,
      character_name: characterName,
      audio_root: resolvedAudioRoot || null,
    };

    const data = await apiPostJson<ReconcileAudioManifestResponse, ReconcileAudioManifestRequest>(
      API_ENDPOINTS.reconcileAudioManifest,
      payload,
    );

    const key = getManifestCacheKey(chapterTitle, characterName);
    manifestRequestCache.delete(key);

    if (data?.manifest && typeof data.manifest === 'object') {
      manifestCache.set(key, data.manifest as AudioManifest);
    } else {
      manifestCache.delete(key);
    }

    return {
      success: true,
      removedCount: Number(data?.removed_count || 0),
      clipCount: Number(data?.clip_count || 0),
    };
  } catch (error) {
    console.warn('[audio] Failed to reconcile manifest for character:', characterName, error);
    invalidateManifestCache(chapterTitle, characterName);
    return {
      success: false,
      removedCount: 0,
      clipCount: 0,
    };
  }
}

/**
 * Generate audio for a single dialogue line
 */
export async function generateAudioForLine(
  lineId: number,
  text: string,
  characterName: string,
  characterId: string,
  voiceId: string,
  provider: string,
  chapterTitle: string,
  sourceFile: string
): Promise<{ success: boolean; audioPath?: string; error?: string }> {
  try {
    const samplesRoot = get(voiceSamplesRoot);
    const isVibeVoice = provider.toLowerCase().includes('vibevoice');
    const includeSamplesRoot = isVibeVoice && samplesRoot;
    const resolvedAudioRoot = get(audioRoot) || get(bookRootAbsolutePath) || get(bookRootPathOverride);
    const endpoint = isVibeVoice
      ? API_ENDPOINTS.generateVibeVoiceLine
      : API_ENDPOINTS.generateAudioLine;

    const payload: GenerateAudioLineRequest = {
      line_id: lineId,
      text,
      character_name: characterName,
      character_id: characterId,
      voice_id: voiceId,
      provider,
      chapter_title: chapterTitle,
      source_file: sourceFile,
      voice_sample_root: includeSamplesRoot ? samplesRoot : null,
      audio_root: resolvedAudioRoot || null,
    };

    const data = await apiPostJson<GenerateAudioLineSuccessResponse, GenerateAudioLineRequest>(endpoint, payload);

    invalidateManifestCache(chapterTitle, characterName);
    return {
      success: true,
      audioPath: data.audio_path,
    };
  } catch (error) {
    console.error('Error generating audio for line:', error);
    return {
      success: false,
      error: `Failed to generate audio: ${getBackendErrorMessage(error, 'Unknown error')}`,
    };
  }
}

/**
 * Generate audio for all lines of a character in a chapter
 */
export async function generateAudioForCharacter(
  characterName: string,
  characterId: string,
  lines: DialogueLine[],
  voiceId: string,
  provider: string,
  chapterTitle: string,
  sourceFile: string,
  onProgress?: (current: number, total: number) => void,
  shouldCancel?: () => boolean
): Promise<{ success: boolean; generatedCount: number; errors: string[]; canceled: boolean }> {
  const errors: string[] = [];
  let generatedCount = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (shouldCancel?.()) {
      return {
        success: false,
        generatedCount,
        errors,
        canceled: true,
      };
    }

    const result = await generateAudioForLine(
      line.id,
      line.text,
      characterName,
      characterId,
      voiceId,
      provider,
      chapterTitle,
      sourceFile
    );

    if (result.success) {
      generatedCount++;
    } else {
      errors.push(`Line ${line.id}: ${result.error}`);
    }

    if (onProgress) {
      onProgress(i + 1, lines.length);
    }
  }

  return {
    success: errors.length === 0,
    generatedCount,
    errors,
    canceled: false,
  };
}

export async function generateAudioForChapterVibeVoice(
  dialoguePath: string,
  chapterTitle: string,
  sourceFile: string,
  assignments: ChapterVoiceAssignmentPayload[]
): Promise<{ success: boolean; generatedCount: number; skippedCount: number; totalLines: number; errors: string[] }> {
  try {
    const samplesRoot = get(voiceSamplesRoot);
    const resolvedAudioRoot = get(audioRoot) || get(bookRootAbsolutePath) || get(bookRootPathOverride);

    const payload: GenerateAudioChapterRequest = {
      dialogue_path: dialoguePath,
      chapter_title: chapterTitle,
      source_file: sourceFile,
      assignments,
      voice_sample_root: samplesRoot || null,
      audio_root: resolvedAudioRoot || null,
    };

    const data = await apiPostJson<GenerateAudioChapterResponse, GenerateAudioChapterRequest>(
      API_ENDPOINTS.generateVibeVoiceChapter,
      payload,
    );

    return {
      success: Boolean(data.success),
      generatedCount: Number(data.generatedCount || 0),
      skippedCount: Number(data.skippedCount || 0),
      totalLines: Number(data.totalLines || 0),
      errors: Array.isArray(data.errors) ? data.errors : [],
    };
  } catch (error) {
    const errorMessage = getBackendErrorMessage(error, 'Failed to generate chapter audio');
    return {
      success: false,
      generatedCount: 0,
      skippedCount: 0,
      totalLines: 0,
      errors: [errorMessage],
    };
  }
}

/**
 * Get audio file path if it exists, otherwise return null
 */
export async function getAudioPathIfExists(
  chapterTitle: string,
  characterName: string,
  lineId: number
): Promise<string | null> {
  const exists = await checkAudioExistsForLine(chapterTitle, characterName, lineId);
  if (!exists) return null;

  return getAudioPath(chapterTitle, characterName, lineId);
}

/**
 * Delete all audio for a character in a chapter
 */
export async function deleteAudioForCharacter(
  chapterTitle: string,
  characterName: string
): Promise<boolean> {
  try {
    const payload: DeleteCharacterAudioRequest = {
      chapter_title: chapterTitle,
      character_name: characterName,
    };
    await apiPostVoid<DeleteCharacterAudioRequest>(API_ENDPOINTS.deleteCharacterAudio, payload);
    invalidateManifestCache(chapterTitle, characterName);
    return true;
  } catch (error) {
    console.error('Error deleting audio:', error);
    return false;
  }
}

export async function deleteAudioLineForCharacter(
  chapterTitle: string,
  characterName: string,
  lineId: number
): Promise<boolean> {
  try {
    const payload: DeleteAudioLineRequest = {
      chapter_title: chapterTitle,
      character_name: characterName,
      line_id: lineId,
    };
    await apiPostVoid<DeleteAudioLineRequest>(API_ENDPOINTS.deleteAudioLine, payload);
    invalidateManifestCache(chapterTitle, characterName);
    return true;
  } catch (error) {
    console.error('Error deleting audio line:', error);
    return false;
  }
}
