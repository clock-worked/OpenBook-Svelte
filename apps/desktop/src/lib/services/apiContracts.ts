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

export interface VoiceSampleEntry {
  sample_file: string;
  display_name: string;
  tags: string[];
}

export interface ListVoiceSamplesResponse {
  samples?: VoiceSampleEntry[];
}

export interface SaveVoiceSampleMetadataRequest {
  samples_root: string;
  sample_file: string;
  display_name: string;
  tags: string[];
}

export interface SaveVoiceSampleMetadataResponse {
  sample?: VoiceSampleEntry;
}

export interface BackendChapterEntry {
  path?: string;
  name?: string;
  parsed?: boolean;
  scriptPath?: string;
  audio?: boolean;
  reviewed?: boolean;
}

export interface ListChaptersResponse {
  chapters?: BackendChapterEntry[];
}

export interface PickBookDirectoryResponse {
  path: string | null;
}

export interface SetBookRootRequest {
  root_path: string;
}

export interface SetAudioRootRequest {
  audio_root: string;
}

export interface ReadTextRequest {
  file_path: string;
  optional?: boolean;
}

export interface ReadTextResponse {
  content: string | null;
}

export interface SaveFileRequest {
  file_path: string;
  content: unknown;
}

export interface UpdateCharacterStatsResponse {
  stats?: unknown;
}

export interface ReviewChapterRequest {
  chapter_name: string;
}

export interface ReviewChapterResponse {
  chapter: string;
  reviewed: boolean;
  reviewedAt: string;
  reviewedLineCount: number;
  descriptorObservations: number;
  descriptorsAdded: number;
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

export interface VibeVoiceCharacterLineRequest {
  id: number;
  text: string;
  characterId?: string;
  characterName?: string;
  chosenSpeaker?: string;
  chapterTitle?: string;
  sourceFile?: string;
}

export interface GenerateAudioCharacterRequest {
  character_name: string;
  character_id: string;
  lines: VibeVoiceCharacterLineRequest[];
  voice_id: string;
  provider: string;
  chapter_title: string;
  source_file: string;
  use_filler_for_short_batch?: boolean;
  filler_text?: string | null;
  replace_line_final_commas_with_periods?: boolean;
  replace_numbers_with_words?: boolean;
  voice_sample_root: string | null;
  audio_root: string | null;
}

export interface GenerateAudioCharacterResponse {
  pipelineMode?: string;
  success?: boolean;
  generatedCount?: number;
  totalLines?: number;
  chaptersAffected?: string[];
  errors?: string[];
  warnings?: string[];
  summary?: {
    generation?: {
      mode?: string;
      chunkCount?: number;
      minChunkChars?: number;
    };
  };
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

export type DialogueAiAssistAction =
  | 'keep_existing'
  | 'reassign_existing'
  | 'needs_review'
  | 'propose_alias'
  | 'propose_new_character'
  | 'error';

export type DialogueAiAssistDisposition =
  | 'auto_apply'
  | 'review'
  | 'alias_review'
  | 'new_character_approval'
  | 'error';

export interface DialogueAiAssistCharacterRef {
  characterId: string;
  name: string;
  aliases?: string[];
  roleLabels?: string[];
  notes?: string | null;
}

export interface DialogueAiAssistAliasProposal {
  characterId: string;
  characterName: string;
  alias: string;
}

export interface DialogueAiAssistNewCharacterProposal {
  name: string;
  alias?: string | null;
  notes?: string | null;
}

export interface DialogueAiAssistToolTraceEntry {
  tool:
  | 'request_previous_paragraph'
  | 'request_next_paragraph'
  | 'request_book_characters'
  | 'request_quote_structure'
  | 'request_recent_turn_history'
  | 'request_scene_entities'
  | 'request_candidate_cards'
  | 'request_attribution_signals';
  status: 'used' | 'unavailable' | 'ignored';
  note?: string | null;
}

export interface DialogueAiAssistLineResult {
  lineId: number;
  paragraphIndex: number | null;
  currentCharacterId?: string | null;
  currentCharacterName?: string | null;
  suggestedCharacterId?: string | null;
  suggestedCharacterName?: string | null;
  action: DialogueAiAssistAction;
  disposition: DialogueAiAssistDisposition;
  confidence: number;
  reasonCodes: string[];
  currentParagraphText: string;
  previousParagraphText?: string | null;
  nextParagraphText?: string | null;
  toolTrace: DialogueAiAssistToolTraceEntry[];
  aliasToAdd?: DialogueAiAssistAliasProposal | null;
  newCharacterProposal?: DialogueAiAssistNewCharacterProposal | null;
  cacheHit?: boolean;
  logPath?: string | null;
  error?: string | null;
}

export interface DialogueAiAssistSummary {
  scannedLines: number;
  autoApplyCount: number;
  reviewCount: number;
  aliasReviewCount: number;
  newCharacterCount: number;
  errorCount: number;
  skippedNarratorLines: number;
  cacheHits?: number;
  modelCalls?: number;
  logDirectory?: string | null;
}

export interface DialogueAiAssistStatus {
  requestId: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  processedLines: number;
  totalLines: number;
  progressRatio: number;
  startedAt?: string | null;
  updatedAt?: string | null;
  chapterPath?: string | null;
  error?: string | null;
  logDirectory?: string | null;
}

export interface DialogueAiAssistRequest {
  request_id?: string | null;
  chapter_path: string;
  chapter_text: string;
  dialogue: import('$lib/types').DialogueJson;
  auto_apply_threshold?: number;
  max_lines?: number | null;
  dry_run?: boolean;
}

export interface DialogueAiAssistResponse {
  summary: DialogueAiAssistSummary;
  results: DialogueAiAssistLineResult[];
  errors?: string[];
}

export type LocalDialogueAiOutcome =
  | 'keep_existing'
  | 'suggestion'
  | 'unresolved'
  | 'error';

export interface LocalDialogueAiLineResult {
  lineId: number;
  paragraphIndex: number | null;
  currentCharacterId?: string | null;
  currentCharacterName?: string | null;
  suggestedCharacterId?: string | null;
  suggestedCharacterName?: string | null;
  outcome: LocalDialogueAiOutcome;
  currentParagraphText: string;
  rawOutput?: string | null;
  error?: string | null;
}

export interface LocalDialogueAiSummary {
  scannedLines: number;
  unchangedCount: number;
  suggestionCount: number;
  unresolvedCount: number;
  errorCount: number;
  skippedNarratorLines: number;
  modelCalls: number;
}

export interface LocalDialogueAiRequest {
  request_id?: string | null;
  chapter_path: string;
  chapter_text: string;
  dialogue: import('$lib/types').DialogueJson;
  context_window: number;
  include_previous_speaker: boolean;
  include_speaker_context: boolean;
  max_lines?: number | null;
}

export interface LocalDialogueAiResponse {
  summary: LocalDialogueAiSummary;
  results: LocalDialogueAiLineResult[];
  errors?: string[];
}
