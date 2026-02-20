"""Deterministic attribution pass stack for dialogue speaker scoring.

This module keeps line-level scoring logic separate from parser I/O so we can
iterate on attribution quality without growing parser service complexity.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import List, Optional, Sequence


def _load_speech_verbs_from_file() -> set[str]:
    default = {
        "said", "asked", "replied", "answered", "muttered", "shouted", "whispered",
        "snapped", "yelled", "growled", "sighed", "hissed", "called", "told",
        "added", "continued", "remarked", "noted", "stated", "declared", "insisted",
    }

    try:
        data_dir = Path(__file__).resolve().parent / "data"
        verbs_path = data_dir / "speech_verbs.txt"
        if not verbs_path.exists():
            return default

        loaded: set[str] = set()
        with verbs_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                value = raw.strip().lower()
                if not value or value.startswith("#"):
                    continue
                loaded.add(value)
        return loaded or default
    except Exception:
        return default


SPEECH_VERBS = _load_speech_verbs_from_file()


@dataclass
class CandidateSignal:
    name: str
    score: float
    reasons: List[str]


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
    line_ends_with_question: bool = False
    next_dialogue_legacy: Optional[str] = None


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
        "he", "she", "they", "we", "you", "i", "him", "her", "them",
        "his", "hers", "their", "our", "protagonist", "person", "speaker",
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


def infer_sentence_attributed_speaker(sentence: str, known_names: Sequence[str]) -> Optional[str]:
    text = str(sentence or "")
    if not text:
        return None
    lower_text = text.lower()

    for name in known_names:
        candidate = str(name or "").strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        for verb in SPEECH_VERBS:
            if re.search(rf"\b{re.escape(lowered)}\b\s+{re.escape(verb)}\b", lower_text):
                return candidate
            if re.search(rf"\b{re.escape(verb)}\b[^.?!]*\b{re.escape(lowered)}\b", lower_text):
                return candidate
    return None


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
    next_dialogue_legacy = _norm(signals.next_dialogue_legacy)
    quote_mentions = {_norm(name) for name in (signals.explicit_quote_mentions or [])}
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
            reasons.append("booknlp_match")

        if key == _norm(signals.legacy_name) and key != "narrator":
            score += 0.16
            reasons.append("legacy_match")
            if signals.used_fallback_alignment or predicted_is_generic:
                score += 0.1
                reasons.append("legacy_anchor_boost")

        if signals.prev_resolved and key == _norm(signals.prev_resolved):
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

        if (
            next_sentence_speaker
            and key == next_sentence_speaker
            and key != "narrator"
            and non_narrator_count >= 2
        ):
            score -= 0.14
            reasons.append("next_sentence_attribution_penalty")

        if (
            signals.line_ends_with_question
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
        "booknlp_match",
        "coref_support",
        "legacy_match",
        "speech_verb",
        "alias_strength",
    }
    hard_reason_count = len(top_reasons & hard_reasons)
    high_conflict = bool(
        next_dialogue_legacy
        and top_choice == next_dialogue_legacy
        and "next_sentence_attribution_penalty" in top_reasons
    )

    # Treat weak/marginal outcomes as suggestions for review.
    is_suggestion = bool(
        signals.used_fallback_alignment
        or top < 0.74
        or margin < 0.14
        or high_conflict
        or (hard_reason_count == 0 and top < 0.8)
    )

    return RankedDecision(
        ranked=scored,
        chosen_name=scored[0].name if scored else (signals.predicted_name or "Narrator"),
        is_suggestion=is_suggestion,
    )
