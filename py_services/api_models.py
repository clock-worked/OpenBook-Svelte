from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class SetRootRequest(BaseModel):
    root_path: str


class SetAudioRootRequest(BaseModel):
    audio_root: str


class ParseRequest(BaseModel):
    text: str
    filename: str
    manual_blocklist: Optional[List[str]] = None
    parser_options: Optional[Dict[str, object]] = None


class CorefTestRequest(BaseModel):
    text: Optional[str] = None


class Span(BaseModel):
    start: int
    end: int


class Metadata(BaseModel):
    emotion: Optional[str] = None
    intensity: float
    pacing: Optional[str] = None
    prefix: Optional[str] = None
    customTags: Dict[str, Any]


class Candidate(BaseModel):
    characterId: Optional[str]
    confidence: float


class AttributionCandidate(BaseModel):
    characterId: Optional[str] = None
    name: str
    confidence: float
    reasons: Optional[List[str]] = None


class AttributionDecisionTrace(BaseModel):
    selectedCandidate: Optional[str] = None
    selectedReasons: Optional[List[str]] = None
    overrideReason: Optional[str] = None
    signals: Optional[Dict[str, Any]] = None


class Attribution(BaseModel):
    confidence: float
    topCandidateConfidence: float
    marginToSecond: float
    misattributionRisk: float
    resolutionStatus: Optional[str] = None
    thresholdUsed: float
    sourceAlias: Optional[str] = None
    sourceCandidates: Optional[List[str]] = None
    sourceDescriptors: Optional[List[str]] = None
    contextGender: Optional[str] = None
    contextGenderCue: Optional[str] = None
    genderConflict: Optional[bool] = None
    parserBackend: Optional[str] = None
    decisionTrace: Optional[AttributionDecisionTrace] = None
    candidates: List[AttributionCandidate] = Field(default_factory=list)


class DialogueLine(BaseModel):
    id: int
    characterId: Optional[str]
    text: str
    span: Span
    metadata: Metadata
    candidates: List[Candidate]
    isConflict: bool
    isReturning: bool = False
    attribution: Optional[Attribution] = None


class Stats(BaseModel):
    totalLines: int
    conflicts: int
    characterBreakdown: Dict[str, int]


class DialogueJson(BaseModel):
    formatVersion: str
    chapterId: str
    lines: List[DialogueLine]
    stats: Stats


class SaveRequest(BaseModel):
    file_path: str
    content: DialogueJson


class RelativeFileRequest(BaseModel):
    file_path: str


class ScanManifestsRequest(BaseModel):
    audioRoot: str


class ListVoiceSamplesRequest(BaseModel):
    samples_root: str


class VoiceSampleEntry(BaseModel):
    sample_file: str
    display_name: str
    tags: List[str] = []


class SaveVoiceSampleMetadataRequest(BaseModel):
    samples_root: str
    sample_file: str
    display_name: str
    tags: List[str] = []


class ReadFileAbsoluteRequest(BaseModel):
    file_path: str


class GenerateAudioLineRequest(BaseModel):
    line_id: int
    text: str
    character_name: str
    character_id: str
    voice_id: str
    provider: str
    chapter_title: str
    source_file: str
    voice_sample_root: Optional[str] = None
    audio_root: Optional[str] = None


class DeleteCharacterAudioRequest(BaseModel):
    chapter_title: str
    character_name: str


class DeleteAudioLineRequest(BaseModel):
    chapter_title: str
    character_name: str
    line_id: int


class ReconcileAudioManifestRequest(BaseModel):
    chapter_title: str
    character_name: str
    audio_root: Optional[str] = None


class DialogueAiCharacterRef(BaseModel):
    characterId: str
    name: str
    aliases: List[str] = Field(default_factory=list)
    roleLabels: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class DialogueAiAliasProposal(BaseModel):
    characterId: str
    characterName: str
    alias: str


class DialogueAiNewCharacterProposal(BaseModel):
    name: str
    alias: Optional[str] = None
    notes: Optional[str] = None


