export interface ManifestData {
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

export interface ScanVoiceManifestsRequest {
  audioRoot: string;
}

export interface ScanVoiceManifestsResponse {
  manifests?: ManifestData[];
}

export interface ListVoiceSamplesRequest {
  samples_root: string;
}

export interface ListVoiceSamplesResponse {
  samples?: string[];
}

export interface BackendChapterEntry {
  path?: string;
  name?: string;
  parsed?: boolean;
  scriptPath?: string;
  audio?: boolean;
}

export interface ListChaptersResponse {
  chapters?: BackendChapterEntry[];
}

export interface SetBookRootRequest {
  root_path: string;
}

export interface SetAudioRootRequest {
  audio_root: string;
}

export interface UpdateCharacterStatsResponse {
  stats?: unknown;
}

export interface ReadFileAbsoluteRequest {
  file_path: string;
}

export interface ChapterVoiceAssignmentPayload {
  character_id: string;
  character_name: string;
  voice_id: string;
  provider: string;
}

export interface GenerateAudioLineRequest {
  line_id: number;
  text: string;
  character_name: string;
  character_id: string;
  voice_id: string;
  provider: string;
  chapter_title: string;
  source_file: string;
  voice_sample_root: string | null;
  audio_root: string | null;
}

export interface GenerateAudioLineSuccessResponse {
  audio_path?: string;
}

export interface GenerateAudioChapterRequest {
  dialogue_path: string;
  chapter_title: string;
  source_file: string;
  assignments: ChapterVoiceAssignmentPayload[];
  voice_sample_root: string | null;
  audio_root: string | null;
}

export interface GenerateAudioChapterResponse {
  success?: boolean;
  generatedCount?: number;
  skippedCount?: number;
  totalLines?: number;
  errors?: string[];
  detail?: string;
  error?: string;
}

export interface DeleteCharacterAudioRequest {
  chapter_title: string;
  character_name: string;
}

export interface DeleteAudioLineRequest {
  chapter_title: string;
  character_name: string;
  line_id: number;
}

export interface ReconcileAudioManifestRequest {
  chapter_title: string;
  character_name: string;
  audio_root: string | null;
}

export interface ReconcileAudioManifestResponse {
  updated?: boolean;
  removed_count?: number;
  clip_count?: number;
  manifest?: ManifestData | Record<string, unknown> | null;
}

export interface BackendErrorPayload {
  detail?: string;
  error?: string;
  message?: string;
}