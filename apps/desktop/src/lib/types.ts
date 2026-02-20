// ============================================================================
// Schema v2.0 - Primary Types
// ============================================================================

export type TtsProvider = 'vibevoice_local';
export type Gender = 'Male' | 'Female' | 'Unknown';

// === Characters (Story Characters) ===

export interface Character {
  id: string;  // Unique character ID (e.g., "catherine", "narrator")
  name: string;  // Display name
  gender: Gender;
  aliases: string[];
  race?: string;  // Character race/species (optional)
  color: string | null;
  notes: string;
  firstAppearance: string | null;  // Chapter ID
  stats: {
    totalLines: number;
    chapterCount: number;
  };
  // Legacy/optional voice-related properties (for backward compatibility)
  voice?: string | null;
  provider?: TtsProvider | null;
  voiceId?: string | null;
  voiceMeta?: CharacterVoiceMeta | null;
  manifestStats?: CharacterManifestStats | null;
  count?: number;  // Legacy: use stats.totalLines instead
  chapterCount?: number;  // Legacy: use stats.chapterCount instead
}

export interface CharactersJson {
  formatVersion: string;
  characters: Character[];
}

// === Voices (TTS Voices / Speakers) ===

export interface Voice {
  id: string;  // Unique voice ID (e.g., "vibevoice_local-my_sample.wav")
  displayName: string;  // UI display name (e.g., "Black Knight Voice", "Narrator")
  provider: TtsProvider;
  providerVoiceId: string;  // Local sample/voice identifier for VibeVoice
  previewUrl: string | null;
  notes: string;  // User notes about the voice/speaker
  metadata: {
    gender?: 'M' | 'F' | 'U';  // Male, Female, Unknown
    ageRange?: string;  // "young-adult", "adult", etc.
    accent?: string;  // "neutral", "en-US", etc.
    tags?: string[];  // ["clear", "confident", ...]
    imageUrl?: string | null;  // Optional image for the voice
    // Audio generation stats
    totalClips?: number;  // Total clips generated with this voice
    usedByCharacters?: string[];  // Character IDs using this voice
    discoveredFrom?: 'manifest' | 'manual';  // How was this voice added
  };
}

export interface VoiceAssignment {
  characterId: string;  // Reference to character.id
  voiceId: string;  // Reference to voice.id
  priority: number;  // 1 = primary, higher = fallback
  contextOverrides: any[];  // Future: emotion-specific overrides
}

export interface VoicesJson {
  formatVersion: string;
  voices: Voice[];
  assignments: VoiceAssignment[];
}

// === Dialogue Lines ===

export interface LineCandidate {
  characterId: string;  // Reference to character.id
  confidence: number;  // 0.0 to 1.0
}

export interface LineMetadata {
  emotion: string | null;  // "neutral", "angry", "sad", etc.
  intensity: number;  // 0.0 to 1.0
  pacing: string | null;  // "slow", "normal", "fast"
  prefix: string | null;  // Custom prefix like "[shouting]"
  customTags: Record<string, any>;  // Arbitrary key-value pairs
}

export type AttributionResolutionStatus = 'auto' | 'unknown' | 'user_confirmed';

export interface LineAttributionCandidate {
  characterId: string | null;
  name: string;
  confidence: number;
  reasons?: string[];
}

export interface LineAttribution {
  confidence: number;
  topCandidateConfidence: number;
  marginToSecond: number;
  misattributionRisk: number;
  resolutionStatus: AttributionResolutionStatus;
  thresholdUsed: number;
  sourceAlias: string | null;
  sourceCandidates?: string[];
  candidates: LineAttributionCandidate[];
}

export interface DialogueLine {
  id: number;
  characterId: string | null;  // Reference to character.id (null defaults to narrator)
  text: string;
  span: { start: number; end: number } | null;
  metadata: LineMetadata;
  candidates: LineCandidate[];
  isConflict: boolean;
  attribution?: LineAttribution;
}

