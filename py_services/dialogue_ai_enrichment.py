"""Structured enrichment helpers for dialogue AI assist.

This module converts raw paragraph text plus known characters into compact,
deterministic evidence the model can reason over more reliably than raw prose.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from api_models import DialogueAiCharacterRef
from openbook_parser.attribution_passes import (
    ATTRIBUTION_VERB_PHRASES,
    REACTION_VERBS,
    SPEECH_VERBS,
    detect_nearby_speech_verb,
    extract_explicit_mentions,
    extract_first_sentence,
    extract_last_sentence,
    infer_addressed_name,
    infer_previous_paragraph_named_mention,
    infer_recent_named_mention,
    infer_sentence_attributed_speaker,
)


QUOTE_PATTERN = re.compile(r'["“](.+?)["”]', re.DOTALL)
ROLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "of",
    "the",
    "to",
    "with",
}
TITLE_PREFIXES = {
    "arch",
    "chief",
    "grand",
    "head",
    "high",
    "holy",
    "king",
    "lady",
    "lord",
    "queen",
    "sir",
}
TRAILING_SURFACE_WORDS = {
    "again",
    "calmly",
    "just",
    "only",
    "openly",
    "quietly",
    "sharply",
    "simply",
    "slowly",
    "softly",
    "still",
}
SURFACE_BREAK_WORDS = {
    " as ",
    " because ",
    " but ",
    " even if ",
    " if ",
    " since ",
    " than ",
    " that ",
    " when ",
    " where ",
    " while ",
    " who ",
    " with ",
}
SUBJECT_PATTERN = re.compile(
    r"(?:(?:the|a|an)\s+)?[a-zA-Z][a-zA-Z'’\-]*(?:\s+[a-zA-Z][a-zA-Z'’\-]*){0,4}|he|she|they",
    re.IGNORECASE,
)
ACTION_CUE_WORDS = set(ATTRIBUTION_VERB_PHRASES) | REACTION_VERBS


def _normalize(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_title_prefix(value: str) -> str:
    tokens = [token for token in _normalize(value).split(" ") if token]
    while len(tokens) > 1:
        if len(tokens) > 2 and tokens[0] in {"the", "a", "an"} and tokens[1] in TITLE_PREFIXES:
            tokens = [tokens[0], *tokens[2:]]
            continue
        if tokens[0] in TITLE_PREFIXES:
            tokens = tokens[1:]
            continue
        break
    return " ".join(tokens)


def _strip_article_prefix(value: str) -> str:
    tokens = [token for token in _normalize(value).split(" ") if token]
    while len(tokens) > 1 and tokens[0] in {"the", "a", "an"}:
        tokens = tokens[1:]
    return " ".join(tokens)


def _tokenize(value: str) -> List[str]:
    return [
        token
        for token in _normalize(value).split(" ")
        if token and token not in ROLE_STOPWORDS
    ]


def _known_surface_names(characters: Sequence[DialogueAiCharacterRef]) -> List[str]:
    surfaces: List[str] = []
    seen: set[str] = set()
    for character in characters:
        for raw in [character.name, character.characterId, *(character.aliases or [])]:
            normalized = _normalize(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            surfaces.append(str(raw).strip())
    return surfaces


def _character_role_labels(character: DialogueAiCharacterRef) -> List[str]:
    labels: List[str] = []
    seen: set[str] = set()
    for raw in [character.characterId, character.name, *(character.aliases or [])]:
        for candidate in (
            _normalize(raw),
            _strip_title_prefix(str(raw or "")),
            _strip_article_prefix(str(raw or "")),
            _strip_article_prefix(_strip_title_prefix(str(raw or ""))),
        ):
            normalized = _normalize(candidate)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            labels.append(normalized)
    return labels


def _verb_regex() -> str:
    return "|".join(
        re.escape(verb)
        for verb in sorted(ATTRIBUTION_VERB_PHRASES, key=len, reverse=True)
    )


ATTRIBUTION_VERB_REGEX = _verb_regex()


def _extract_subject_before(text: str) -> Optional[str]:
    tail = str(text or "").strip(" ,;:-\u2014\u2013")
    if not tail:
        return None
    tail = re.split(r"[.!?]\s+", tail)[-1].strip()
    matches = [
        match
        for match in SUBJECT_PATTERN.finditer(tail)
        if (match.start() == 0 or not tail[match.start() - 1].isalnum())
        and (match.end() >= len(tail) or not tail[match.end()].isalnum())
    ]
    if not matches:
        return None
    surface = matches[-1].group(0).strip()
    if _normalize(surface) in {"he", "she", "they"}:
        proper_names = list(
            re.finditer(
                r"\b([A-Z][A-Za-z'â€™\-]*(?:\s+[A-Z][A-Za-z'â€™\-]*){0,3})\b",
                tail[: matches[-1].start()],
            )
        )
        if proper_names:
            return proper_names[-1].group(1).strip()
    return surface


def _extract_subject_after(text: str) -> Optional[str]:
    head = str(text or "").strip(" ,;:-\u2014\u2013")
    if not head:
        return None
    head = re.split(r"[,.!?;:]", head)[0].strip()
    match = SUBJECT_PATTERN.search(head)
    return match.group(0).strip() if match else None


def _verb_type(verb: str) -> str:
    normalized = _normalize(verb)
    if normalized in SPEECH_VERBS:
        return "speech"
    if normalized in REACTION_VERBS:
        return "reaction"
    return "attribution"


def _trim_surface(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip(" ,;:-\u2014\u2013")
    if not text:
        return None

    normalized_text = text.lower()
    for breaker in SURFACE_BREAK_WORDS:
        if breaker in normalized_text:
            split_index = normalized_text.index(breaker)
            candidate = text[:split_index].strip(" ,;:-\u2014\u2013")
            if candidate:
                text = candidate
                normalized_text = text.lower()

    tokens = [token for token in text.split(" ") if token]
    while tokens and tokens[-1].lower() in TRAILING_SURFACE_WORDS:
        tokens = tokens[:-1]
    if not tokens:
        return None
    return " ".join(tokens)


def _looks_like_action_word(token: str) -> bool:
    normalized = _normalize(token)
    if not normalized:
        return False
    if normalized in ACTION_CUE_WORDS:
        return True
    return normalized.endswith("ed") or normalized.endswith("ing")


def _extract_surface_before_verb(text: str) -> Optional[str]:
    tail = str(text or "").strip(" ,;:-\u2014\u2013")
    if not tail:
        return None

    tail = re.split(r"[.!?]\s+", tail)[-1].strip()
    if not tail:
        return None

    leading_subject_match = re.search(
        r"^([A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*){0,3})\s+"
        r"[a-z][A-Za-z'\-]*\b",
        tail,
    )
    if leading_subject_match and (
        re.search(r"\b(?:he|she|they)\s*$", tail, re.IGNORECASE)
        or " as " not in tail.lower()
    ):
        return _trim_surface(leading_subject_match.group(1))

    role_of_name_match = re.search(
        r"((?:the|a|an)\s+[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,3})\s+of\s+"
        r"(?:(?:the|a|an)\s+)?[A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*){0,3}$",
        tail,
    )
    if role_of_name_match:
        return _trim_surface(role_of_name_match.group(1))

    appositive_match = re.search(
        r"([A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*){0,3})\s*,\s*[^.!?]{0,80}$",
        tail,
    )
    if appositive_match:
        return _trim_surface(appositive_match.group(1))

    proper_name_match = re.search(
        r"([A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*){0,3})"
        r"(?:\s+(?:again|calmly|just|only|openly|quietly|sharply|simply|slowly|softly|still))*$",
        tail,
    )
    if proper_name_match:
        return _trim_surface(proper_name_match.group(1))

    role_phrase_match = re.search(
        r"((?:the|a|an)\s+[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){0,6})"
        r"(?:\s+(?:again|calmly|just|only|openly|quietly|sharply|simply|slowly|softly|still))*$",
        tail,
        re.IGNORECASE,
    )
    if role_phrase_match:
        return _trim_surface(role_phrase_match.group(1))

    return _extract_subject_before(tail)


def _extract_surface_after_verb(text: str) -> Optional[str]:
    head = str(text or "").strip(" ,;:-\u2014\u2013")
    if not head:
        return None

    head = re.split(r"[.!?]", head)[0].strip()
    if not head:
        return None

    for pattern in (
        r"^([A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*){0,3})",
        r"^((?:the|a|an)\s+[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){0,6})",
        r"^(he|she|they)",
    ):
        match = re.search(pattern, head, re.IGNORECASE)
        if match:
            return _trim_surface(match.group(1))

    return _extract_subject_after(head)


def _extract_surface_without_verb(text: str) -> Optional[str]:
    head = str(text or "").strip(" ,;:-\u2014\u2013")
    if not head:
        return None

    head = re.split(r"[.!?]", head)[0].strip()
    if not head:
        return None

    proper_action_match = re.match(
        r"^([A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*){0,3})\s+([a-z][A-Za-z'\-]*)\b",
        head,
    )
    if proper_action_match and _looks_like_action_word(proper_action_match.group(2)):
        return _trim_surface(proper_action_match.group(1))

    match = re.match(
        r"^((?:[A-Z][A-Za-z'’\-]*|(?:the|a|an))(?:\s+[A-Za-z][A-Za-z'’\-]*){0,5})\s+([A-Za-z][A-Za-z'’\-]*)\b",
        head,
        re.IGNORECASE,
    )
    if match and _looks_like_action_word(match.group(2)):
        return _trim_surface(match.group(1))

    return None


def _is_generic_placeholder(character: DialogueAiCharacterRef) -> bool:
    normalized_id = _normalize(character.characterId)
    normalized_name = _normalize(character.name)
    return normalized_id.startswith("unknown") or normalized_name.startswith("unknown")


def extract_attribution_cue(text: str, known_surfaces: Sequence[str]) -> Dict[str, Any]:
    """Extract the strongest local attribution cue around a quote boundary."""

    raw_text = str(text or "").strip()
    if not raw_text:
        return {
            "cueText": raw_text,
            "speakerSurface": None,
            "speakerNameMatch": None,
            "cueVerb": None,
            "cueVerbType": None,
            "hasSpeechVerb": False,
        }

    matches = list(re.finditer(rf"\b({ATTRIBUTION_VERB_REGEX})\b", raw_text, re.IGNORECASE))
    if not matches:
        return {
            "cueText": raw_text,
            "speakerSurface": _extract_surface_without_verb(raw_text),
            "speakerNameMatch": infer_sentence_attributed_speaker(raw_text, known_surfaces),
            "cueVerb": None,
            "cueVerbType": None,
            "hasSpeechVerb": detect_nearby_speech_verb(raw_text),
        }

    best = matches[0]
    verb = best.group(1)
    subject_before = _extract_surface_before_verb(raw_text[: best.start()])
    subject_after = _extract_surface_after_verb(raw_text[best.end() :])
    speaker_surface = subject_before or subject_after
    speaker_name_match = infer_sentence_attributed_speaker(raw_text, known_surfaces)

    return {
        "cueText": raw_text,
        "speakerSurface": speaker_surface,
        "speakerNameMatch": speaker_name_match,
        "cueVerb": verb,
        "cueVerbType": _verb_type(verb),
        "hasSpeechVerb": detect_nearby_speech_verb(raw_text),
    }


def extract_quote_structure(
    paragraph_text: str,
    known_surfaces: Sequence[str],
) -> List[Dict[str, Any]]:
    """Split a paragraph into quote units with nearby speaker cues."""

    text = str(paragraph_text or "")
    if not text:
        return []

    matches = list(QUOTE_PATTERN.finditer(text))
    if not matches:
        return []

    units: List[Dict[str, Any]] = []
    for index, match in enumerate(matches):
        quote_text = match.group(1).strip()
        previous_end = matches[index - 1].end() if index > 0 else 0
        next_start = (
            matches[index + 1].start() if index + 1 < len(matches) else len(text)
        )
        prefix = text[previous_end:match.start()].strip()
        suffix = text[match.end() : next_start].strip()
        prefix_cue = extract_attribution_cue(prefix, known_surfaces)
        suffix_cue = extract_attribution_cue(suffix, known_surfaces)

        chosen_position = "none"
        chosen_cue = prefix_cue
        if (
            suffix_cue.get("speakerSurface")
            or suffix_cue.get("speakerNameMatch")
            or suffix_cue.get("cueVerb")
        ):
            chosen_position = "after"
            chosen_cue = suffix_cue
        elif (
            prefix_cue.get("speakerSurface")
            or prefix_cue.get("speakerNameMatch")
            or prefix_cue.get("cueVerb")
        ):
            chosen_position = "before"
            chosen_cue = prefix_cue

        units.append(
            {
                "quoteIndex": index,
                "quoteText": quote_text,
                "cuePosition": chosen_position,
                "cueVerb": chosen_cue.get("cueVerb"),
                "cueVerbType": chosen_cue.get("cueVerbType"),
                "cueText": chosen_cue.get("cueText"),
                "speakerSurface": chosen_cue.get("speakerSurface"),
                "speakerNameMatch": chosen_cue.get("speakerNameMatch"),
                "mentionedEntities": extract_explicit_mentions(quote_text, known_surfaces),
                "addressedEntity": infer_addressed_name(
                    extract_last_sentence(quote_text),
                    known_surfaces,
                ),
                "firstSentence": extract_first_sentence(quote_text),
                "lastSentence": extract_last_sentence(quote_text),
            }
        )

    return units


def build_scene_alias_memory(
    chapter_text: str,
    chapter_characters: Sequence[DialogueAiCharacterRef],
    book_characters: Sequence[DialogueAiCharacterRef],
) -> Dict[str, Dict[str, Any]]:
    """Infer scene-local alias surfaces from appositive narrator text."""

    text = str(chapter_text or "")
    if not text:
        return {}

    chapter_ids = {item.characterId for item in chapter_characters}
    combined = list(chapter_characters) + [
        character
        for character in book_characters
        if character.characterId not in chapter_ids
    ]
    scene_map: Dict[str, Dict[str, Any]] = {}

    for character in combined:
        for raw_name in [character.name, *(character.aliases or [])]:
            normalized_name = _normalize(raw_name)
            if not normalized_name:
                continue
            name_pattern = re.escape(str(raw_name))
            patterns = [
                (
                    rf"\b{name_pattern}\b\s*,\s*the\s+"
                    r"([A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){0,4})"
                ),
                (
                    r"the\s+([A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){0,4})"
                    rf"\s*,\s*\b{name_pattern}\b"
                ),
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    surface = _strip_article_prefix(match.group(1))
                    normalized_surface = _normalize(surface)
                    if not normalized_surface:
                        continue
                    scene_map[normalized_surface] = {
                        "characterId": character.characterId,
                        "characterName": character.name,
                        "evidence": "appositive_match",
                        "surface": surface,
                    }

    return scene_map


def resolve_surface_identity(
    surface: Optional[str],
    chapter_characters: Sequence[DialogueAiCharacterRef],
    book_characters: Sequence[DialogueAiCharacterRef],
    scene_alias_memory: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Rank candidate characters that plausibly match a role or title surface."""

    normalized_surface = _normalize(surface)
    if not normalized_surface:
        return []

    scene_alias_memory = scene_alias_memory or {}
    if normalized_surface in scene_alias_memory:
        mapped = scene_alias_memory[normalized_surface]
        return [
            {
                "characterId": mapped["characterId"],
                "characterName": mapped["characterName"],
                "score": 0.99,
                "evidence": [mapped.get("evidence") or "scene_alias_memory"],
            }
        ]

    combined: List[DialogueAiCharacterRef] = []
    seen_ids: set[str] = set()
    for character in list(chapter_characters) + list(book_characters):
        if character.characterId in seen_ids:
            continue
        seen_ids.add(character.characterId)
        combined.append(character)

    stripped_surface = _strip_article_prefix(_strip_title_prefix(normalized_surface))
    stripped_tokens = set(_tokenize(stripped_surface))
    ranked: List[Dict[str, Any]] = []

    for character in combined:
        labels = _character_role_labels(character)
        evidence: List[str] = []
        score = 0.0

        if normalized_surface in labels:
            score = max(score, 0.98)
            evidence.append("exact_label_match")
        if stripped_surface and stripped_surface in labels:
            score = max(score, 0.94)
            evidence.append("stripped_label_match")

        label_tokens = set()
        for label in labels:
            label_tokens.update(_tokenize(label))
        token_overlap = stripped_tokens & label_tokens
        if stripped_tokens and token_overlap:
            overlap_ratio = len(token_overlap) / max(1, len(stripped_tokens))
            token_score = 0.45 + (0.35 * overlap_ratio)
            score = max(score, token_score)
            evidence.append(f"token_overlap:{'/'.join(sorted(token_overlap))}")

        if score > 0.0 and _is_generic_placeholder(character):
            score *= 0.7
            evidence.append("generic_placeholder_penalty")

        if score <= 0.0:
            continue
        ranked.append(
            {
                "characterId": character.characterId,
                "characterName": character.name,
                "score": round(min(score, 0.99), 4),
                "evidence": evidence,
            }
        )

    ranked.sort(key=lambda item: (item["score"], item["characterName"]), reverse=True)
    return ranked[:5]


