import json
import hashlib
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx

from api_models import (
    DialogueAiAliasProposal,
    DialogueAiAssistRequest,
    DialogueAiAssistResponse,
    DialogueAiAssistStatus,
    DialogueAiAssistSummary,
    DialogueAiCharacterRef,
    DialogueAiLineResult,
    DialogueAiNewCharacterProposal,
    DialogueAiToolTraceEntry,
)
from dialogue_ai_context import build_assist_context
from dialogue_ai_enrichment import (
    build_candidate_cards,
    extract_attribution_cue,
    extract_attribution_signals,
    resolve_surface_identity,
)


DEFAULT_MODEL_NAME = "gemini-3-flash-preview"
ENV_FILE_PATH = Path(__file__).with_name(".env")
AI_CACHE_DIR = Path(__file__).with_name(".cache") / "dialogue_ai"
AI_LOG_DIR = Path(__file__).with_name("logs") / "dialogue_ai"
UNKNOWN_CHARACTER_NAME = "Unknown"
SYSTEM_PROMPT = """You validate speaker attribution for dialogue lines in a novel.

Return JSON only.

You may either:
1. ask for one extra context tool using {\"responseType\":\"tool\",\"tool\":...}
2. return a final decision using {\"responseType\":\"final\",...}

Allowed tool names:
- request_previous_paragraph
- request_next_paragraph
- request_book_characters
- request_quote_structure
- request_recent_turn_history
- request_scene_entities
- request_candidate_cards
- request_attribution_signals

Final response schema:
{
  \"responseType\": \"final\",
  \"action\": \"keep_existing\" | \"reassign_existing\" | \"needs_review\" | \"propose_alias\" | \"propose_new_character\",
  \"characterId\": string | null,
  \"characterName\": string | null,
  \"confidence\": number,
  \"reasonCodes\": string[],
  \"aliasToAdd\": {\"characterId\": string, \"characterName\": string, \"alias\": string} | null,
  \"newCharacterProposal\": {\"name\": string, \"alias\": string | null, \"notes\": string | null} | null
}

Rules:
- Prefer keep_existing or reassign_existing to an existing chapter or book character.
- Use propose_alias only when the spoken surface form is clearly an alias for an existing canonical character.
- Use propose_new_character rarely.
- Treat parser-provided speaker labels as provisional evidence, not ground truth.
- Do not confuse the addressee with the speaker. Names or titles used at the start of a quote often identify who is being spoken to, not who is speaking.
- In negotiations or arguments, prefer continuity across the current paragraph's dialogue sequence before reassigning to a newly mentioned character.
- Explicit narration cues such as "X said", "the head priest raised his hands", or "the Augur nodded" are strong speaker evidence.
- Prefer resolving role and title surfaces to an existing character before proposing a new character.
- If the evidence mainly comes from who is being threatened, addressed, or discussed, that is weak evidence for speaker identity.
- If multiple plausible speakers remain after reading the current paragraph dialogue sequence, request more context instead of guessing.
- If the speaker remains genuinely ambiguous after available context, return needs_review with characterId null and characterName "Unknown"; unknown is better than a likely-wrong named speaker.
- Do not explain in prose outside JSON.
- Keep reasonCodes short, snake_case, and few.
"""


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _load_local_env_file() -> None:
    if not ENV_FILE_PATH.exists():
        return

    for raw_line in ENV_FILE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key:
            continue
        if (
            len(normalized_value) >= 2
            and normalized_value[0] == normalized_value[-1]
            and normalized_value[0] in {'"', "'"}
        ):
            normalized_value = normalized_value[1:-1]
        os.environ.setdefault(normalized_key, normalized_value)


def _get_model_name() -> str:
    configured = (
        os.getenv("OPENBOOK_DIALOGUE_AI_MODEL")
        or os.getenv("GEMINI_MODEL")
        or DEFAULT_MODEL_NAME
    )
    return configured.strip() or DEFAULT_MODEL_NAME