class DialogueAiToolTraceEntry(BaseModel):
    tool: Literal[
        "request_previous_paragraph",
        "request_next_paragraph",
        "request_book_characters",
        "request_quote_structure",
        "request_recent_turn_history",
        "request_scene_entities",
        "request_candidate_cards",
        "request_attribution_signals",
    ]
    status: Literal["used", "unavailable", "ignored"]
    note: Optional[str] = None


class DialogueAiLineResult(BaseModel):
    lineId: int
    paragraphIndex: Optional[int] = None
    currentCharacterId: Optional[str] = None
    currentCharacterName: Optional[str] = None
    suggestedCharacterId: Optional[str] = None
    suggestedCharacterName: Optional[str] = None
    action: Literal[
        "keep_existing",
        "reassign_existing",
        "needs_review",
        "propose_alias",
        "propose_new_character",
        "error",
    ]
    disposition: Literal["auto_apply", "review", "alias_review", "new_character_approval", "error"]
    confidence: float
    reasonCodes: List[str] = Field(default_factory=list)
    currentParagraphText: str
    previousParagraphText: Optional[str] = None
    nextParagraphText: Optional[str] = None
    toolTrace: List[DialogueAiToolTraceEntry] = Field(default_factory=list)
    aliasToAdd: Optional[DialogueAiAliasProposal] = None
    newCharacterProposal: Optional[DialogueAiNewCharacterProposal] = None
    cacheHit: bool = False
    logPath: Optional[str] = None
    error: Optional[str] = None


class DialogueAiAssistSummary(BaseModel):
    scannedLines: int
    autoApplyCount: int
    reviewCount: int
    aliasReviewCount: int
    newCharacterCount: int
    errorCount: int
    skippedNarratorLines: int
    cacheHits: int = 0
    modelCalls: int = 0
    logDirectory: Optional[str] = None


class DialogueAiAssistStatus(BaseModel):
    requestId: str
    status: Literal["queued", "running", "completed", "failed"]
    processedLines: int = 0
    totalLines: int = 0
    progressRatio: float = 0.0
    startedAt: Optional[str] = None
    updatedAt: Optional[str] = None
    chapterPath: Optional[str] = None
    error: Optional[str] = None
    logDirectory: Optional[str] = None


class DialogueAiAssistRequest(BaseModel):
    request_id: Optional[str] = None
    chapter_path: str
    chapter_text: str
    dialogue: DialogueJson
    auto_apply_threshold: float = 0.92
    max_lines: Optional[int] = None
    dry_run: bool = False


class DialogueAiAssistResponse(BaseModel):
    summary: DialogueAiAssistSummary
    results: List[DialogueAiLineResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class LocalDialogueAiLineResult(BaseModel):
    lineId: int
    paragraphIndex: Optional[int] = None
    currentCharacterId: Optional[str] = None
    currentCharacterName: Optional[str] = None
    suggestedCharacterId: Optional[str] = None
    suggestedCharacterName: Optional[str] = None
    outcome: Literal["keep_existing", "suggestion", "unresolved", "error"]
    currentParagraphText: str
    rawOutput: Optional[str] = None
    error: Optional[str] = None


class LocalDialogueAiSummary(BaseModel):
    scannedLines: int
    unchangedCount: int
    suggestionCount: int
    unresolvedCount: int
    errorCount: int
    skippedNarratorLines: int
    modelCalls: int = 0


class LocalDialogueAiRequest(BaseModel):
    request_id: Optional[str] = None
    chapter_path: str
    chapter_text: str
    dialogue: DialogueJson
    context_window: int = Field(default=600, ge=0, le=8000)
    include_previous_speaker: bool = True
    include_speaker_context: bool = True
    max_lines: Optional[int] = None


class LocalDialogueAiResponse(BaseModel):
    summary: LocalDialogueAiSummary
    results: List[LocalDialogueAiLineResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