def build_candidate_cards(
    current_character_id: Optional[str],
    line_candidates: Sequence[Dict[str, Any]],
    chapter_characters: Sequence[DialogueAiCharacterRef],
    book_characters: Sequence[DialogueAiCharacterRef],
    recent_turn_history: Sequence[Dict[str, Any]],
    quote_structure: Sequence[Dict[str, Any]],
    scene_alias_memory: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build compact candidate summaries for the model to compare directly."""

    candidate_ids: List[str] = []
    for candidate_id in [
        current_character_id,
        *(item.get("characterId") for item in line_candidates),
    ]:
        normalized = str(candidate_id or "").strip()
        if normalized and normalized not in candidate_ids:
            candidate_ids.append(normalized)

    recent_ids = [str(item.get("characterId") or "").strip() for item in recent_turn_history]
    for candidate_id in recent_ids:
        if candidate_id and candidate_id not in candidate_ids:
            candidate_ids.append(candidate_id)

    for quote_unit in quote_structure:
        surface = quote_unit.get("speakerNameMatch") or quote_unit.get("speakerSurface")
        for resolved in resolve_surface_identity(
            surface,
            chapter_characters,
            book_characters,
            scene_alias_memory,
        ):
            candidate_id = str(resolved.get("characterId") or "").strip()
            if candidate_id and candidate_id not in candidate_ids:
                candidate_ids.append(candidate_id)

    by_id = {
        character.characterId: character
        for character in list(chapter_characters) + list(book_characters)
    }
    candidate_cards: List[Dict[str, Any]] = []
    for candidate_id in candidate_ids[:8]:
        character = by_id.get(candidate_id)
        if character is None:
            continue
        role_labels = _character_role_labels(character)
        candidate_cards.append(
            {
                "characterId": character.characterId,
                "characterName": character.name,
                "aliases": list(character.aliases or []),
                "roleLabels": role_labels[:8],
                "isCurrentAssignment": character.characterId == current_character_id,
                "wasRecentSpeaker": character.characterId in recent_ids,
            }
        )

    return candidate_cards


def extract_attribution_signals(
    current_paragraph_text: str,
    previous_paragraph_text: Optional[str],
    next_paragraph_text: Optional[str],
    known_surfaces: Sequence[str],
) -> Dict[str, Any]:
    """Summarize parser-backed verb and mention signals for a paragraph."""

    current_text = str(current_paragraph_text or "")
    previous_text = str(previous_paragraph_text or "")
    next_text = str(next_paragraph_text or "")
    current_structure = extract_quote_structure(current_text, known_surfaces)
    verb_matches = re.findall(
        rf"\b({ATTRIBUTION_VERB_REGEX})\b",
        current_text,
        re.IGNORECASE,
    )

    return {
        "currentParagraphHasSpeechVerb": detect_nearby_speech_verb(current_text),
        "previousParagraphHasSpeechVerb": detect_nearby_speech_verb(previous_text),
        "nextParagraphHasSpeechVerb": detect_nearby_speech_verb(next_text),
        "recentNamedMentionBeforeQuote": infer_recent_named_mention(previous_text, known_surfaces),
        "previousParagraphNamedMention": infer_previous_paragraph_named_mention(
            previous_text,
            known_surfaces,
        ),
        "nextSentenceAttributedSpeaker": infer_sentence_attributed_speaker(
            next_text,
            known_surfaces,
        ),
        "quoteStructure": current_structure,
        "speechVerbCount": sum(
            1 for item in verb_matches if _normalize(item) in SPEECH_VERBS
        ),
        "reactionVerbCount": sum(
            1 for item in verb_matches if _normalize(item) in REACTION_VERBS
        ),
    }


def build_recent_turn_history(
    targets: Sequence[Dict[str, Any]],
    current_index: int,
    chapter_characters: Sequence[DialogueAiCharacterRef],
    backward_limit: int = 4,
    forward_limit: int = 2,
) -> List[Dict[str, Any]]:
    """Capture nearby dialogue turns to expose turn-taking continuity."""

    history: List[Dict[str, Any]] = []
    start = max(0, current_index - backward_limit)
    end = min(len(targets), current_index + forward_limit + 1)
    for index in range(start, end):
        if index == current_index:
            continue
        line = targets[index]["line"]
        character_name = next(
            (
                character.name
                for character in chapter_characters
                if character.characterId == line.characterId
            ),
            str(line.characterId or "").replace("_", " ").title(),
        )
        history.append(
            {
                "relativeIndex": index - current_index,
                "lineId": line.id,
                "paragraphIndex": targets[index].get("paragraphIndex"),
                "characterId": line.characterId,
                "characterName": character_name,
                "text": line.text,
            }
        )
    return history
