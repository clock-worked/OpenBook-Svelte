import { get } from 'svelte/store';
import { audioRoot, bookRoot, bookRootAbsolutePath, chapters, voiceSamplesRoot } from '$lib/stores/bookState';
import { bookRootPathOverride } from '$lib/stores/settings';
import type { AudioGeneratorId, CharacterManifestStats, ChatterboxSettingsChoice, DialogueLine, CharactersJson } from '$lib/types';
import { readCentralCharacters, readDialogueForChapter, readOptionalJsonRelative, readTextFile } from '$lib/services/fs';
import { buildLineTextWithSpeechVerbTags, extractNarrationTags } from '$lib/services/audioNarration';
import type { NarrationTagCandidate } from '$lib/services/audioNarration';
import { API_ENDPOINTS, ApiClientError, apiFetch, apiPostJson, apiPostVoid, toApiUrl } from '$lib/services/apiClient';
import type {
  BackendErrorPayload,
  ChapterVoiceAssignmentPayload,
  ChatterboxSettingsPayload,
  DeleteAudioLineRequest,
  DeleteCharacterAudioRequest,
  GenerateAudioCharacterRequest,
  GenerateAudioCharacterResponse,
  GenerateAudioChapterRequest,
  GenerateAudioChapterResponse,
  GenerateAudioLineRequest,
  GenerateAudioLineSuccessResponse,
  ReadFileAbsoluteRequest,
  ReconcileAudioManifestRequest,
  ReconcileAudioManifestResponse,
} from '$lib/services/apiContracts';
import {
  resolvePlannedLineChapterTitle,
  resolvePlannedLineCharacterName,
  resolvePlannedLineSourceFile,
} from '$lib/services/audioQueuePlanning';
import {
  startCharacterPipelineProgressPolling,
  type PipelineProgressRecord,
} from '$lib/services/audioPipelineProgress';
import { classifyProvider, isLocalChunkedProvider } from '$lib/services/audioGenerator';
import { createManifestReader, manifestAbsolutePath, type ManifestTransport } from '$lib/services/manifestReader';

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

export interface GenerateAudioCharacterOptions {
  useFillerForShortBatch?: boolean;
  fillerText?: string | null;
  replaceLineFinalCommasWithPeriods?: boolean;
  replaceNumbersWithWords?: boolean;
  /**
   * Re-include short narration speech-verb tags (e.g. "Caleb muttered,
   * shaking his head") next to each line in the TTS text. Frontend-only —
   * the augmentation happens before the request is built.
   */
  includeSpeechVerbNarration?: boolean;
  /**
   * Raw advanced-overlay generator override, kept on queued jobs so the
   * queue can re-resolve provider + settings per job at run time.
   * Absent/null = use the settings-tab default.
   */
  generatorOverride?: AudioGeneratorId | null;
  chatterboxChoice?: ChatterboxSettingsChoice | null;
  /** Resolved concrete provider for this run (wins over the voice's). */
  providerOverride?: string | null;
  /** Resolved Chatterbox settings for this run. */
  chatterboxSettings?: ChatterboxSettingsPayload | null;
}

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
 * Resolve the effective audio root — the one FE ordering, matching the BE
 * resolver (`resolve_audio_root` in py_services/audio/router_support.py):
 *
 * 1. the explicit audio root,
 * 2. the user-configured book root override,
 * 3. the book absolute path.
 *
 * The override wins over the book root.
 */
export function audioRootFor(): string | null {
  return get(audioRoot) || get(bookRootPathOverride) || get(bookRootAbsolutePath) || null;
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
  const resolvedRoot = audioRootFor();

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
  const root = audioRootFor();
  if (!root) {
    return '';
  }
  return manifestAbsolutePath(root, chapterTitle, characterName);
}