export interface DialogueJson {
  formatVersion: string;
  chapterId: string;
  lines: DialogueLine[];
  stats: {
    totalLines: number;
    conflicts: number;
    characterBreakdown: Record<string, number>;  // characterId → count
  };
}

// === UI Types ===

export type ParagraphRun = {
  text: string;
  characterId: string | null;  // Changed from "speaker" to "characterId"
  lineId?: number;
};

// ============================================================================
// Legacy v1.0 Types (for backward compatibility during migration)
// ============================================================================

/** @deprecated Use DialogueLine instead */
export interface LineItem {
  id: number;
  text: string;
  span: { start: number; end: number } | null;
  chosenSpeaker: string | null;  // v1: character name (not ID)
  candidates: { name: string; confidence: number }[];  // v1: uses name
  isConflict: boolean;
  attribution?: {
    confidence?: number;
    topCandidateConfidence?: number;
    marginToSecond?: number;
    misattributionRisk?: number;
    resolutionStatus?: AttributionResolutionStatus;
    thresholdUsed?: number;
    sourceAlias?: string | null;
    sourceCandidates?: string[];
    candidates?: { name: string; characterId?: string | null; confidence: number; reasons?: string[] }[];
  };
}

/** @deprecated Use DialogueJson instead */
export interface ScriptJson {
  chapter: string;
  sourceFile: string;
  lines: LineItem[];
  stats: { numConflicts: number; numLines: number };
}

/** @deprecated Use Character instead */
export interface CharacterConfig {
  name: string;
  color: string | null;
  voice: string | null;
  count?: number;
  provider?: TtsProvider | null;
  voiceId?: string | null;
  voiceMeta?: CharacterVoiceMeta | null;
  manifestStats?: CharacterManifestStats | null;
  firstAppearance?: string | null;
  chapterCount?: number;
}

export interface CharacterVoiceMeta {
  name?: string;
  previewUrl?: string | null;
  fetchedAt?: string;
  provider?: TtsProvider;
}

export interface CharacterManifestStats {
  clipCount: number;
  totalLines: number;
  voiceIds: Record<string, number>;
  coverageRatio?: number;
  primaryVoiceId?: string | null;
  lastUpdated?: string;
}

// ============================================================================
// Parser Types (from Python service)
// ============================================================================

export type Script = Line[];

export interface Line {
  id: number;
  segments: Segment[];
  speaker: string;  // Character name from parser
  line_type: 'narration' | 'dialogue';
  is_suggestion: boolean;
  suggestions: any[];
  span_start?: number;
  span_end?: number;
}

export type Segment = TextSegment | PauseSegment;

export interface TextSegment {
  type: 'text';
  value: string;
}

export interface PauseSegment {
  type: 'pause';
  duration: 'short' | 'medium' | 'long';
}

// ============================================================================
// Settings & Configuration
// ============================================================================

export interface AudiobookSettings {
  narratorSameAsProtagonist: boolean;
  narratorVoice?: VoiceAssignment;
  defaultAccent: string;
  speakingRate: number; // 1.0 default
  allowMultipleNarrators?: boolean;
  narrators?: string[];
  protagonist?: string | null;
  mainCharacters?: string[];
  sideCharacters?: string[];
}

export interface ParserHints {
  protagonistName?: string;
  protagonistNames?: string[];
  povMode?: 'first_person' | 'third_person';
  learnVerbs?: boolean;
  heuristics?: {
    protagonistFirstPersonTag?: boolean;
    narratorIdentity?: boolean;
    coreference?: boolean;
    explicitTags?: boolean;
    tagContinuation?: boolean;
    contiguousDialogue?: boolean;
    carryAcrossShortNarration?: boolean;
    suggestAlternatives?: boolean;
    firstPersonOverride?: boolean;
    narratorFallback?: boolean;
    vocativeGuard?: boolean;
  };
  manualBlockList?: string[];  // Character names to block during parsing
  attribution?: {
    unknownThreshold?: number;
  };
}

// ============================================================================
// Voice Models (for UI)
// ============================================================================

export interface VoiceModel {
  voiceId: string;
  name: string;
  provider: TtsProvider;
}

