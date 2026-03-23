from typing import Any, Dict, List, Optional

from pydantic import BaseModel


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
    candidates: List[AttributionCandidate] = []


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


class ScanManifestsRequest(BaseModel):
    audioRoot: str


class ListVoiceSamplesRequest(BaseModel):
    samples_root: str


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