def _get_model_endpoint(model_name: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"


def _get_model_api_key() -> Optional[str]:
    _load_local_env_file()
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    normalized = api_key.strip()
    return normalized or None


def _clamp_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric < 0:
        return 0.0
    if numeric > 1:
        return 1.0
    return numeric


def _character_name(character_id: Optional[str], chapter_characters: List[DialogueAiCharacterRef]) -> Optional[str]:
    if not character_id:
        return None
    for character in chapter_characters:
        if character.characterId == character_id:
            return character.name
    return character_id.replace("_", " ").replace("-", " ").title()


def _serialize_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _slugify_fragment(value: Optional[str]) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "chapter"


def _normalize_character_lookup(value: Optional[str]) -> str:
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_title_prefix(value: str) -> str:
    tokens = [token for token in str(value or "").split(" ") if token]
    while len(tokens) > 1:
        if len(tokens) > 2 and tokens[0] in {"the", "a", "an"} and tokens[1] in {"king", "queen", "lord", "lady", "sir", "head", "high"}:
            tokens = [tokens[0], *tokens[2:]]
            continue
        if tokens[0] in {"king", "queen", "lord", "lady", "sir", "head", "high"}:
            tokens = tokens[1:]
            continue
        break
    return " ".join(tokens)


def _timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _hash_payload(model_name: str, payload: Dict[str, Any]) -> str:
    serialized = _serialize_json(
        {
            "modelName": model_name,
            "systemPrompt": SYSTEM_PROMPT,
            "payload": payload,
        }
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _get_character_ref(
    character_id: Optional[str],
    chapter_characters: List[DialogueAiCharacterRef],
) -> Optional[DialogueAiCharacterRef]:
    if not character_id:
        return None
    for character in chapter_characters:
        if character.characterId == character_id:
            return character
    return None


def _canonicalize_suggested_character(
    character_id: Optional[str],
    character_name: Optional[str],
    book_characters: List[DialogueAiCharacterRef],
) -> Tuple[Optional[str], Optional[str]]:
    if not character_id:
        return character_id, character_name

    by_id = {character.characterId: character for character in book_characters}
    if character_id in by_id:
        canonical = by_id[character_id]
        return canonical.characterId, canonical.name

    lookup: Dict[str, DialogueAiCharacterRef] = {}
    for character in book_characters:
        keys = {
            _normalize_character_lookup(character.characterId),
            _normalize_character_lookup(character.name),
            *(_normalize_character_lookup(alias) for alias in character.aliases),
        }
        for key in keys:
            if key:
                lookup.setdefault(key, character)

    for candidate in (
        _normalize_character_lookup(character_id),
        _normalize_character_lookup(character_name),
    ):
        if not candidate:
            continue
        if candidate in lookup:
            canonical = lookup[candidate]
            return canonical.characterId, canonical.name
        stripped_candidate = _strip_title_prefix(candidate)
        if stripped_candidate in lookup:
            canonical = lookup[stripped_candidate]
            return canonical.characterId, canonical.name

    return character_id, character_name


def _extract_tool_name(decision: Dict[str, Any]) -> str:
    raw_tool = decision.get("tool")
    if isinstance(raw_tool, str):
        return raw_tool.strip()
    if isinstance(raw_tool, dict):
        return str(raw_tool.get("name") or "").strip()
    return ""


def _decision_trace_signals(line: Any) -> Optional[Dict[str, Any]]:
    attribution = getattr(line, "attribution", None)
    decision_trace = getattr(attribution, "decisionTrace", None) if attribution else None
    if decision_trace is None:
        return None
    signals = getattr(decision_trace, "signals", None)
    if isinstance(signals, dict):
        return signals
    return None


def _current_line_position(target: Dict[str, Any], line_id: int) -> Optional[int]:
    return next(
        (
            index
            for index, candidate in enumerate(target.get("currentParagraphLines") or [])
            if candidate.id == line_id
        ),
        None,
    )


def _normalize_quote_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("’", "'")
    text = re.sub(r"[\"“”]", "", text)
    text = re.sub(r"[^a-zA-Z0-9']+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _quote_text_matches_line(line_text: Any, quote_unit: Optional[Dict[str, Any]]) -> bool:
    if not quote_unit:
        return False
    normalized_line = _normalize_quote_text(line_text)
    normalized_quote = _normalize_quote_text(quote_unit.get("quoteText"))
    return bool(normalized_line and normalized_quote and normalized_line == normalized_quote)


def _iter_target_quote_units(target: Dict[str, Any]) -> List[Dict[str, Any]]:
    quote_units: List[Dict[str, Any]] = []
    for key in ("currentQuoteStructure", "previousQuoteStructure", "nextQuoteStructure"):
        for unit in target.get(key) or []:
            if isinstance(unit, dict):
                quote_units.append(unit)
    return quote_units


def _current_quote_unit(target: Dict[str, Any], line: Any) -> Optional[Dict[str, Any]]:
    position = _current_line_position(target, line.id)
    quote_units = list(target.get("currentQuoteStructure") or [])
    if position is not None and 0 <= position < len(quote_units):
        candidate = quote_units[position]
        if _quote_text_matches_line(line.text, candidate):
            return candidate

    for candidate in _iter_target_quote_units(target):
        if _quote_text_matches_line(line.text, candidate):
            return candidate

    if position is not None and 0 <= position < len(quote_units):
        return quote_units[position]
    return None


def _line_ends_with_continuation_punctuation(text: Any) -> bool:
    normalized = str(text or "").rstrip()
    if not normalized:
        return False
    return normalized.endswith(",") or normalized.endswith(":") or normalized.endswith(";") or normalized.endswith("-") or normalized.endswith("—")


def _is_pronoun_surface(surface: Any) -> bool:
    return _normalize_character_lookup(surface) in {"he", "she", "they"}


def _is_generic_surface(surface: Any) -> bool:
    normalized = _normalize_character_lookup(surface)
    generic_role_nouns = {
        "god",
        "goddess",
        "man",
        "mage",
        "old man",
        "primordial",
        "priest",
        "the man",
        "the god",
        "the goddess",
        "the mage",
        "the old man",
        "the primordial",
        "the priest",
        "the woman",
        "woman",
    }
    if normalized in generic_role_nouns:
        return True
    tokens = normalized.split(" ")
    if len(tokens) >= 2 and tokens[0] in {"other", "another"}:
        return True
    if len(tokens) >= 3 and tokens[0] == "the" and tokens[1] in {"other", "another"}:
        return True
    return bool(" of " in f" {normalized} ")


def _is_generic_character_id(character_id: Optional[str]) -> bool:
    normalized = str(character_id or "").strip().lower()
    return normalized in {"unknown", "primordial"} or normalized.startswith("unknown-")


def _alignment_is_uncertain(target: Dict[str, Any]) -> bool:
    alignment = target.get("alignment") or {}
    if bool(alignment.get("isSpanFallback")):
        return True
    try:
        return float(alignment.get("confidence") or 0.0) < 0.75
    except (TypeError, ValueError):
        return True


def _known_character_surfaces(assist_context: Dict[str, Any]) -> List[str]:
    surfaces: List[str] = []
    seen: set[str] = set()
    for character in [*assist_context["chapterCharacters"], *assist_context["bookCharacters"]]:
        for raw_value in [character.characterId, character.name, *(character.aliases or [])]:
            text = str(raw_value or "").strip()
            normalized = _normalize_character_lookup(text)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            surfaces.append(text)
    return surfaces


def _quote_unit_has_local_cue(quote_unit: Optional[Dict[str, Any]]) -> bool:
    if not quote_unit:
        return False
    return any(
        bool(str(quote_unit.get(key) or "").strip())
        for key in ("cueText", "speakerSurface", "speakerNameMatch", "cueVerb")
    )


def _resolve_surface_match(
    surface: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    normalized_surface = _normalize_character_lookup(surface)
    if normalized_surface in {"", "he", "she", "they"}:
        return None

    matches = resolve_surface_identity(
        surface,
        assist_context["chapterCharacters"],
        assist_context["bookCharacters"],
        run_state.get("sceneAliasMemory"),
    )
    if not matches:
        return None

    top_match = matches[0]
    top_score = float(top_match.get("score") or 0.0)
    if top_score < 0.78:
        return None
    if len(matches) > 1 and float(matches[1].get("score") or 0.0) >= top_score - 0.08:
        return None

    character_id = str(top_match.get("characterId") or "").strip()
    if not character_id:
        return None
    if _is_generic_character_id(character_id) and _is_generic_surface(surface):
        return None
    character_name = str(top_match.get("characterName") or "").strip() or _character_name(character_id, assist_context["chapterCharacters"])
    return character_id, character_name, top_score


def _resolve_cue_text_match(
    quote_unit: Optional[Dict[str, Any]],
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    if not quote_unit:
        return None

    cue_text = str(quote_unit.get("cueText") or "").strip()
    if not cue_text:
        return None

    extracted = extract_attribution_cue(
        cue_text,
        known_surfaces=_known_character_surfaces(assist_context),
    )
    surface = extracted.get("speakerNameMatch") or extracted.get("speakerSurface")
    resolved = _resolve_surface_match(surface, assist_context, run_state)
    if resolved is not None:
        return resolved

    cue_tokens = re.findall(r"[A-Za-z'’\-]+", cue_text)
    if cue_tokens and cue_tokens[0].lower() in {"the", "a", "an"}:
        max_prefix_length = min(4, len(cue_tokens))
        for prefix_length in range(max_prefix_length, 1, -1):
            candidate_surface = " ".join(cue_tokens[:prefix_length])
            resolved = _resolve_surface_match(candidate_surface, assist_context, run_state)
            if resolved is not None:
                return resolved

    return None


def _quote_unit_resolved_speaker(
    quote_unit: Optional[Dict[str, Any]],
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    if not quote_unit:
        return None
    speaker_surface = quote_unit.get("speakerSurface")
    speaker_name_match = quote_unit.get("speakerNameMatch")
    surface = speaker_surface
    if _is_pronoun_surface(surface) or not str(surface or "").strip():
        surface = speaker_name_match
    resolved = _resolve_surface_match(surface, assist_context, run_state)
    if resolved is not None:
        return resolved
    return _resolve_cue_text_match(quote_unit, assist_context, run_state)


def _self_identified_quote_speaker(
    quote_unit: Optional[Dict[str, Any]],
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    if not quote_unit:
        return None

    quote_text = str(quote_unit.get("quoteText") or "").strip()
    if not quote_text:
        return None

    for pattern in (
        r"^you are speaking to\s+(.+?)(?:[,.!?]|$)",
        r"^i am\s+(.+?)(?:[,.!?]|$)",
        r"^this is\s+(.+?)(?:[,.!?]|$)",
    ):
        match = re.match(pattern, quote_text, re.IGNORECASE)
        if not match:
            continue
        resolved = _resolve_surface_match(match.group(1), assist_context, run_state)
        if resolved is not None:
            return resolved

    return None


def _self_identified_text_speaker(
    text: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    quote_unit = {"quoteText": str(text or "").strip()}
    return _self_identified_quote_speaker(quote_unit, assist_context, run_state)


def _same_paragraph_neighbor_speaker(
    target: Dict[str, Any],
    line: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    position = _current_line_position(target, line.id)
    quote_units = list(target.get("currentQuoteStructure") or [])
    if position is None or not 0 <= position < len(quote_units):
        return None

    current_unit = quote_units[position]
    if _quote_unit_has_local_cue(current_unit):
        return None

    for neighbor_index in (position - 1, position + 1):
        if not 0 <= neighbor_index < len(quote_units):
            continue
        neighbor_unit = quote_units[neighbor_index]
        if not _quote_unit_has_local_cue(neighbor_unit):
            continue
        resolved = _quote_unit_resolved_speaker(neighbor_unit, assist_context, run_state)
        if resolved is not None:
            return resolved

    return None


def _paragraph_has_resolved_local_cue(
    target: Dict[str, Any],
    line: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> bool:
    position = _current_line_position(target, line.id)
    quote_units = list(target.get("currentQuoteStructure") or [])
    for index, quote_unit in enumerate(quote_units):
        if position is not None and index == position:
            continue
        if not _quote_unit_has_local_cue(quote_unit):
            continue
        if _quote_unit_resolved_speaker(quote_unit, assist_context, run_state) is not None:
            return True
    return False


def _adjacent_paragraph_explicit_speaker(
    paragraph_text: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    return _adjacent_narration_speaker(paragraph_text, assist_context, run_state)


def _neighbor_named_dialogue_anchor(
    target: Dict[str, Any],
    line: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    quote_unit = _current_quote_unit(target, line)
    if quote_unit is None:
        return None

    current_surface = quote_unit.get("speakerNameMatch") or quote_unit.get("speakerSurface")
    if not _is_generic_surface(current_surface):
        return None

    position = _current_line_position(target, line.id)
    paragraph_lines = list(target.get("currentParagraphLines") or [])
    if position is None:
        return None

    for candidate in paragraph_lines[:position]:
        candidate_text = str(getattr(candidate, "text", "") or "").strip()
        if not candidate_text:
            continue
        resolved = _resolve_surface_match(candidate_text, assist_context, run_state)
        if resolved is not None and not _is_generic_character_id(resolved[0]):
            return resolved
    return None


def _question_reply_sandwich_speaker(
    target: Dict[str, Any],
) -> Optional[Tuple[str, str]]:
    previous_lines = list(target.get("previousParagraphLines") or [])
    if not previous_lines:
        return None

    previous_line = previous_lines[-1]
    if not str(getattr(previous_line, "text", "") or "").rstrip().endswith("?"):
        return None

    previous_speaker_id = str(getattr(previous_line, "characterId", "") or "").strip()
    if not previous_speaker_id:
        return None

    history = sorted(
        [
            item
            for item in (target.get("recentTurnHistory") or [])
            if int(item.get("relativeIndex") or 0) < 0
        ],
        key=lambda item: abs(int(item.get("relativeIndex") or 0)),
    )
    for item in history:
        candidate_id = str(item.get("characterId") or "").strip()
        if not candidate_id or candidate_id == previous_speaker_id:
            continue
        return candidate_id, str(item.get("characterName") or "").strip() or candidate_id
    return None


def _orphan_quote_followed_by_narration_speaker(
    target: Dict[str, Any],
    line: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    quote_unit = _current_quote_unit(target, line)
    if quote_unit is None or _quote_unit_has_local_cue(quote_unit):
        return None
    if _alignment_is_uncertain(target):
        return None

    if target.get("nextParagraphDialogue"):
        return None

    next_paragraph_text = str(target.get("nextParagraphText") or "").strip()
    if not next_paragraph_text or '"' in next_paragraph_text or "“" in next_paragraph_text:
        return None

    extracted = extract_attribution_cue(
        next_paragraph_text,
        known_surfaces=_known_character_surfaces(assist_context),
    )
    if not extracted.get("cueVerb"):
        return None

    return _adjacent_paragraph_explicit_speaker(
        next_paragraph_text,
        assist_context,
        run_state,
    )


def _previous_narrator_carryover_speaker(
    target: Dict[str, Any],
    line: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    quote_unit = _current_quote_unit(target, line)
    if quote_unit is None or _quote_unit_has_local_cue(quote_unit):
        return None
    if _alignment_is_uncertain(target):
        return None

    previous_speaker = _adjacent_paragraph_explicit_speaker(
        target.get("previousParagraphText"),
        assist_context,
        run_state,
    )
    if previous_speaker is None:
        return None

    previous_lines = list(target.get("previousParagraphLines") or [])
    previous_line = previous_lines[-1] if previous_lines else None
    if previous_line is not None:
        if not _line_ends_with_continuation_punctuation(getattr(previous_line, "text", "")):
            return None

    next_quote_units = list(target.get("nextQuoteStructure") or [])
    next_quote_unit = next_quote_units[0] if next_quote_units else None
    next_speaker = _quote_unit_resolved_speaker(next_quote_unit, assist_context, run_state)
    if next_speaker is not None and next_speaker[0] == previous_speaker[0]:
        return None

    if _paragraph_has_resolved_local_cue(target, line, assist_context, run_state):
        return None

    return previous_speaker


def _resolved_surface_mentions(
    text: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> List[Tuple[int, str, str, float]]:
    raw_text = str(text or "")
    if not raw_text:
        return []

    pattern = re.compile(
        r"\b(?:[A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*){0,3}|(?:the|a|an)\s+[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){0,5})\b"
    )
    mentions: List[Tuple[int, str, str, float]] = []
    seen: set[Tuple[int, str]] = set()
    for match in pattern.finditer(raw_text):
        candidate = match.group(0).strip(" ,.;:!?()[]{}\"“”")
        resolved = _resolve_surface_match(candidate, assist_context, run_state)
        if resolved is None or _is_generic_character_id(resolved[0]):
            continue
        key = (match.start(), resolved[0])
        if key in seen:
            continue
        seen.add(key)
        mentions.append((match.start(), resolved[0], resolved[1], resolved[2]))
    return mentions


def _narrative_anchor_speaker(
    request: DialogueAiAssistRequest,
    target: Dict[str, Any],
    quote_unit: Optional[Dict[str, Any]],
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    if not quote_unit:
        return None

    current_surface = quote_unit.get("speakerNameMatch") or quote_unit.get("speakerSurface")
    if not _is_pronoun_surface(current_surface):
        return None
    if quote_unit.get("cueVerb"):
        return None

    previous_mentions = _resolved_surface_mentions(
        target.get("previousParagraphText"),
        assist_context,
        run_state,
    )
    if previous_mentions:
        _, character_id, character_name, score = previous_mentions[-1]
        return character_id, character_name, score

    next_mentions = _resolved_surface_mentions(
        target.get("nextParagraphText"),
        assist_context,
        run_state,
    )
    if next_mentions:
        _, character_id, character_name, score = next_mentions[0]
        return character_id, character_name, score

    chapter_paragraphs = [line.strip() for line in str(request.chapter_text or "").splitlines() if line.strip()]
    current_paragraph = str(target.get("currentParagraphText") or "").strip()
    if not current_paragraph:
        return None

    try:
        paragraph_index = chapter_paragraphs.index(current_paragraph)
    except ValueError:
        return None

    for candidate_index in range(paragraph_index - 1, max(-1, paragraph_index - 4), -1):
        paragraph_text = chapter_paragraphs[candidate_index]
        mentions = _resolved_surface_mentions(paragraph_text[:180], assist_context, run_state)
        if not mentions:
            continue
        _, character_id, character_name, score = mentions[0]
        return character_id, character_name, score

    return None


def _future_self_identified_speaker(
    target: Dict[str, Any],
    quote_unit: Optional[Dict[str, Any]],
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    if not quote_unit:
        return None

    current_surface = quote_unit.get("speakerNameMatch") or quote_unit.get("speakerSurface")
    if not _is_generic_surface(current_surface):
        return None

    for item in sorted(
        [entry for entry in (target.get("recentTurnHistory") or []) if int(entry.get("relativeIndex") or 0) > 0],
        key=lambda entry: int(entry.get("relativeIndex") or 0),
    ):
        if int(item.get("relativeIndex") or 0) > 3:
            break
        resolved = _self_identified_text_speaker(
            item.get("text"),
            assist_context,
            run_state,
        )
        if resolved is not None:
            return resolved

    return None


def _adjacent_narration_speaker(
    paragraph_text: Any,
    assist_context: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Optional[Tuple[str, str, float]]:
    text = str(paragraph_text or "").strip()
    if not text:
        return None
    cue = extract_attribution_cue(
        text,
        known_surfaces=_known_character_surfaces(assist_context),
    )
    surface = cue.get("speakerSurface")
    if _is_pronoun_surface(surface) or not str(surface or "").strip():
        surface = cue.get("speakerNameMatch")
    return _resolve_surface_match(surface, assist_context, run_state)


def _nearest_recent_speaker(
    target: Dict[str, Any],
    excluded_ids: Sequence[str],
) -> Optional[Tuple[str, str]]:
    excluded = {str(value or "").strip() for value in excluded_ids if str(value or "").strip()}
    history = sorted(
        [item for item in (target.get("recentTurnHistory") or []) if int(item.get("relativeIndex") or 0) < 0],
        key=lambda item: abs(int(item.get("relativeIndex") or 0)),
    )
    for item in history:
        character_id = str(item.get("characterId") or "").strip()
        if not character_id or character_id in excluded or character_id == "narrator":
            continue
        return character_id, str(item.get("characterName") or "").strip() or character_id
    return None


def _should_include_adjacent_context(line: Any, target: Dict[str, Any]) -> bool:
    paragraph_lines = target.get("currentParagraphLines") or []
    if len(paragraph_lines) > 1:
        return False
    if not (target.get("previousParagraphText") or target.get("nextParagraphText")):
        return False
    attribution = getattr(line, "attribution", None)
    if attribution is None:
        return True
    if attribution.misattributionRisk >= 0.12:
        return True
    if attribution.topCandidateConfidence <= 0.86:
        return True
    if attribution.marginToSecond <= 0.4:
        return True
    if len(attribution.candidates or []) <= 1:
        return True
    return False


def _format_dialogue_line_context(
    line: Any,
    chapter_characters: List[DialogueAiCharacterRef],
) -> Dict[str, Any]:
    return {
        "lineId": line.id,
        "text": line.text,
        "characterId": line.characterId,
        "characterName": _character_name(line.characterId, chapter_characters),
        "isReturning": bool(line.isReturning),
    }


class DialogueAiAssistService:
    def __init__(self, get_book_root: Callable[[], str]):
        self._get_book_root = get_book_root
        self._status_lock = threading.Lock()
        self._run_statuses: Dict[str, Dict[str, Any]] = {}

    def prepare_status(self, request: DialogueAiAssistRequest) -> None:
        request_id = str(request.request_id or "").strip() or None
        if request_id is None:
            return

        total_lines = sum(
            1
            for line in request.dialogue.lines
            if line.characterId not in (None, "", "narrator")
        )
        self._update_status(
            request_id,
            status="queued",
            processed_lines=0,
            total_lines=total_lines,
            chapter_path=request.chapter_path,
        )

    def get_status(self, request_id: str) -> Optional[DialogueAiAssistStatus]:
        with self._status_lock:
            payload = self._run_statuses.get(str(request_id or "").strip())
            if not payload:
                return None
            return DialogueAiAssistStatus(**payload)

    def _update_status(
        self,
        request_id: Optional[str],
        *,
        status: str,
        processed_lines: int,
        total_lines: int,
        chapter_path: Optional[str] = None,
        error: Optional[str] = None,
        log_directory: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> None:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return

        now = datetime.now(timezone.utc).isoformat()
        normalized_total = max(int(total_lines or 0), 0)
        normalized_processed = max(int(processed_lines or 0), 0)

        if normalized_total > 0:
            normalized_processed = min(normalized_processed, normalized_total)
            progress_ratio = normalized_processed / normalized_total
        else:
            progress_ratio = 1.0 if status == "completed" else 0.0

        with self._status_lock:
            previous = self._run_statuses.get(normalized_request_id, {})
            self._run_statuses[normalized_request_id] = {
                "requestId": normalized_request_id,
                "status": status,
                "processedLines": normalized_processed,
                "totalLines": normalized_total,
                "progressRatio": progress_ratio,
                "startedAt": started_at or previous.get("startedAt") or now,
                "updatedAt": now,
                "chapterPath": chapter_path or previous.get("chapterPath"),
                "error": error,
                "logDirectory": log_directory or previous.get("logDirectory"),
            }

            while len(self._run_statuses) > 64:
                oldest_request_id = next(iter(self._run_statuses))
                self._run_statuses.pop(oldest_request_id, None)

    def run(self, request: DialogueAiAssistRequest) -> DialogueAiAssistResponse:
        book_root = self._get_book_root()
        assist_context = build_assist_context(book_root=book_root, chapter_text=request.chapter_text, dialogue=request.dialogue)
        targets = assist_context["targets"]
        if request.max_lines is not None and request.max_lines >= 0:
            targets = targets[: request.max_lines]

        request_id = str(request.request_id or "").strip() or None
        started_at = datetime.now(timezone.utc).isoformat()
        total_lines = len(targets)

        chapter_slug = _slugify_fragment(Path(str(request.chapter_path or request.dialogue.chapterId or "chapter")).stem)
        run_log_directory = AI_LOG_DIR / chapter_slug / _timestamp_token()
        run_log_directory.mkdir(parents=True, exist_ok=True)
        self._update_status(
            request_id,
            status="running",
            processed_lines=0,
            total_lines=total_lines,
            chapter_path=request.chapter_path,
            log_directory=str(run_log_directory.resolve()),
            started_at=started_at,
        )
        run_state: Dict[str, Any] = {
            "logDirectory": run_log_directory,
            "modelCalls": 0,
            "cacheHits": 0,
            "sceneAliasMemory": dict(assist_context.get("sceneAliasMemory") or {}),
        }

        results: List[DialogueAiLineResult] = []
        errors: List[str] = []
        try:
            for index, target in enumerate(targets, start=1):
                result = self._run_line(request, target, assist_context, run_state)
                results.append(result)
                if result.disposition == "error" and result.error:
                    errors.append(f"line {result.lineId}: {result.error}")

                self._update_status(
                    request_id,
                    status="running",
                    processed_lines=index,
                    total_lines=total_lines,
                    chapter_path=request.chapter_path,
                    log_directory=str(run_log_directory.resolve()),
                    started_at=started_at,
                )

            summary = DialogueAiAssistSummary(
                scannedLines=len(targets),
                autoApplyCount=sum(1 for result in results if result.disposition == "auto_apply"),
                reviewCount=sum(1 for result in results if result.disposition == "review"),
                aliasReviewCount=sum(1 for result in results if result.disposition == "alias_review"),
                newCharacterCount=sum(1 for result in results if result.disposition == "new_character_approval"),
                errorCount=sum(1 for result in results if result.disposition == "error"),
                skippedNarratorLines=assist_context["skippedNarratorLines"],
                cacheHits=int(run_state["cacheHits"]),
                modelCalls=int(run_state["modelCalls"]),
                logDirectory=str(run_log_directory.resolve()),
            )
            self._update_status(
                request_id,
                status="completed",
                processed_lines=total_lines,
                total_lines=total_lines,
                chapter_path=request.chapter_path,
                log_directory=str(run_log_directory.resolve()),
                started_at=started_at,
            )
            return DialogueAiAssistResponse(summary=summary, results=results, errors=errors)
        except Exception as exc:
            self._update_status(
                request_id,
                status="failed",
                processed_lines=len(results),
                total_lines=total_lines,
                chapter_path=request.chapter_path,
                error=str(exc),
                log_directory=str(run_log_directory.resolve()),
                started_at=started_at,
            )
            raise

    def _build_scene_entity_payload(
        self,
        target: Dict[str, Any],
        assist_context: Dict[str, Any],
        run_state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        seen_surfaces: List[str] = []
        for quote_unit in target.get("currentQuoteStructure") or []:
            for surface in [quote_unit.get("speakerNameMatch"), quote_unit.get("speakerSurface")]:
                normalized = _normalize_character_lookup(surface)
                if normalized and normalized not in seen_surfaces:
                    seen_surfaces.append(normalized)

        payload: List[Dict[str, Any]] = []
        for surface in seen_surfaces:
            matches = resolve_surface_identity(
                surface,
                assist_context["chapterCharacters"],
                assist_context["bookCharacters"],
                run_state.get("sceneAliasMemory"),
            )
            if not matches:
                continue
            payload.append({"surface": surface, "matches": matches})
        return payload

    def _candidate_cards(
        self,
        line: Any,
        target: Dict[str, Any],
        assist_context: Dict[str, Any],
        run_state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        line_candidates = [candidate.model_dump() for candidate in (line.attribution.candidates if line.attribution else [])]
        return build_candidate_cards(
            current_character_id=line.characterId,
            line_candidates=line_candidates,
            chapter_characters=assist_context["chapterCharacters"],
            book_characters=assist_context["bookCharacters"],
            recent_turn_history=target.get("recentTurnHistory") or [],
            quote_structure=target.get("currentQuoteStructure") or [],
            scene_alias_memory=run_state.get("sceneAliasMemory"),
        )

    def _deterministic_override_result(
        self,
        request: DialogueAiAssistRequest,
        line: Any,
        target: Dict[str, Any],
        assist_context: Dict[str, Any],
        run_state: Dict[str, Any],
    ) -> Optional[DialogueAiLineResult]:
        def build_result(
            suggested_character_id: str,
            suggested_character_name: str,
            confidence: float,
            reason_codes: List[str],
        ) -> DialogueAiLineResult:
            action = "keep_existing" if suggested_character_id == line.characterId else "reassign_existing"
            normalized_confidence = _clamp_confidence(confidence)
            normalized_reason_codes = list(reason_codes)
            if _alignment_is_uncertain(target):
                normalized_confidence = min(normalized_confidence, 0.72)
                normalized_reason_codes.append("alignment_uncertain")
            if action == "reassign_existing" and _is_generic_character_id(suggested_character_id):
                if not _is_generic_character_id(line.characterId):
                    suggested_character_id = None
                    suggested_character_name = UNKNOWN_CHARACTER_NAME
                    action = "needs_review"
                normalized_confidence = min(normalized_confidence, 0.72)
                normalized_reason_codes.append("generic_character_guard")
                normalized_reason_codes.append("ambiguous_unknown")
            return DialogueAiLineResult(
                lineId=line.id,
                paragraphIndex=target["paragraphIndex"],
                currentCharacterId=line.characterId,
                currentCharacterName=_character_name(line.characterId, assist_context["chapterCharacters"]),
                suggestedCharacterId=suggested_character_id,
                suggestedCharacterName=suggested_character_name,
                action=action,
                disposition=("auto_apply" if normalized_confidence >= request.auto_apply_threshold else "review"),
                confidence=normalized_confidence,
                reasonCodes=normalized_reason_codes,
                currentParagraphText=target["currentParagraphText"],
                previousParagraphText=target["previousParagraphText"],
                nextParagraphText=target["nextParagraphText"],
            )

        quote_unit = _current_quote_unit(target, line)
        explicit_match = _quote_unit_resolved_speaker(quote_unit, assist_context, run_state)
        if explicit_match is not None:
            suggested_character_id, suggested_character_name, top_score = explicit_match
            reason_codes = ["explicit_cue_resolution", "deterministic_override"]
            if quote_unit and quote_unit.get("cueVerb"):
                reason_codes.append(f"cue_verb_{_normalize_character_lookup(quote_unit.get('cueVerb')).replace(' ', '_')}")
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=_clamp_confidence(max(top_score, 0.9)),
                reason_codes=reason_codes,
            )

        self_identified_match = _self_identified_quote_speaker(
            quote_unit,
            assist_context,
            run_state,
        )
        if self_identified_match is not None:
            suggested_character_id, suggested_character_name, top_score = self_identified_match
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=_clamp_confidence(max(top_score, 0.95)),
                reason_codes=["self_identification", "deterministic_override"],
            )

        future_self_identified_match = _future_self_identified_speaker(
            target,
            quote_unit,
            assist_context,
            run_state,
        )
        if future_self_identified_match is not None:
            suggested_character_id, suggested_character_name, top_score = future_self_identified_match
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=_clamp_confidence(max(top_score, 0.94)),
                reason_codes=["future_self_identification", "deterministic_override"],
            )

        same_paragraph_match = _same_paragraph_neighbor_speaker(
            target,
            line,
            assist_context,
            run_state,
        )
        if same_paragraph_match is not None:
            suggested_character_id, suggested_character_name, top_score = same_paragraph_match
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=_clamp_confidence(max(top_score, 0.95)),
                reason_codes=["shared_paragraph_cue", "deterministic_override"],
            )

        generic_anchor_match = _neighbor_named_dialogue_anchor(
            target,
            line,
            assist_context,
            run_state,
        )
        if generic_anchor_match is not None:
            suggested_character_id, suggested_character_name, top_score = generic_anchor_match
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=_clamp_confidence(max(top_score, 0.93)),
                reason_codes=["generic_surface_anchor", "deterministic_override"],
            )

        narrative_anchor_match = _narrative_anchor_speaker(
            request,
            target,
            quote_unit,
            assist_context,
            run_state,
        )
        if narrative_anchor_match is not None:
            suggested_character_id, suggested_character_name, top_score = narrative_anchor_match
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=_clamp_confidence(max(top_score, 0.93)),
                reason_codes=["narrative_anchor_resolution", "deterministic_override"],
            )

        question_reply_match = _question_reply_sandwich_speaker(target)
        next_quote_units = list(target.get("nextQuoteStructure") or [])
        next_quote_unit = next_quote_units[0] if next_quote_units else None
        next_match = _quote_unit_resolved_speaker(next_quote_unit, assist_context, run_state)
        if (
            question_reply_match is not None
            and next_match is not None
            and question_reply_match[0] != next_match[0]
            and quote_unit is not None
            and not _quote_unit_has_local_cue(quote_unit)
        ):
            return build_result(
                suggested_character_id=question_reply_match[0],
                suggested_character_name=question_reply_match[1],
                confidence=0.93,
                reason_codes=["question_reply_turn_taking", "deterministic_override"],
            )

        orphan_narration_match = _orphan_quote_followed_by_narration_speaker(
            target,
            line,
            assist_context,
            run_state,
        )
        if orphan_narration_match is not None:
            suggested_character_id, suggested_character_name, top_score = orphan_narration_match
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=_clamp_confidence(max(top_score, 0.92)),
                reason_codes=["next_narration_speaker", "deterministic_override"],
            )

        previous_narrator_match = _previous_narrator_carryover_speaker(
            target,
            line,
            assist_context,
            run_state,
        )
        if previous_narrator_match is not None:
            suggested_character_id, suggested_character_name, top_score = previous_narrator_match
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=_clamp_confidence(max(top_score, 0.95)),
                reason_codes=["previous_narrator_carryover", "deterministic_override"],
            )

        previous_quote_units = list(target.get("previousQuoteStructure") or [])
        previous_quote_unit = previous_quote_units[-1] if previous_quote_units else None
        previous_match = _quote_unit_resolved_speaker(previous_quote_unit, assist_context, run_state)
        if (
            previous_match is not None
            and not _quote_unit_has_local_cue(quote_unit)
            and not _paragraph_has_resolved_local_cue(target, line, assist_context, run_state)
            and not str(line.text or "").rstrip().endswith("?")
            and _line_ends_with_continuation_punctuation((previous_quote_unit or {}).get("quoteText"))
        ):
            suggested_character_id, suggested_character_name, _ = previous_match
            return build_result(
                suggested_character_id=suggested_character_id,
                suggested_character_name=suggested_character_name,
                confidence=0.95,
                reason_codes=["adjacent_same_speaker_continuation", "previous_explicit_cue", "continuation_punctuation"],
            )

        if _line_ends_with_continuation_punctuation(line.text) and not (target.get("nextQuoteStructure") or []):
            next_narration_match = _adjacent_narration_speaker(
                target.get("nextParagraphText"),
                assist_context,
                run_state,
            )
            if next_narration_match is not None:
                suggested_character_id, suggested_character_name, _ = next_narration_match
                return build_result(
                    suggested_character_id=suggested_character_id,
                    suggested_character_name=suggested_character_name,
                    confidence=0.95,
                    reason_codes=["adjacent_same_speaker_continuation", "next_narration_attribution", "continuation_punctuation"],
                )

        if next_match is not None and str(line.text or "").rstrip().endswith("?"):
            other_recent_speaker = _nearest_recent_speaker(target, excluded_ids=[next_match[0]])
            if other_recent_speaker is not None:
                return build_result(
                    suggested_character_id=other_recent_speaker[0],
                    suggested_character_name=other_recent_speaker[1],
                    confidence=0.93,
                    reason_codes=["question_precedes_explicit_reply", "recent_turn_alternation", "deterministic_override"],
                )

        return None

    def _update_scene_alias_memory(
        self,
        target: Dict[str, Any],
        result: DialogueAiLineResult,
        run_state: Dict[str, Any],
    ) -> None:
        if result.action not in {"keep_existing", "reassign_existing"}:
            return
        if result.confidence < 0.75 or not result.suggestedCharacterId:
            return
        line = target.get("line")
        if line is None:
            return
        quote_unit = _current_quote_unit(target, line)
        if quote_unit is None:
            return
        surface = quote_unit.get("speakerSurface") or quote_unit.get("speakerNameMatch")
        normalized = _normalize_character_lookup(surface)
        if not normalized or _is_pronoun_surface(normalized) or _is_generic_surface(normalized):
            return
        run_state.setdefault("sceneAliasMemory", {})[normalized] = {
            "characterId": result.suggestedCharacterId,
            "characterName": result.suggestedCharacterName,
            "evidence": "ai_scene_memory",
            "surface": surface,
        }

    def _run_line(
        self,
        request: DialogueAiAssistRequest,
        target: Dict[str, Any],
        assist_context: Dict[str, Any],
        run_state: Dict[str, Any],
    ) -> DialogueAiLineResult:
        line = target["line"]
        current_character_id = line.characterId
        current_character_name = _character_name(current_character_id, assist_context["chapterCharacters"])
        line_log: Dict[str, Any] = {
            "lineId": line.id,
            "paragraphIndex": target["paragraphIndex"],
            "chapterPath": request.chapter_path,
            "currentCharacterId": current_character_id,
            "currentCharacterName": current_character_name,
            "alignment": target.get("alignment"),
            "sceneRoster": target.get("sceneRoster"),
            "currentParagraphText": target["currentParagraphText"],
            "previousParagraphText": target["previousParagraphText"],
            "nextParagraphText": target["nextParagraphText"],
            "calls": [],
        }

        try:
            if request.dry_run:
                result = DialogueAiLineResult(
                    lineId=line.id,
                    paragraphIndex=target["paragraphIndex"],
                    currentCharacterId=current_character_id,
                    currentCharacterName=current_character_name,
                    suggestedCharacterId=current_character_id,
                    suggestedCharacterName=current_character_name,
                    action="needs_review",
                    disposition="review",
                    confidence=0.0,
                    reasonCodes=["dry_run"],
                    currentParagraphText=target["currentParagraphText"],
                    previousParagraphText=target["previousParagraphText"],
                    nextParagraphText=target["nextParagraphText"],
                )
                return self._finalize_logged_result(line_log, run_state, result, target)

            api_key = _get_model_api_key()
            if not api_key:
                result = DialogueAiLineResult(
                    lineId=line.id,
                    paragraphIndex=target["paragraphIndex"],
                    currentCharacterId=current_character_id,
                    currentCharacterName=current_character_name,
                    suggestedCharacterId=current_character_id,
                    suggestedCharacterName=current_character_name,
                    action="needs_review",
                    disposition="error",
                    confidence=0.0,
                    reasonCodes=["model_unconfigured"],
                    currentParagraphText=target["currentParagraphText"],
                    previousParagraphText=target["previousParagraphText"],
                    nextParagraphText=target["nextParagraphText"],
                    error="Set GEMINI_API_KEY or GOOGLE_API_KEY in the backend environment or py_services/.env to enable dialogue AI assist.",
                )
                return self._finalize_logged_result(line_log, run_state, result, target)

            trace: List[DialogueAiToolTraceEntry] = []
            state: Dict[str, Any] = {
                "includePreviousParagraph": False,
                "includeNextParagraph": False,
                "includeBookCharacters": False,
                "includeQuoteStructure": True,
                "includeRecentTurnHistory": True,
                "includeSceneEntities": True,
                "includeCandidateCards": True,
                "includeAttributionSignals": False,
            }
            if _should_include_adjacent_context(line, target):
                state["includePreviousParagraph"] = bool(target.get("previousParagraphText"))
                state["includeNextParagraph"] = bool(target.get("nextParagraphText"))

            deterministic_result = self._deterministic_override_result(
                request=request,
                line=line,
                target=target,
                assist_context=assist_context,
                run_state=run_state,
            )
            if deterministic_result is not None:
                return self._finalize_logged_result(line_log, run_state, deterministic_result, target)

            for call_index in range(4):
                payload = self._build_prompt_payload(
                    line=line,
                    target=target,
                    assist_context=assist_context,
                    state=state,
                    run_state=run_state,
                )
                decision, _ = self._call_model(
                    api_key=api_key,
                    payload=payload,
                    run_state=run_state,
                    line_log=line_log,
                    call_index=call_index + 1,
                )
                response_type = str(decision.get("responseType") or "").strip()
                if response_type == "tool":
                    tool_name = _extract_tool_name(decision)
                    trace.append(self._apply_tool(tool_name, state, target))
                    continue
                if response_type == "final":
                    result = self._finalize_result(
                        request=request,
                        line=line,
                        target=target,
                        assist_context=assist_context,
                        trace=trace,
                        decision=decision,
                    )
                    return self._finalize_logged_result(line_log, run_state, result, target)

                trace.append(DialogueAiToolTraceEntry(tool="request_previous_paragraph", status="ignored", note="invalid_response_type"))
                break

            result = DialogueAiLineResult(
                lineId=line.id,
                paragraphIndex=target["paragraphIndex"],
                currentCharacterId=current_character_id,
                currentCharacterName=current_character_name,
                suggestedCharacterId=current_character_id,
                suggestedCharacterName=current_character_name,
                action="needs_review",
                disposition="review",
                confidence=0.0,
                reasonCodes=["step_limit_reached"],
                currentParagraphText=target["currentParagraphText"],
                previousParagraphText=target["previousParagraphText"],
                nextParagraphText=target["nextParagraphText"],
                toolTrace=trace,
            )
            return self._finalize_logged_result(line_log, run_state, result, target)
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            httpx.HTTPError,
        ) as exc:
            result = DialogueAiLineResult(
                lineId=line.id,
                paragraphIndex=target["paragraphIndex"],
                currentCharacterId=current_character_id,
                currentCharacterName=current_character_name,
                suggestedCharacterId=current_character_id,
                suggestedCharacterName=current_character_name,
                action="error",
                disposition="error",
                confidence=0.0,
                reasonCodes=["assist_failed"],
                currentParagraphText=target["currentParagraphText"],
                previousParagraphText=target["previousParagraphText"],
                nextParagraphText=target["nextParagraphText"],
                error=str(exc),
            )
            line_log["exception"] = str(exc)
            return self._finalize_logged_result(line_log, run_state, result, target)

    def _build_prompt_payload(
        self,
        line: Any,
        target: Dict[str, Any],
        assist_context: Dict[str, Any],
        state: Dict[str, Any],
        run_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        current_character = _get_character_ref(line.characterId, assist_context["chapterCharacters"])
        current_paragraph_lines = [
            _format_dialogue_line_context(candidate, assist_context["chapterCharacters"])
            for candidate in target.get("currentParagraphLines") or []
        ]
        current_position = _current_line_position(target, line.id)
        chapter_candidates = [character.model_dump() for character in assist_context["chapterCharacters"]]
        current_quote_unit = _current_quote_unit(target, line)
        scene_entities = self._build_scene_entity_payload(target, assist_context, run_state)
        candidate_cards = self._candidate_cards(line, target, assist_context, run_state)
        payload: Dict[str, Any] = {
            "line": {
                "id": line.id,
                "text": line.text,
                "currentCharacterId": line.characterId,
                "currentCharacterName": _character_name(line.characterId, assist_context["chapterCharacters"]),
                "currentCharacterAliases": list(current_character.aliases) if current_character else [],
                "currentCharacterRoleLabels": list(current_character.roleLabels) if current_character else [],
                "currentCharacterNotes": current_character.notes if current_character else None,
                "parserLabelsAreProvisional": True,
                "attribution": {
                    "resolutionStatus": line.attribution.resolutionStatus if line.attribution else None,
                    "sourceAlias": line.attribution.sourceAlias if line.attribution else None,
                    "sourceCandidates": line.attribution.sourceCandidates if line.attribution else None,
                    "sourceDescriptors": line.attribution.sourceDescriptors if line.attribution else None,
                    "topCandidateConfidence": line.attribution.topCandidateConfidence if line.attribution else None,
                    "marginToSecond": line.attribution.marginToSecond if line.attribution else None,
                    "misattributionRisk": line.attribution.misattributionRisk if line.attribution else None,
                    "parserBackend": line.attribution.parserBackend if line.attribution else None,
                    "decisionSignals": _decision_trace_signals(line),
                },
                "candidates": [candidate.model_dump() for candidate in (line.attribution.candidates if line.attribution else [])],
                "isReturning": bool(line.isReturning),
                "alignment": target.get("alignment"),
            },
            "paragraphContext": {
                "paragraphIndex": target["paragraphIndex"],
                "currentLinePosition": current_position,
                "dialogueLineCount": len(current_paragraph_lines),
                "currentParagraphText": target["currentParagraphText"],
                "currentParagraphDialogue": current_paragraph_lines,
            },
            "currentQuoteUnit": current_quote_unit,
            "sceneRoster": target.get("sceneRoster"),
            "sceneEntities": scene_entities,
            "candidateCards": candidate_cards,
            "recentTurnHistory": list(target.get("recentTurnHistory") or []),
            "chapterCharacters": chapter_candidates,
            "availableTools": {
                "request_previous_paragraph": bool(target["previousParagraphText"]),
                "request_next_paragraph": bool(target["nextParagraphText"]),
                "request_book_characters": len(assist_context["bookCharacters"]) > 0,
                "request_quote_structure": True,
                "request_recent_turn_history": bool(target.get("recentTurnHistory")),
                "request_scene_entities": bool(scene_entities),
                "request_candidate_cards": bool(candidate_cards),
                "request_attribution_signals": True,
            },
        }
        if state.get("includePreviousParagraph"):
            payload["paragraphContext"]["previousParagraphText"] = target["previousParagraphText"]
            payload["paragraphContext"]["previousParagraphDialogue"] = [
                _format_dialogue_line_context(candidate, assist_context["chapterCharacters"])
                for candidate in target.get("previousParagraphLines") or []
            ]
        if state.get("includeNextParagraph"):
            payload["paragraphContext"]["nextParagraphText"] = target["nextParagraphText"]
            payload["paragraphContext"]["nextParagraphDialogue"] = [
                _format_dialogue_line_context(candidate, assist_context["chapterCharacters"])
                for candidate in target.get("nextParagraphLines") or []
            ]
        if state.get("includeBookCharacters"):
            payload["bookCharacters"] = [character.model_dump() for character in assist_context["bookCharacters"]]
        if state.get("includeQuoteStructure"):
            payload["paragraphContext"]["currentParagraphQuoteStructure"] = list(target.get("currentQuoteStructure") or [])
            if state.get("includePreviousParagraph"):
                payload["paragraphContext"]["previousParagraphQuoteStructure"] = list(target.get("previousQuoteStructure") or [])
            if state.get("includeNextParagraph"):
                payload["paragraphContext"]["nextParagraphQuoteStructure"] = list(target.get("nextQuoteStructure") or [])
        else:
            payload.pop("currentQuoteUnit", None)
        if not state.get("includeRecentTurnHistory"):
            payload.pop("recentTurnHistory", None)
        if not state.get("includeSceneEntities"):
            payload.pop("sceneEntities", None)
        if not state.get("includeCandidateCards"):
            payload.pop("candidateCards", None)
        if state.get("includeAttributionSignals"):
            payload["attributionSignals"] = extract_attribution_signals(
                current_paragraph_text=target["currentParagraphText"],
                previous_paragraph_text=target.get("previousParagraphText"),
                next_paragraph_text=target.get("nextParagraphText"),
                known_surfaces=[character.name for character in assist_context["chapterCharacters"]] + [character.name for character in assist_context["bookCharacters"]],
            )
        return payload

    def _call_model(
        self,
        api_key: str,
        payload: Dict[str, Any],
        run_state: Dict[str, Any],
        line_log: Dict[str, Any],
        call_index: int,
    ) -> Tuple[Dict[str, Any], bool]:
        model_name = _get_model_name()
        request_json = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"{SYSTEM_PROMPT}\n\nINPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        cache_key = _hash_payload(model_name, payload)
        cache_path = AI_CACHE_DIR / f"{cache_key}.json"
        call_log: Dict[str, Any] = {
            "callIndex": call_index,
            "modelName": model_name,
            "cacheKey": cache_key,
            "cachePath": str(cache_path.resolve()),
            "payload": payload,
            "request": request_json,
        }

        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_decision = cached.get("decision")
                if isinstance(cached_decision, dict):
                    run_state["cacheHits"] += 1
                    call_log["cacheHit"] = True
                    call_log["responseText"] = cached.get("responseText")
                    call_log["decision"] = cached_decision
                    line_log["calls"].append(call_log)
                    return cached_decision, True
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass

        AI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        response = httpx.post(
            _get_model_endpoint(model_name),
            params={"key": api_key},
            json=request_json,
            timeout=60.0,
        )
        run_state["modelCalls"] += 1
        response.raise_for_status()
        data = response.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text")
        )
        if not text:
            raise ValueError("Gemini returned no text payload")
        decision = json.loads(text)
        cache_entry = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "modelName": model_name,
            "cacheKey": cache_key,
            "payload": payload,
            "request": request_json,
            "responseText": text,
            "decision": decision,
        }
        cache_path.write_text(_serialize_json(cache_entry), encoding="utf-8")
        call_log["cacheHit"] = False
        call_log["responseText"] = text
        call_log["decision"] = decision
        line_log["calls"].append(call_log)
        return decision, False

    def _finalize_logged_result(
        self,
        line_log: Dict[str, Any],
        run_state: Dict[str, Any],
        result: DialogueAiLineResult,
        target: Optional[Dict[str, Any]] = None,
    ) -> DialogueAiLineResult:
        if target is not None:
            self._update_scene_alias_memory(target, result, run_state)
        log_path = self._write_line_log(
            run_state["logDirectory"],
            result.lineId,
            {
                **line_log,
                "finalResult": result.model_dump(),
            },
        )
        return result.model_copy(
            update={
                "cacheHit": any(bool(call.get("cacheHit")) for call in line_log.get("calls", [])),
                "logPath": log_path,
            }
        )

    def _write_line_log(self, log_directory: Path, line_id: int, payload: Dict[str, Any]) -> str:
        log_path = log_directory / f"line-{int(line_id):04d}.json"
        log_path.write_text(_serialize_json(payload), encoding="utf-8")
        return str(log_path.resolve())

    def _apply_tool(self, tool_name: str, state: Dict[str, Any], target: Dict[str, Any]) -> DialogueAiToolTraceEntry:
        if tool_name == "request_previous_paragraph":
            if target["previousParagraphText"]:
                state["includePreviousParagraph"] = True
                return DialogueAiToolTraceEntry(tool=tool_name, status="used")
            return DialogueAiToolTraceEntry(tool=tool_name, status="unavailable", note="no_previous_paragraph")
        if tool_name == "request_next_paragraph":
            if target["nextParagraphText"]:
                state["includeNextParagraph"] = True
                return DialogueAiToolTraceEntry(tool=tool_name, status="used")
            return DialogueAiToolTraceEntry(tool=tool_name, status="unavailable", note="no_next_paragraph")
        if tool_name == "request_book_characters":
            state["includeBookCharacters"] = True
            return DialogueAiToolTraceEntry(tool=tool_name, status="used")
        if tool_name == "request_quote_structure":
            state["includeQuoteStructure"] = True
            return DialogueAiToolTraceEntry(tool=tool_name, status="used")
        if tool_name == "request_recent_turn_history":
            if target.get("recentTurnHistory"):
                state["includeRecentTurnHistory"] = True
                return DialogueAiToolTraceEntry(tool=tool_name, status="used")
            return DialogueAiToolTraceEntry(tool=tool_name, status="unavailable", note="no_recent_turn_history")
        if tool_name == "request_scene_entities":
            if target.get("currentQuoteStructure"):
                state["includeSceneEntities"] = True
                return DialogueAiToolTraceEntry(tool=tool_name, status="used")
            return DialogueAiToolTraceEntry(tool=tool_name, status="unavailable", note="no_scene_entities")
        if tool_name == "request_candidate_cards":
            state["includeCandidateCards"] = True
            return DialogueAiToolTraceEntry(tool=tool_name, status="used")
        if tool_name == "request_attribution_signals":
            state["includeAttributionSignals"] = True
            return DialogueAiToolTraceEntry(tool=tool_name, status="used")
        return DialogueAiToolTraceEntry(tool="request_previous_paragraph", status="ignored", note=f"unknown_tool:{tool_name}")

    def _finalize_result(
        self,
        request: DialogueAiAssistRequest,
        line: Any,
        target: Dict[str, Any],
        assist_context: Dict[str, Any],
        trace: List[DialogueAiToolTraceEntry],
        decision: Dict[str, Any],
    ) -> DialogueAiLineResult:
        action = str(decision.get("action") or "needs_review").strip()
        confidence = _clamp_confidence(decision.get("confidence"))
        has_suggested_character = "characterId" in decision and decision.get("characterId") is not None
        if action == "needs_review" and not has_suggested_character:
            suggested_character_id = None
            suggested_character_name = UNKNOWN_CHARACTER_NAME
        else:
            suggested_character_id = decision.get("characterId") or line.characterId
            suggested_character_name = decision.get("characterName") or _character_name(suggested_character_id, assist_context["chapterCharacters"])
            suggested_character_id, suggested_character_name = _canonicalize_suggested_character(
                suggested_character_id,
                suggested_character_name,
                assist_context["bookCharacters"],
            )
        if (
            action == "reassign_existing"
            and _is_generic_character_id(suggested_character_id)
            and not _is_generic_character_id(line.characterId)
        ):
            action = "needs_review"
            suggested_character_id = None
            suggested_character_name = UNKNOWN_CHARACTER_NAME
            confidence = min(confidence, 0.72)
            decision.setdefault("reasonCodes", [])
            if isinstance(decision["reasonCodes"], list):
                decision["reasonCodes"].append("generic_character_guard")
                decision["reasonCodes"].append("ambiguous_unknown")

        disposition = "review"
        if action in {"keep_existing", "reassign_existing"} and suggested_character_id:
            disposition = "auto_apply" if confidence >= request.auto_apply_threshold else "review"
        elif action == "propose_alias":
            disposition = "alias_review"
        elif action == "propose_new_character":
            disposition = "new_character_approval"
        elif action == "error":
            disposition = "error"

        alias_payload = decision.get("aliasToAdd") or None
        new_character_payload = decision.get("newCharacterProposal") or None
        alias_to_add = DialogueAiAliasProposal(**alias_payload) if isinstance(alias_payload, dict) else None
        new_character_proposal = (
            DialogueAiNewCharacterProposal(**new_character_payload)
            if isinstance(new_character_payload, dict)
            else None
        )

        reason_codes = [str(code) for code in decision.get("reasonCodes") or [] if str(code).strip()]
        if action == "propose_new_character" and new_character_proposal is not None:
            proposed_name = str(new_character_proposal.name or "").strip()
            book_character_ids = {
                character.characterId for character in assist_context["bookCharacters"]
            }
            if proposed_name:
                canonical_id, canonical_name = _canonicalize_suggested_character(
                    proposed_name,
                    proposed_name,
                    assist_context["bookCharacters"],
                )
                if canonical_id in book_character_ids:
                    action = "reassign_existing"
                    suggested_character_id = canonical_id
                    suggested_character_name = canonical_name
                    confidence = max(confidence, 0.96)
                    new_character_proposal = None
                    reason_codes.append("matched_existing_name")

        if action == "propose_new_character" and new_character_proposal is not None:
            proposed_surface = new_character_proposal.alias or new_character_proposal.name
            resolved = resolve_surface_identity(
                proposed_surface,
                assist_context["chapterCharacters"],
                assist_context["bookCharacters"],
                assist_context.get("sceneAliasMemory"),
            )
            if resolved and float(resolved[0].get("score") or 0.0) >= 0.78:
                top_match = resolved[0]
                action = "reassign_existing"
                suggested_character_id = str(top_match.get("characterId") or suggested_character_id)
                suggested_character_name = str(top_match.get("characterName") or suggested_character_name)
                confidence = max(confidence, _clamp_confidence(top_match.get("score") or 0.0))
                new_character_proposal = None
                reason_codes.append("resolved_existing_role")

        disposition = "review"
        if action in {"keep_existing", "reassign_existing"} and suggested_character_id:
            disposition = "auto_apply" if confidence >= request.auto_apply_threshold else "review"
        elif action == "propose_alias":
            disposition = "alias_review"
        elif action == "propose_new_character":
            disposition = "new_character_approval"
        elif action == "error":
            disposition = "error"

        return DialogueAiLineResult(
            lineId=line.id,
            paragraphIndex=target["paragraphIndex"],
            currentCharacterId=line.characterId,
            currentCharacterName=_character_name(line.characterId, assist_context["chapterCharacters"]),
            suggestedCharacterId=suggested_character_id,
            suggestedCharacterName=suggested_character_name,
            action=action if action else "needs_review",
            disposition=disposition,
            confidence=confidence,
            reasonCodes=reason_codes,
            currentParagraphText=target["currentParagraphText"],
            previousParagraphText=target["previousParagraphText"],
            nextParagraphText=target["nextParagraphText"],
            toolTrace=trace,
            aliasToAdd=alias_to_add,
            newCharacterProposal=new_character_proposal,
            error=decision.get("error"),
        )