/** HTTP/fs transport for the manifest reader (services/manifestReader.ts). */
const manifestTransport: ManifestTransport = {
  async reconcile({ chapterTitle, characterName, audioRoot: root }) {
    const payload: ReconcileAudioManifestRequest = {
      chapter_title: chapterTitle,
      character_name: characterName,
      audio_root: root || null,
    };
    try {
      const data = await apiPostJson<ReconcileAudioManifestResponse, ReconcileAudioManifestRequest>(
        API_ENDPOINTS.reconcileAudioManifest,
        payload,
      );
      const manifest = data?.manifest && typeof data.manifest === 'object' ? (data.manifest as AudioManifest) : null;
      return { kind: 'ok', manifest };
    } catch (error) {
      if (error instanceof ApiClientError && error.status === 404) return { kind: 'missing' };
      throw error;
    }
  },
  async readAbsolute(filePath) {
    const body: ReadFileAbsoluteRequest = { file_path: filePath };
    const response = await apiFetch(API_ENDPOINTS.readFileAbsolute, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (response.ok) return { kind: 'ok', manifest: (await response.json()) as AudioManifest };
    if (response.status === 404) return { kind: 'missing' };
    return { kind: 'failed', status: response.status };
  },
  readRelative: (relativePath) => readOptionalJsonRelative<AudioManifest>(relativePath),
};

/** The app's one manifest reader: one cache, one fallback chain. */
const manifestReader = createManifestReader({
  transport: manifestTransport,
  resolveAudioRoot: audioRootFor,
});

function invalidateManifestCache(chapterTitle: string, characterName: string): void {
  manifestReader.invalidate(chapterTitle, characterName);
}

export function clearAllManifestCache(): void {
  manifestReader.clear();
}

/**
 * Read a character's audio manifest (cached; see services/manifestReader.ts
 * for the reconcile -> absolute read -> relative read fallback chain).
 */
export function readManifest(
  chapterTitle: string,
  characterName: string
): Promise<AudioManifest | null> {
  return manifestReader.read(chapterTitle, characterName);
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

export interface CharacterManifestSummaryOptions {
  /** Line count the coverage ratio is computed against. */
  totalLines?: number;
  /** Drop the cached manifests first, so every chapter is re-read. */
  force?: boolean;
  /** Chapters to aggregate; defaults to the open book's chapters. */
  chapterTitles?: string[];
}

export interface CharacterManifestSummary {
  stats: CharacterManifestStats;
  clips: AudioClip[];
  /** Chapters that contributed at least one clip. */
  chapterTitles: string[];
}

/**
 * Book-wide manifest stats for one character: every chapter's manifest (read
 * through `readManifest`, so it shares its cache) aggregated into clip count,
 * per-voice counts, the most-used voice and coverage of `totalLines`.
 * `null` when no chapter has a clip for the character.
 */
export async function loadCharacterManifestSummary(
  characterName: string,
  options?: CharacterManifestSummaryOptions,
): Promise<CharacterManifestSummary | null> {
  const titles = (options?.chapterTitles ?? get(chapters).map((chapter) => chapter?.title))
    .map((title) => String(title || '').trim())
    .filter((title) => title.length > 0);

  const clips: AudioClip[] = [];
  const contributing: string[] = [];
  for (const title of titles) {
    if (options?.force) {
      invalidateManifestCache(title, characterName);
    }
    const manifest = await readManifest(title, characterName);
    const chapterClips = Array.isArray(manifest?.clips) ? manifest.clips : [];
    if (!chapterClips.length) continue;
    clips.push(...chapterClips);
    contributing.push(title);
  }

  if (!clips.length) return null;
  return {
    stats: buildCharacterManifestStats(clips, options?.totalLines),
    clips,
    chapterTitles: contributing,
  };
}

function buildCharacterManifestStats(clips: AudioClip[], totalLines: number | undefined): CharacterManifestStats {
  const voiceIds: Record<string, number> = {};
  for (const clip of clips) {
    if (!clip?.voiceId) continue;
    voiceIds[clip.voiceId] = (voiceIds[clip.voiceId] ?? 0) + 1;
  }
  let primaryVoiceId: string | null = null;
  let bestCount = -1;
  for (const [voiceId, count] of Object.entries(voiceIds)) {
    if (count > bestCount) {
      primaryVoiceId = voiceId;
      bestCount = count;
    }
  }
  const total = totalLines ?? 0;
  return {
    clipCount: clips.length,
    totalLines: total,
    voiceIds,
    coverageRatio: total > 0 ? clips.length / total : undefined,
    primaryVoiceId,
    lastUpdated: new Date().toISOString(),
  };
}

export async function reconcileAudioManifestForCharacter(
  chapterTitle: string,
  characterName: string,
): Promise<{ success: boolean; removedCount: number; clipCount: number }> {
  try {
    const resolvedAudioRoot = audioRootFor();
    const payload: ReconcileAudioManifestRequest = {
      chapter_title: chapterTitle,
      character_name: characterName,
      audio_root: resolvedAudioRoot || null,
    };

    const data = await apiPostJson<ReconcileAudioManifestResponse, ReconcileAudioManifestRequest>(
      API_ENDPOINTS.reconcileAudioManifest,
      payload,
    );

    if (data?.manifest && typeof data.manifest === 'object') {
      manifestReader.store(chapterTitle, characterName, data.manifest as AudioManifest);
    } else {
      manifestReader.invalidate(chapterTitle, characterName);
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
  sourceFile: string,
  chatterboxSettings?: ChatterboxSettingsPayload | null
): Promise<{ success: boolean; audioPath?: string; error?: string }> {
  try {
    const classification = classifyProvider(provider);
    if (!classification) {
      return {
        success: false,
        error: `Failed to generate audio: Unsupported TTS provider: ${provider}`,
      };
    }
    const samplesRoot = get(voiceSamplesRoot);
    // Local-model providers (VibeVoice/Chatterbox) share the chunked
    // generation endpoints and the voice-sample root plumbing.
    const isLocalChunked = classification.endpointKind === 'local_chunked';
    const includeSamplesRoot = isLocalChunked && samplesRoot;
    const resolvedAudioRoot = audioRootFor();
    const endpoint = isLocalChunked
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
      chatterbox_settings: chatterboxSettings ?? null,
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
 * Generate audio for a character line batch (chapter-scoped or cross-chapter)
 */
/**
 * When the speech-verb narration option is on, rebuild each line's TTS text
 * with the short speech-verb narration tags adjacent to it re-included.
 * Each affected chapter's dialogue.json + raw source text is read once and
 * cached for the run. Degrades to the original text per line when a
 * chapter file cannot be read.
 */
async function resolveSpeechVerbAugmentedLineTexts(
  lines: DialogueLine[],
  defaultChapterTitle: string,
  defaultSourceFile: string,
): Promise<Map<number, string>> {
  const result = new Map<number, string>();
  const chapterCache = new Map<string, { tags: NarrationTagCandidate[]; rawText: string | null }>();

  const chapterData = async (chapterTitle: string, sourceFile: string) => {
    const cached = chapterCache.get(chapterTitle);
    if (cached) return cached;
    let tags: NarrationTagCandidate[] = [];
    let rawText: string | null = null;
    try {
      const dialogue = await readDialogueForChapter(chapterTitle);
      if (dialogue && 'formatVersion' in dialogue) {
        tags = extractNarrationTags(dialogue.lines);
      }
    } catch (error) {
      console.warn('[audio] speech-verb narration: failed to read dialogue.json', error);
    }
    try {
      rawText = await readTextFile(sourceFile);
    } catch (error) {
      console.warn('[audio] speech-verb narration: failed to read chapter text', error);
      rawText = null;
    }
    const entry = { tags, rawText };
    chapterCache.set(chapterTitle, entry);
    return entry;
  };

  for (const line of lines) {
    const chapterTitle = resolvePlannedLineChapterTitle(line, defaultChapterTitle);
    const sourceFile = resolvePlannedLineSourceFile(line, defaultSourceFile);
    const { tags, rawText } = await chapterData(chapterTitle, sourceFile);
    result.set(Number(line.id), buildLineTextWithSpeechVerbTags(line, tags, rawText));
  }
  return result;
}

export async function generateAudioForCharacter(
  characterName: string,
  characterId: string,
  lines: DialogueLine[],
  voiceId: string,
  provider: string,
  chapterTitle: string,
  sourceFile: string,
  onProgress?: (current: number, total: number) => void,
  shouldCancel?: () => boolean,
  options: GenerateAudioCharacterOptions = {},
  onPipelineProgress?: (record: PipelineProgressRecord) => void
): Promise<{ success: boolean; generatedCount: number; errors: string[]; canceled: boolean }> {
  if (shouldCancel?.()) {
    return {
      success: false,
      generatedCount: 0,
      errors: [],
      canceled: true,
    };
  }

  // A resolved generator override (advanced batch UI / settings tab) wins
  // over the voice's assigned provider for this run.
  const effectiveProvider = options.providerOverride ?? provider;
  // Local-model providers (VibeVoice/Chatterbox) share the chunked
  // character generation pipeline.
  if (!isLocalChunkedProvider(effectiveProvider)) {
    return {
      success: false,
      generatedCount: 0,
      errors: [
        `Unsupported provider '${provider}'. Chunked generation supports local models only (VibeVoice/Chatterbox).`,
      ],
      canceled: false,
    };
  }

  const totalLines = lines.length;
  if (onProgress) {
    onProgress(0, totalLines);
  }

  // While the (long) synchronous generation request is in flight, poll the
  // live pipeline progress so the UI can show which phase the backend is in
  // (speech generation, WhisperX transcription, splitting, ...). The
  // backend finds the record and reports its scope (single chapter vs book
  // batch); nothing here derives it.
  const stopProgressPolling = onPipelineProgress
    ? startCharacterPipelineProgressPolling({
        characterName,
        chapterTitle,
        audioRoot: audioRootFor() || null,
        requestStart: Date.now(),
        onProgress: onPipelineProgress,
      })
    : () => {};

  try {
    const samplesRoot = get(voiceSamplesRoot);
    const resolvedAudioRoot = audioRootFor();

    // Advanced-overlay option: re-include short speech-verb narration tags
    // (e.g. "Caleb muttered, shaking his head") in each line's TTS text.
    const speechVerbLineTexts = options.includeSpeechVerbNarration
      ? await resolveSpeechVerbAugmentedLineTexts(lines, chapterTitle, sourceFile)
      : null;

    const payload: GenerateAudioCharacterRequest = {
      character_name: characterName,
      character_id: characterId,
      lines: lines.map((line) => ({
        id: Number(line.id),
        text: speechVerbLineTexts?.get(Number(line.id)) ?? String((line as any)?.text ?? ''),
        characterId: (line as any)?.characterId ? String((line as any).characterId) : undefined,
        characterName: resolvePlannedLineCharacterName(line) || undefined,
        chosenSpeaker: (line as any)?.chosenSpeaker ? String((line as any).chosenSpeaker) : undefined,
        chapterTitle: resolvePlannedLineChapterTitle(line, chapterTitle),
        sourceFile: resolvePlannedLineSourceFile(line, sourceFile),
      })),
      voice_id: voiceId,
      provider: effectiveProvider,
      chapter_title: chapterTitle,
      source_file: sourceFile,
      use_filler_for_short_batch: Boolean(options.useFillerForShortBatch),
      filler_text: Boolean(options.useFillerForShortBatch)
        ? (String(options.fillerText ?? '').trim() || null)
        : null,
      replace_line_final_commas_with_periods: options.replaceLineFinalCommasWithPeriods !== false,
      replace_numbers_with_words: Boolean(options.replaceNumbersWithWords),
      voice_sample_root: samplesRoot || null,
      audio_root: resolvedAudioRoot || null,
      chatterbox_settings: options.chatterboxSettings ?? null,
    };

    const data = await apiPostJson<GenerateAudioCharacterResponse, GenerateAudioCharacterRequest>(
      API_ENDPOINTS.generateVibeVoiceCharacter,
      payload,
    );

    const pipelineMode = String(data.pipelineMode || '').trim();
    const generationMode = String(data.summary?.generation?.mode || '').trim();
    const isChunked = pipelineMode === 'chunked-character-v1' || generationMode === 'line-chunked';

    if (!isChunked) {
      return {
        success: false,
        generatedCount: 0,
        errors: [
          'Backend did not confirm chunked character generation. Restart backend and retry.',
        ],
        canceled: false,
      };
    }

    const affectedChapters = new Set<string>();

    if (Array.isArray(data.chaptersAffected)) {
      for (const chapter of data.chaptersAffected) {
        if (typeof chapter !== 'string') continue;
        const normalized = chapter.trim();
        if (normalized) {
          affectedChapters.add(normalized);
        }
      }
    }

    if (affectedChapters.size === 0) {
      for (const line of lines) {
        const lineChapter = resolvePlannedLineChapterTitle(line, chapterTitle);
        if (lineChapter) {
          affectedChapters.add(lineChapter);
        }
      }
    }

    if (affectedChapters.size === 0) {
      affectedChapters.add(chapterTitle);
    }

    const affectedCharacterNames = new Set<string>();
    for (const line of lines) {
      const lineCharacterName = resolvePlannedLineCharacterName(line);
      if (lineCharacterName) {
        affectedCharacterNames.add(lineCharacterName);
      }
    }

    if (affectedCharacterNames.size === 0) {
      affectedCharacterNames.add(characterName);
    }

    for (const affectedChapter of affectedChapters) {
      for (const affectedCharacterName of affectedCharacterNames) {
        invalidateManifestCache(affectedChapter, affectedCharacterName);
      }
    }

    const generatedCount = Number(data.generatedCount || 0);
    const responseErrors = Array.isArray(data.errors) ? data.errors : [];

    if (onProgress) {
      onProgress(totalLines, totalLines);
    }

    return {
      success: Boolean(data.success) && responseErrors.length === 0,
      generatedCount,
      errors: responseErrors,
      canceled: false,
    };
  } catch (error) {
    return {
      success: false,
      generatedCount: 0,
      errors: [getBackendErrorMessage(error, 'Failed to generate character audio')],
      canceled: false,
    };
  } finally {
    stopProgressPolling();
  }
}

export async function generateAudioForChapterVibeVoice(
  dialoguePath: string,
  chapterTitle: string,
  sourceFile: string,
  assignments: ChapterVoiceAssignmentPayload[]
): Promise<{ success: boolean; generatedCount: number; skippedCount: number; totalLines: number; errors: string[] }> {
  try {
    const samplesRoot = get(voiceSamplesRoot);
    const resolvedAudioRoot = audioRootFor();

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
