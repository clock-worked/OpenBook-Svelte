"""Deterministic attribution pass stack for dialogue speaker scoring.

This module keeps line-level scoring logic separate from parser I/O so we can
iterate on attribution quality without growing parser service complexity.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Dict, List, Optional, Sequence, Tuple


def _load_verbs_from_file(filename: str, default: set[str]) -> set[str]:
    fallback = set(default)

    try:
        data_dir = Path(__file__).resolve().parent / "data"
        verbs_path = data_dir / filename
        if not verbs_path.exists():
            return fallback

        loaded: set[str] = set()
        with verbs_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                value = raw.strip().lower()
                if not value or value.startswith("#"):
                    continue
                loaded.add(value)
        return loaded or fallback
    except OSError:
        return fallback


SPEECH_VERBS = _load_verbs_from_file(
    "speech_verbs.txt",
    {
        "said", "asked", "replied", "answered", "muttered", "shouted", "whispered",
        "snapped", "yelled", "growled", "sighed", "hissed", "called", "told",
        "added", "continued", "remarked", "noted", "stated", "declared", "insisted",
    },
)
REACTION_VERBS = _load_verbs_from_file(
    "reaction_verbs.txt",
    {"grunted", "sighed", "spat"},
)
ATTRIBUTION_VERBS = SPEECH_VERBS | REACTION_VERBS | {
    "cursed",
    "gravelled",
    "keened",
    "pointed out",
}
ATTRIBUTION_GENDER_CUE_VERBS = ATTRIBUTION_VERBS | {
    "barked",
    "croaked",
    "greeted",
    "murmured",
}
PRONOUN_GENDER_MAP = {
    "he": "male",
    "him": "male",
    "his": "male",
    "she": "female",
    "her": "female",
    "they": "neutral",
    "them": "neutral",
    "their": "neutral",
}
ATTRIBUTION_GENDER_CUE_VERB_TOKENS = sorted(
    {
        tuple(part for part in verb.split() if part)
        for verb in ATTRIBUTION_GENDER_CUE_VERBS
    },
    key=len,
    reverse=True,
)
ATTRIBUTION_VERB_PHRASES = sorted(
    ATTRIBUTION_VERBS - REACTION_VERBS,
    key=len,
    reverse=True,
)
SPEECH_PHASE_VERBS = {"began", "started", "finished"}


@dataclass
class CandidateSignal:
    name: str
    score: float
    reasons: List[str]


@dataclass(frozen=True)
class ParagraphRange:
    start: int
    end: int


@dataclass
class LineSignals:
    predicted_name: str
    legacy_name: str
    prev_resolved: Optional[str]
    next_resolved: Optional[str]
    used_fallback_alignment: bool
    exact_quote_match: bool
    quote_boundary_quality: float
    nearby_speech_verb: bool
    has_coref_name: bool
    alias_match_strength: float
    addressed_name_prev_sentence: Optional[str] = None
    explicit_quote_mentions: Optional[List[str]] = None
    next_sentence_attributed_speaker: Optional[str] = None
    recent_named_mention_before_quote: Optional[str] = None
    previous_paragraph_named_mention: Optional[str] = None
    candidate_genders: Optional[Dict[str, str]] = None
    context_gender: Optional[str] = None
    context_gender_cue: Optional[str] = None
    is_returning: bool = False
    continues_paragraph_dialogue: bool = False
    line_ends_with_question: bool = False
    next_dialogue_legacy: Optional[str] = None
    descriptor_match: Optional[str] = None


@dataclass
class RankedDecision:
    ranked: List[CandidateSignal]
    chosen_name: str
    is_suggestion: bool


def _norm(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _clamp(value: float, lo: float = 0.02, hi: float = 0.98) -> float:
    return max(lo, min(hi, value))


def _contains_name(text: str, name: str) -> bool:
    if not text or not name:
        return False
    pattern = r"\b" + re.escape(name.lower()) + r"\b"
    return bool(re.search(pattern, text.lower()))


def _is_generic_speaker_label(name: str) -> bool:
    lowered = _norm(name)
    if not lowered:
        return True

    generic = {
        "he", "she", "they", "we", "you", "i", "it", "this", "that",
        "these", "those", "him", "her", "them", "his", "hers", "its",
        "their", "our", "protagonist", "person", "speaker",
        "the man", "the woman", "the crowd", "the boy", "the girl",
    }
    if lowered in generic:
        return True
    if lowered.startswith("unknown"):
        return True
    return False


def extract_last_sentence(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", raw)
    return parts[-1].strip() if parts else raw


def extract_first_sentence(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", raw)
    return parts[0].strip() if parts else raw


def build_paragraph_ranges(raw_text: str) -> List[ParagraphRange]:
    text = str(raw_text or "")
    if not text:
        return []

    ranges: List[ParagraphRange] = []
    text_length = len(text)
    index = 0

    while index < text_length:
        line_start = index
        line_end = line_start
        while line_end < text_length and text[line_end] not in ("\n", "\r"):
            line_end += 1

        line_text = text[line_start:line_end]
        next_line_start = line_end
        if next_line_start < text_length:
            if (
                text[next_line_start] == "\r"
                and next_line_start + 1 < text_length
                and text[next_line_start + 1] == "\n"
            ):
                next_line_start += 2
            else:
                next_line_start += 1

        if line_text.strip():
            ranges.append(ParagraphRange(start=line_start, end=line_end))

        index = next_line_start

    return ranges


def find_paragraph_index_for_offset(
    paragraph_ranges: Sequence[ParagraphRange],
    offset: Optional[int],
) -> int:
    if not isinstance(offset, int):
        return -1

    for index, paragraph_range in enumerate(paragraph_ranges):
        if paragraph_range.start <= offset < paragraph_range.end:
            return index

    return -1


def infer_addressed_name(last_sentence: str, known_names: Sequence[str]) -> Optional[str]:
    sentence = str(last_sentence or "")
    if not sentence:
        return None
    lower_sentence = sentence.lower()

    for name in known_names:
        candidate = str(name or "").strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if re.search(rf"\bto\s+{re.escape(lowered)}\b", lower_sentence):
            return candidate
        if re.search(rf"\btoward\s+{re.escape(lowered)}\b", lower_sentence):
            return candidate
        if re.search(rf"\bturned\s+to\s+{re.escape(lowered)}\b", lower_sentence):
            return candidate
        if re.search(rf"\bfacing\s+{re.escape(lowered)}\b", lower_sentence):
            return candidate
    return None


def extract_explicit_mentions(quote_text: str, known_names: Sequence[str]) -> List[str]:
    mentions: List[str] = []
    text = str(quote_text or "")
    for name in known_names:
        candidate = str(name or "").strip()
        if not candidate:
            continue
        if _contains_name(text, candidate):
            mentions.append(candidate)
    return mentions


def infer_recent_named_mention(text: str, known_names: Sequence[str]) -> Optional[str]:
    raw_text = str(text or "")
    if not raw_text:
        return None

    latest_index = -1
    latest_name: Optional[str] = None
    for name in known_names:
        candidate = str(name or "").strip()
        if not candidate:
            continue
        pattern = r"\b" + re.escape(candidate) + r"(?:['’]s)?\b"
        for match in re.finditer(pattern, raw_text, re.I):
            if match.start() >= latest_index:
                latest_index = match.start()
                latest_name = candidate

    return latest_name


def infer_previous_paragraph_named_mention(text: str, known_names: Sequence[str]) -> Optional[str]:
    raw_text = str(text or "")
    if not raw_text:
        return None

    matches: List[Tuple[int, str]] = []
    for name in known_names:
        candidate = str(name or "").strip()
        if not candidate:
            continue
        pattern = r"\b" + re.escape(candidate) + r"(?:['’]s)?\b"
        for match in re.finditer(pattern, raw_text, re.I):
            matches.append((match.start(), candidate))

    if not matches:
        return None

    matches.sort(key=lambda item: item[0])
    unique_names = {_norm(name) for _, name in matches}
    if len(unique_names) != 1:
        return None

    return matches[-1][1]


def _normalize_phrase_text(text: str) -> str:
    lowered = str(text or "").lower()
    lowered = re.sub(r"[^a-z0-9']+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def infer_sentence_attributed_speaker(sentence: str, known_names: Sequence[str]) -> Optional[str]:
    normalized_sentence = _normalize_phrase_text(sentence)
    if not normalized_sentence:
        return None
    padded_sentence = f" {normalized_sentence} "
    direct_matches: set[str] = set()
    reverse_matches: set[str] = set()
    phase_matches: set[str] = set()

    for name in sorted(known_names, key=lambda value: len(str(value or "")), reverse=True):
        candidate = str(name or "").strip()
        if not candidate:
            continue
        lowered = _normalize_phrase_text(candidate)
        if not lowered:
            continue
        for verb in ATTRIBUTION_VERB_PHRASES:
            if f" {lowered} {verb} " in padded_sentence:
                direct_matches.add(candidate)
            if f" {verb} {lowered} " in padded_sentence:
                reverse_matches.add(candidate)
        phase_pattern = rf"\b{re.escape(lowered)}\s+(?:{'|'.join(SPEECH_PHASE_VERBS)})(?=\s*[,.;:!?]|\s*$)"
        if re.search(phase_pattern, str(sentence or ""), re.I):
            phase_matches.add(candidate)

    subject_matches = direct_matches | phase_matches
    if len(subject_matches) == 1:
        return next(iter(subject_matches))
    if subject_matches:
        return None
    if len(reverse_matches) == 1:
        return next(iter(reverse_matches))
    return None


def infer_pronoun_attributed_gender(sentence: str) -> Tuple[Optional[str], Optional[str]]:
    text = str(sentence or "").strip()
    if not text:
        return None, None
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", text.lower())
    if not tokens:
        return None, None

    pronouns = {"he", "she", "they"}

    for index, token in enumerate(tokens):
        if token not in pronouns:
            continue

        for verb_tokens in ATTRIBUTION_GENDER_CUE_VERB_TOKENS:
            verb_len = len(verb_tokens)
            if verb_len == 0:
                continue

            forward = tokens[index + 1:index + 1 + verb_len]
            if tuple(forward) == verb_tokens:
                verb_phrase = " ".join(verb_tokens)
                return PRONOUN_GENDER_MAP.get(token), f"{token} {verb_phrase}"

            backward_start = index - verb_len
            if backward_start >= 0:
                backward = tokens[backward_start:index]
                if tuple(backward) == verb_tokens:
                    verb_phrase = " ".join(verb_tokens)
                    return PRONOUN_GENDER_MAP.get(token), f"{verb_phrase} {token}"
    return None, None


def detect_nearby_speech_verb(context_text: str) -> bool:
    text = _norm(context_text)
    if not text:
        return False
    return any(f" {verb} " in f" {text} " for verb in SPEECH_VERBS)


def quote_boundary_quality(context_text: str, quote_text: str) -> float:
    context = str(context_text or "")
    quote = str(quote_text or "")
    if not context or not quote:
        return 0.35

    score = 0.35
    if '"' in context or "“" in context or "”" in context:
        score += 0.35
    if quote.endswith("?") or quote.endswith("!") or quote.endswith("."):
        score += 0.15
    if len(quote.split()) >= 4:
        score += 0.1
    return _clamp(score, 0.0, 1.0)


def rank_candidates(candidates: Sequence[str], signals: LineSignals) -> RankedDecision:
    unique = []
    seen = set()
    for candidate in candidates:
        name = str(candidate or "").strip()
        if not name:
            continue
        key = _norm(name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)

    if not unique:
        unique = [signals.predicted_name or signals.legacy_name or "Narrator"]

    scored: List[CandidateSignal] = []
    addressed = _norm(signals.addressed_name_prev_sentence)
    next_sentence_speaker = _norm(signals.next_sentence_attributed_speaker)
    recent_named_mention = _norm(signals.recent_named_mention_before_quote)
    previous_paragraph_named_mention = _norm(signals.previous_paragraph_named_mention)
    next_dialogue_legacy = _norm(signals.next_dialogue_legacy)
    quote_mentions = {_norm(name) for name in (signals.explicit_quote_mentions or [])}
    context_gender = _norm(signals.context_gender)
    candidate_genders = {
        _norm(name): _norm(gender)
        for name, gender in (signals.candidate_genders or {}).items()
        if _norm(name) and _norm(gender)
    }
    non_narrator_count = sum(1 for candidate in unique if _norm(candidate) != "narrator")
    has_specific_named_candidate = any(
        _norm(candidate) != "narrator" and not _is_generic_speaker_label(candidate)
        for candidate in unique
    )
    predicted_is_generic = _is_generic_speaker_label(signals.predicted_name)

    for candidate in unique:
        key = _norm(candidate)
        score = 0.38
        reasons: List[str] = []

        if key == _norm(signals.predicted_name):
            score += 0.2 if signals.exact_quote_match else 0.08
            reasons.append("parser_match")

        if key == _norm(signals.legacy_name) and key != "narrator":
            score += 0.16
            reasons.append("legacy_match")
            if signals.used_fallback_alignment or predicted_is_generic:
                score += 0.1
                reasons.append("legacy_anchor_boost")

        if signals.prev_resolved and key == _norm(signals.prev_resolved):
            if signals.continues_paragraph_dialogue:
                score += 0.4
                reasons.append("same_paragraph_continuation")
            else:
                score += 0.1
                reasons.append("prev_continuity")

        if signals.next_resolved and key == _norm(signals.next_resolved):
            score += 0.1
            reasons.append("next_continuity")

        if (
            signals.prev_resolved
            and signals.next_resolved
            and _norm(signals.prev_resolved) == _norm(signals.next_resolved)
            and key == _norm(signals.prev_resolved)
        ):
            score += 0.08
            reasons.append("two_sided_continuity")

        if key == _norm(signals.predicted_name) and signals.has_coref_name:
            score += 0.08
            reasons.append("coref_support")

        if key == _norm(signals.predicted_name):
            score += 0.08 * signals.alias_match_strength
            if signals.alias_match_strength > 0:
                reasons.append("alias_strength")

        if key == _norm(signals.descriptor_match) and key != "narrator":
            score += 0.4
            reasons.append("learned_descriptor_match")

        if next_sentence_speaker and key == next_sentence_speaker and key != "narrator":
            score += 0.4
            reasons.append("post_quote_attribution")

        if recent_named_mention and key == recent_named_mention and key != "narrator":
            score += 0.16
            reasons.append("recent_named_mention")

        if (
            previous_paragraph_named_mention
            and key == previous_paragraph_named_mention
            and key != "narrator"
        ):
            score += 0.16
            reasons.append("previous_paragraph_named_mention")

        score += 0.08 * signals.quote_boundary_quality
        if signals.nearby_speech_verb:
            score += 0.06
            reasons.append("speech_verb")

        if signals.used_fallback_alignment and key == _norm(signals.predicted_name):
            score -= 0.1
            reasons.append("fallback_penalty")

        if has_specific_named_candidate and _is_generic_speaker_label(key):
            score -= 0.2
            reasons.append("generic_label_penalty")

        if addressed and key == addressed and key != "narrator" and non_narrator_count >= 2:
            score -= 0.18
            reasons.append("addressed_target_penalty")

        if key in quote_mentions and key != "narrator" and non_narrator_count >= 2:
            score -= 0.15
            reasons.append("self_name_mention_penalty")

        candidate_gender = candidate_genders.get(key)
        if context_gender and candidate_gender and key != "narrator":
            if candidate_gender == context_gender:
                score += 0.18
                reasons.append("context_gender_match")
            else:
                score -= 0.34
                reasons.append("context_gender_conflict")

        if (
            signals.is_returning
            and signals.line_ends_with_question
            and next_dialogue_legacy
            and key == next_dialogue_legacy
            and key != "narrator"
            and non_narrator_count >= 2
        ):
            score -= 0.18
            reasons.append("question_next_speaker_penalty")

        scored.append(CandidateSignal(name=candidate, score=_clamp(score), reasons=reasons))

    scored.sort(key=lambda item: item.score, reverse=True)
    top = scored[0].score if scored else 0.0
    second = scored[1].score if len(scored) > 1 else 0.0
    margin = top - second

    top_reasons = set(scored[0].reasons) if scored else set()
    top_choice = _norm(scored[0].name) if scored else ""
    hard_reasons = {
        "parser_match",
        "coref_support",
        "legacy_match",
        "post_quote_attribution",
        "recent_named_mention",
        "previous_paragraph_named_mention",
        "same_paragraph_continuation",
        "speech_verb",
        "alias_strength",
        "learned_descriptor_match",
        "context_gender_match",
    }
    hard_reason_count = len(top_reasons & hard_reasons)
    high_conflict = bool(
        signals.is_returning
        and signals.line_ends_with_question
        and next_dialogue_legacy
        and top_choice == next_dialogue_legacy
        and "question_next_speaker_penalty" in top_reasons
    )
    gender_conflict = bool(context_gender and "context_gender_conflict" in top_reasons)
    generic_top_choice = bool(
        scored
        and _is_generic_speaker_label(scored[0].name)
        and has_specific_named_candidate
    )

    is_suggestion = bool(
        signals.used_fallback_alignment
        or top < 0.74
        or margin < 0.14
        or high_conflict
        or gender_conflict
        or generic_top_choice
        or (hard_reason_count == 0 and top < 0.8)
    )

    return RankedDecision(
        ranked=scored,
        chosen_name=scored[0].name if scored else (signals.predicted_name or "Narrator"),
        is_suggestion=is_suggestion,
    )
