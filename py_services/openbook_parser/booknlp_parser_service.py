"""Legacy-only parser adapter for OpenBook parse API."""

import os
import re
from typing import Dict, List, Optional, Tuple

from .attribution_passes import (
    LineSignals,
    build_paragraph_ranges,
    detect_nearby_speech_verb,
    extract_explicit_mentions,
    extract_first_sentence,
    extract_last_sentence,
    find_paragraph_index_for_offset,
    infer_addressed_name,
    infer_pronoun_attributed_gender,
    infer_previous_paragraph_named_mention,
    infer_recent_named_mention,
    infer_sentence_attributed_speaker,
    quote_boundary_quality,
    rank_candidates,
)
from .dialogue_parser_service import DialogueLine, DialogueParserService
from .episode_decoder import HARD_REASONS, decode_dialogue_episodes
from .knowledge_store import load_knowledge_for_file
from .modernbooknlp_service import ModernBookNLPService, align_dialogue_lines


BLOCKED_CHARACTER_TOKENS = {
    "he", "she", "as", "it", "that", "they", "them", "the",
    "this", "these", "those",
}


def _normalize_text(value: str) -> str:
    text = (value or "").lower()
    text = (
        text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    text = re.sub(r"(?<=\w)\s*'\s*(?=\w)", "'", text)
    text = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"^[\s\"'`“”‘’]+|[\s\"'`“”‘’]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_gender_label(value: Optional[str]) -> Optional[str]:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return None
    if lowered in {"m", "male", "man", "boy"}:
        return "male"
    if lowered in {"f", "female", "woman", "girl"}:
        return "female"
    if lowered in {"u", "unknown", "narrator"}:
        return None
    if lowered.startswith("he/"):
        return "male"
    if lowered.startswith("she/"):
        return "female"
    if lowered.startswith("they/"):
        return "neutral"
    return None


def build_closed_world_catalog(rows) -> Dict[str, object]:
    surface_to_name: Dict[str, str] = {}
    gender_by_name: Dict[str, str] = {}
    descriptor_owners: Dict[str, set[str]] = {}
    names: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        names.add(name)
        for value in [row.get("characterId"), name, *(row.get("aliases") or [])]:
            key = _normalize_text(str(value or ""))
            if key:
                surface_to_name[key] = name
        gender = _normalize_gender_label(row.get("gender"))
        if gender:
            gender_by_name[name] = gender
        for value in row.get("descriptors") or []:
            descriptor = _normalize_text(str(value or ""))
            if descriptor:
                descriptor_owners.setdefault(descriptor, set()).add(name)
    descriptor_to_name = {
        descriptor: next(iter(owners))
        for descriptor, owners in descriptor_owners.items()
        if len(owners) == 1
    }
    return {
        "surface_to_name": surface_to_name,
        "gender_by_name": gender_by_name,
        "descriptor_to_name": descriptor_to_name,
        "names": names,
    }


def canonicalize_closed_world_name(value: Optional[str], catalog: Dict[str, object]) -> Optional[str]:
    return catalog.get("surface_to_name", {}).get(_normalize_text(str(value or "")))


def infer_unique_descriptor_speaker(text: str, catalog: Dict[str, object]) -> Optional[str]:
    normalized_text = f" {_normalize_text(text)} "
    matched_names = {
        name
        for descriptor, name in catalog.get("descriptor_to_name", {}).items()
        if descriptor and f" {descriptor} " in normalized_text
    }
    return next(iter(matched_names)) if len(matched_names) == 1 else None


def _read_text_with_fallback(file_path: str) -> str:
    with open(file_path, "rb") as source_handle:
        raw_bytes = source_handle.read()
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1252", errors="replace")


def _unique_suggestions(*names: Optional[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


def _build_line_attribution(signals: LineSignals, decision) -> Dict[str, object]:
    selected_entry = next(
        (
            entry
            for entry in decision.ranked
            if _normalize_text(entry.name) == _normalize_text(decision.chosen_name)
        ),
        decision.ranked[0] if decision.ranked else None,
    )
    selected_reasons = list(selected_entry.reasons) if selected_entry else []

    return {
        "parserBackend": "legacy",
        "contextGender": signals.context_gender,
        "contextGenderCue": signals.context_gender_cue,
        "genderConflict": bool(
            selected_entry and "context_gender_conflict" in selected_entry.reasons
        ),
        "candidates": [
            {
                "name": entry.name,
                "confidence": round(float(entry.score), 4),
                "reasons": list(entry.reasons),
            }
            for entry in decision.ranked
        ],
        "decisionTrace": {
            "selectedCandidate": decision.chosen_name,
            "selectedReasons": selected_reasons,
            "signals": {
                "usedFallbackAlignment": signals.used_fallback_alignment,
                "exactQuoteMatch": signals.exact_quote_match,
                "quoteBoundaryQuality": round(float(signals.quote_boundary_quality), 4),
                "nearbySpeechVerb": signals.nearby_speech_verb,
                "hasCorefName": signals.has_coref_name,
                "aliasMatchStrength": round(float(signals.alias_match_strength), 4),
                "addressedNamePrevSentence": signals.addressed_name_prev_sentence,
                "explicitQuoteMentions": list(signals.explicit_quote_mentions or []),
                "nextSentenceAttributedSpeaker": signals.next_sentence_attributed_speaker,
                "recentNamedMentionBeforeQuote": signals.recent_named_mention_before_quote,
                "previousParagraphNamedMention": signals.previous_paragraph_named_mention,
                "descriptorMatch": signals.descriptor_match,
                "isReturning": signals.is_returning,
                "continuesParagraphDialogue": signals.continues_paragraph_dialogue,
                "lineEndsWithQuestion": signals.line_ends_with_question,
                "nextDialogueLegacy": signals.next_dialogue_legacy,
                "contextGender": signals.context_gender,
                "contextGenderCue": signals.context_gender_cue,
            },
        },
    }


def _apply_attribution_override(line: DialogueLine, chosen_name: str, override_reason: str) -> None:
    if not isinstance(line.attribution, dict):
        return

    candidate_rows = line.attribution.get("candidates")
    if not isinstance(candidate_rows, list):
        return

    chosen_key = _normalize_text(chosen_name)
    chosen_index = next(
        (
            index
            for index, row in enumerate(candidate_rows)
            if _normalize_text((row or {}).get("name")) == chosen_key
        ),
        None,
    )
    if chosen_index is None:
        return

    if chosen_index > 0:
        chosen_row = candidate_rows.pop(chosen_index)
        candidate_rows.insert(0, chosen_row)

    selected_row = candidate_rows[0] if candidate_rows else {}
    selected_reasons = list(selected_row.get("reasons") or [])
    if override_reason not in selected_reasons:
        selected_reasons.append(override_reason)
        selected_row["reasons"] = selected_reasons

    line.attribution["genderConflict"] = "context_gender_conflict" in selected_reasons
    trace = line.attribution.setdefault("decisionTrace", {})
    trace["selectedCandidate"] = chosen_name
    trace["selectedReasons"] = selected_reasons
    trace["overrideReason"] = override_reason


def _has_explicit_local_attribution(line: DialogueLine) -> bool:
    attribution = line.attribution if isinstance(line.attribution, dict) else {}
    trace = attribution.get("decisionTrace")
    if not isinstance(trace, dict):
        return False
    reasons = {str(reason) for reason in trace.get("selectedReasons") or []}
    return bool(reasons & (HARD_REASONS | {
        "0p_protagonist_first_person",
        "first_person_override",
    }))


class BookNLPParserService:
    """Compatibility adapter that keeps parsing on the fast legacy path."""

    def __init__(self) -> None:
        self.legacy_service = DialogueParserService()
        self.modernbooknlp_service = ModernBookNLPService()

    def parse_file(
        self,
        file_path: str,
        options: Optional[dict] = None,
        manual_blocklist: Optional[List[str]] = None,
        source_path: Optional[str] = None,
    ) -> Tuple[List[DialogueLine], List[str], Dict[str, object]]:
        """Parse a chapter file with the legacy parser plus deterministic attribution scoring."""
        normalized_manual_blocklist = {
            _normalize_text(name)
            for name in (manual_blocklist or [])
            if _normalize_text(name)
        }
        effective_blocklist = set(BLOCKED_CHARACTER_TOKENS)
        effective_blocklist.update(normalized_manual_blocklist)

        self.legacy_service.blocked_speakers.update(effective_blocklist)

        legacy_input_path = source_path if (source_path and os.path.exists(source_path)) else file_path
        base_lines, _ = self.legacy_service.parse_file(legacy_input_path, options=options or {})

        knowledge_store = load_knowledge_for_file(source_path or legacy_input_path)
        display_name_by_key: Dict[str, str] = {}
        known_gender_by_key: Dict[str, str] = {}
        closed_world = bool((options or {}).get("closed_world_characters"))
        closed_world_catalog = build_closed_world_catalog((options or {}).get("character_catalog") or [])

        if closed_world:
            display_name_by_key.update(closed_world_catalog["surface_to_name"])
            for canonical, gender in closed_world_catalog["gender_by_name"].items():
                known_gender_by_key[_normalize_text(canonical)] = gender

        if knowledge_store:
            for canonical in knowledge_store.genders.keys():
                key = _normalize_text(canonical)
                if not key:
                    continue
                display_name_by_key[key] = canonical
                gender = _normalize_gender_label(knowledge_store.genders.get(canonical))
                if gender:
                    known_gender_by_key[key] = gender
            for alias, canonical in knowledge_store.aliases.items():
                alias_key = _normalize_text(alias)
                canonical_key = _normalize_text(canonical)
                if not alias_key or not canonical_key:
                    continue
                display_name_by_key.setdefault(alias_key, canonical)
                if canonical_key in known_gender_by_key:
                    known_gender_by_key[alias_key] = known_gender_by_key[canonical_key]

        if not closed_world:
            for line in base_lines:
                speaker = str(line.speaker or "").strip()
                if speaker:
                    display_name_by_key.setdefault(_normalize_text(speaker), speaker)
                for suggestion in line.suggestions or []:
                    suggestion_name = str(suggestion or "").strip()
                    if suggestion_name:
                        display_name_by_key.setdefault(_normalize_text(suggestion_name), suggestion_name)

        known_names = sorted(set(display_name_by_key.values()))
        original_text = _read_text_with_fallback(legacy_input_path)

        paragraph_ranges = build_paragraph_ranges(original_text)
        line_paragraph_index: Dict[int, int] = {}
        last_entry_by_paragraph: Dict[int, int] = {}
        previous_dialogue_by_line: Dict[int, Optional[int]] = {}
        last_dialogue_by_paragraph: Dict[int, int] = {}
        paragraphs_with_dialogue = set()

        for idx, base_line in enumerate(base_lines):
            paragraph_index = find_paragraph_index_for_offset(
                paragraph_ranges,
                base_line.span_start,
            )
            line_paragraph_index[idx] = paragraph_index
            base_line.paragraph_index = paragraph_index
            if paragraph_index < 0:
                continue
            last_entry_by_paragraph[paragraph_index] = idx
            if base_line.line_type != "dialogue":
                continue
            paragraphs_with_dialogue.add(paragraph_index)
            previous_dialogue_by_line[idx] = last_dialogue_by_paragraph.get(paragraph_index)
            last_dialogue_by_paragraph[paragraph_index] = idx

        is_returning_by_line = {
            idx: (
                paragraph_index >= 0
                and paragraph_index in paragraphs_with_dialogue
                and last_entry_by_paragraph.get(paragraph_index) == idx
            )
            for idx, paragraph_index in line_paragraph_index.items()
        }

        uncertain_lines = 0
        resolved_dialogue_by_paragraph: Dict[int, str] = {}

        next_legacy_by_line: Dict[int, Optional[str]] = {}
        next_dialogue_by_line: Dict[int, Optional[int]] = {}
        next_dialogue_legacy: Optional[str] = None
        next_dialogue_index: Optional[int] = None
        for idx in range(len(base_lines) - 1, -1, -1):
            base_line = base_lines[idx]
            if base_line.line_type != "dialogue":
                continue
            next_legacy_by_line[idx] = next_dialogue_legacy
            next_dialogue_by_line[idx] = next_dialogue_index
            current = str(base_line.speaker or "").strip()
            if current:
                next_dialogue_legacy = current
            next_dialogue_index = idx

        for line_idx, line in enumerate(base_lines):
            if line.line_type != "dialogue":
                continue

            legacy_name = str(line.speaker or "").strip()
            legacy_rule = (
                line.attribution.get("legacyRule")
                if isinstance(line.attribution, dict)
                else None
            )
            paragraph_index = line_paragraph_index.get(line_idx, -1)
            paragraph_range = (
                paragraph_ranges[paragraph_index]
                if 0 <= paragraph_index < len(paragraph_ranges)
                else None
            )
            raw_context = ""
            pre_quote_context = ""
            post_quote_context = ""
            if isinstance(line.span_start, int) and isinstance(line.span_end, int):
                if paragraph_range:
                    left = max(paragraph_range.start, line.span_start - 120)
                    right = min(paragraph_range.end, line.span_end + 120)
                    pre_left = paragraph_range.start
                    post_right = paragraph_range.end
                    previous_dialogue_index = previous_dialogue_by_line.get(line_idx)
                    if previous_dialogue_index is not None:
                        previous_dialogue = base_lines[previous_dialogue_index]
                        if isinstance(previous_dialogue.span_end, int):
                            pre_left = max(pre_left, previous_dialogue.span_end)
                    next_dialogue_index = next_dialogue_by_line.get(line_idx)
                    if (
                        next_dialogue_index is not None
                        and line_paragraph_index.get(next_dialogue_index) == paragraph_index
                    ):
                        next_dialogue = base_lines[next_dialogue_index]
                        if isinstance(next_dialogue.span_start, int):
                            post_right = min(post_right, next_dialogue.span_start)
                else:
                    left = max(0, line.span_start - 120)
                    right = min(len(original_text), line.span_end + 120)
                    pre_left = max(0, line.span_start - 240)
                    post_right = min(len(original_text), line.span_end + 280)
                raw_context = original_text[left:right]
                pre_quote_context = original_text[pre_left:line.span_start]
                post_quote_context = original_text[line.span_end:post_right]

            last_sentence = extract_last_sentence(pre_quote_context)
            next_sentence = extract_first_sentence(post_quote_context)
            addressed_name = infer_addressed_name(last_sentence, known_names)
            explicit_mentions = extract_explicit_mentions(line.text, known_names)
            next_sentence_speaker = infer_sentence_attributed_speaker(next_sentence, known_names)
            descriptor_speaker = infer_unique_descriptor_speaker(
                f"{pre_quote_context[-160:]} {next_sentence}",
                closed_world_catalog,
            ) if closed_world else None
            recent_named_mention = infer_recent_named_mention(pre_quote_context, known_names)
            previous_paragraph_named_mention = None
            context_gender = None
            context_gender_cue = None
            for sentence in (next_sentence, last_sentence):
                context_gender, context_gender_cue = infer_pronoun_attributed_gender(sentence)
                if context_gender:
                    break
            if (
                context_gender
                and next_sentence_speaker is None
                and recent_named_mention is None
                and previous_paragraph_named_mention is None
                and paragraph_index > 0
            ):
                previous_paragraph_range = paragraph_ranges[paragraph_index - 1]
                previous_paragraph_text = original_text[
                    previous_paragraph_range.start:previous_paragraph_range.end
                ]
                previous_paragraph_named_mention = infer_previous_paragraph_named_mention(
                    previous_paragraph_text,
                    known_names,
                )

            next_legacy = next_legacy_by_line.get(line_idx)
            prev_resolved = (
                resolved_dialogue_by_paragraph.get(paragraph_index)
                if paragraph_index >= 0
                else None
            )
            continues_paragraph_dialogue = bool(
                previous_dialogue_by_line.get(line_idx) is not None and prev_resolved
            )

            dialogue_candidates = _unique_suggestions(
                *(line.suggestions or []),
                legacy_name,
                prev_resolved,
                next_sentence_speaker,
                recent_named_mention if context_gender else None,
                previous_paragraph_named_mention if context_gender else None,
                descriptor_speaker,
            )
            if not dialogue_candidates:
                dialogue_candidates = _unique_suggestions(
                    legacy_name,
                    prev_resolved,
                    next_sentence_speaker,
                    recent_named_mention if context_gender else None,
                    previous_paragraph_named_mention if context_gender else None,
                    descriptor_speaker,
                )

            if closed_world:
                canonical_candidates: List[str] = []
                for candidate_name in dialogue_candidates:
                    canonical = canonicalize_closed_world_name(candidate_name, closed_world_catalog)
                    if canonical:
                        canonical_candidates.append(canonical)
                dialogue_candidates = _unique_suggestions(*canonical_candidates)

            if not dialogue_candidates:
                line.speaker = legacy_name
                line.is_suggestion = True
                line.suggestions = _unique_suggestions(legacy_name)
                line.attribution = {
                    "parserBackend": "legacy",
                    "candidates": [],
                    "decisionTrace": {
                        "selectedCandidate": None,
                        "selectedReasons": ["closed_world_unresolved"],
                        "signals": {"sourceCandidate": legacy_name},
                    },
                }
                uncertain_lines += 1
                continue

            candidate_genders: Dict[str, str] = {}
            for candidate_name in dialogue_candidates:
                candidate_key = _normalize_text(candidate_name)
                candidate_gender = known_gender_by_key.get(candidate_key)
                if candidate_gender:
                    candidate_genders[candidate_key] = candidate_gender

            signals = LineSignals(
                predicted_name=legacy_name,
                legacy_name=legacy_name,
                prev_resolved=prev_resolved,
                next_resolved=None,
                used_fallback_alignment=False,
                exact_quote_match=True,
                quote_boundary_quality=quote_boundary_quality(raw_context, line.text),
                nearby_speech_verb=detect_nearby_speech_verb(raw_context),
                has_coref_name=False,
                alias_match_strength=0.0,
                addressed_name_prev_sentence=addressed_name,
                explicit_quote_mentions=explicit_mentions,
                next_sentence_attributed_speaker=next_sentence_speaker,
                recent_named_mention_before_quote=recent_named_mention if context_gender else None,
                previous_paragraph_named_mention=previous_paragraph_named_mention if context_gender else None,
                candidate_genders=candidate_genders,
                context_gender=context_gender,
                context_gender_cue=context_gender_cue,
                is_returning=is_returning_by_line.get(line_idx, False),
                continues_paragraph_dialogue=continues_paragraph_dialogue,
                line_ends_with_question=str(line.text or "").strip().endswith("?"),
                next_dialogue_legacy=next_legacy,
                descriptor_match=descriptor_speaker,
            )
            decision = rank_candidates(dialogue_candidates, signals)
            line.speaker = decision.chosen_name
            line.is_suggestion = decision.is_suggestion
            line.suggestions = [entry.name for entry in decision.ranked]
            line.attribution = _build_line_attribution(signals, decision)
            if legacy_rule in {"0p_protagonist_first_person", "first_person_override"}:
                protagonist_name = (
                    canonicalize_closed_world_name(legacy_name, closed_world_catalog)
                    if closed_world
                    else legacy_name
                )
                if protagonist_name:
                    line.speaker = protagonist_name
                    line.is_suggestion = False
                    line.suggestions = _unique_suggestions(
                        protagonist_name,
                        *line.suggestions,
                    )
                    _apply_attribution_override(line, protagonist_name, legacy_rule)
            if line.is_suggestion:
                uncertain_lines += 1
            elif paragraph_index >= 0 and line.speaker:
                resolved_dialogue_by_paragraph[paragraph_index] = line.speaker

        dialogue_indices = [
            idx for idx, value in enumerate(base_lines)
            if value.line_type == "dialogue"
        ]

        for idx_pos, line_idx in enumerate(dialogue_indices):
            line = base_lines[line_idx]
            if not line.is_suggestion or not line.suggestions or len(line.suggestions) < 2:
                continue
            if _has_explicit_local_attribution(line):
                continue

            current_paragraph = line_paragraph_index.get(line_idx, -1)
            previous_speaker = None
            next_speaker = None

            for prev_pos in range(idx_pos - 1, -1, -1):
                prev_idx = dialogue_indices[prev_pos]
                if line_paragraph_index.get(prev_idx, -1) != current_paragraph:
                    break
                prev_line = base_lines[prev_idx]
                if prev_line.line_type == "dialogue" and not prev_line.is_suggestion:
                    previous_speaker = str(prev_line.speaker or "").strip()
                    break

            for next_pos in range(idx_pos + 1, len(dialogue_indices)):
                next_idx = dialogue_indices[next_pos]
                if line_paragraph_index.get(next_idx, -1) != current_paragraph:
                    break
                next_line = base_lines[next_idx]
                if next_line.line_type == "dialogue" and not next_line.is_suggestion:
                    next_speaker = str(next_line.speaker or "").strip()
                    break

            if not previous_speaker or not next_speaker:
                continue
            if _normalize_text(previous_speaker) != _normalize_text(next_speaker):
                continue

            for candidate in line.suggestions:
                if _normalize_text(candidate) == _normalize_text(previous_speaker):
                    reordered = _unique_suggestions(candidate, *line.suggestions)
                    line.speaker = reordered[0]
                    line.suggestions = reordered
                    _apply_attribution_override(
                        line,
                        reordered[0],
                        "same_paragraph_context_consistency",
                    )
                    break

        episode_decoder_enabled = bool((options or {}).get("episode_joint_decode", True))
        episode_decoder_changes = (
            decode_dialogue_episodes(base_lines) if episode_decoder_enabled else 0
        )
        requested_backend = str((options or {}).get("parser_backend") or "legacy").strip().lower()
        backend = "legacy"
        fallback_reason = None
        modernbooknlp_quotes = 0
        modernbooknlp_aligned = 0
        if requested_backend == "modernbooknlp":
            try:
                predictions = self.modernbooknlp_service.attribute_file(
                    legacy_input_path,
                    (options or {}).get("character_catalog") or [],
                )
                aligned = align_dialogue_lines(base_lines, predictions)
                modernbooknlp_quotes = len(predictions)
                modernbooknlp_aligned = len(aligned)
                for line_index, prediction in aligned.items():
                    line = base_lines[line_index]
                    previous_speaker = str(line.speaker or "").strip() or None
                    attribution = line.attribution if isinstance(line.attribution, dict) else {}
                    trace = attribution.setdefault("decisionTrace", {})
                    modernbooknlp_metadata = {
                        "corefId": prediction.coref_id,
                        "nominalMentions": list(prediction.nominal_mentions),
                        "quoteSpan": {"start": prediction.start, "end": prediction.end},
                        "previousSpeaker": previous_speaker,
                        "proposedSpeaker": prediction.speaker,
                    }
                    trace["modernBookNLP"] = modernbooknlp_metadata
                    if _has_explicit_local_attribution(line):
                        trace["modernBookNLPDecision"] = (
                            "rejected_explicit_local_attribution"
                        )
                        line.attribution = attribution
                        continue
                    line.speaker = prediction.speaker
                    line.is_suggestion = False
                    line.suggestions = _unique_suggestions(prediction.speaker, *line.suggestions)
                    attribution["parserBackend"] = "modernbooknlp"
                    attribution["candidates"] = [
                        {
                            "name": prediction.speaker,
                            "confidence": 0.9,
                            "reasons": ["modernbooknlp_joint_attribution"],
                        },
                        *[
                            candidate
                            for candidate in attribution.get("candidates", [])
                            if _normalize_text((candidate or {}).get("name"))
                            != _normalize_text(prediction.speaker)
                        ],
                    ]
                    trace.update({
                        "selectedCandidate": prediction.speaker,
                        "selectedReasons": ["modernbooknlp_joint_attribution"],
                        "overrideReason": "modernbooknlp_joint_attribution",
                        "modernBookNLPDecision": "accepted",
                    })
                    line.attribution = attribution
                backend = "modernbooknlp"
            except Exception as exc:
                fallback_reason = f"modernbooknlp_failed: {exc}"
        elif requested_backend != "legacy":
            fallback_reason = f"unsupported_parser_backend: {requested_backend}"

        uncertain_lines = sum(
            1
            for line in base_lines
            if line.line_type == "dialogue" and line.is_suggestion
        )

        names = sorted(closed_world_catalog["names"]) if closed_world else sorted(
            {
                line.speaker
                for line in base_lines
                if _normalize_text(line.speaker) not in effective_blocklist
            }
        )
        return base_lines, names, {
            "backend": backend,
            "fallback_reason": fallback_reason,
            "quotes": modernbooknlp_quotes,
            "aligned_quotes": modernbooknlp_aligned,
            "uncertain_lines": uncertain_lines,
            "episode_decoder_enabled": episode_decoder_enabled,
            "episode_decoder_changes": episode_decoder_changes,
        }
