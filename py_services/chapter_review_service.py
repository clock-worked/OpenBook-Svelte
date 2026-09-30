"""Finalize reviewed chapters and learn conservative character descriptors."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
import os
import re
import tempfile
from typing import Any, Dict, Iterable, List, Optional

import spacy

try:
    from .openbook_parser.attribution_passes import ATTRIBUTION_VERBS
except ImportError:
    from openbook_parser.attribution_passes import ATTRIBUTION_VERBS

VAGUE_DESCRIPTOR_MODIFIERS = {"all", "any", "each", "every", "few", "other", "same", "some"}
PERSON_REFERENCE_WORDS = {
    "he", "her", "hers", "him", "his", "i", "me", "mine", "my", "our",
    "ours", "she", "their", "theirs", "them", "they", "us", "we", "you",
    "your", "yours",
}
DESCRIPTOR_ARTICLES = {"a", "an", "the"}
SUBJECT_PATTERN = r"((?:the|a|an)\s+[A-Za-z][A-Za-z'’-]*(?:\s+[A-Za-z][A-Za-z'’-]*){0,4})"
VERB_PATTERN = "|".join(sorted((re.escape(value) for value in ATTRIBUTION_VERBS), key=len, reverse=True))
PERSON_REFERENCE_PATTERN = "|".join(sorted(PERSON_REFERENCE_WORDS, key=len, reverse=True))
QUOTE_MARKER_PATTERN = r'["“”\ufffd]'


def _span_starts_inside_quote(raw_text: str, paragraph_start: int, start: int) -> bool:
    prefix = raw_text[paragraph_start:start]
    last_open = prefix.rfind("“")
    last_close = prefix.rfind("”")
    if last_open != last_close:
        return last_open > last_close
    return (prefix.count('"') + prefix.count("\ufffd")) % 2 == 1


@lru_cache(maxsize=1)
def _load_descriptor_nlp() -> Any:
    """Load the shared English parser lazily because review is an infrequent action."""
    try:
        return spacy.load("en_core_web_sm")
    except OSError:
        return None


def normalize_descriptor(value: object) -> Optional[str]:
    """Normalize a determiner-led descriptor without assuming a role vocabulary."""
    descriptor = re.sub(r"\s+", " ", str(value or "").strip())
    descriptor = descriptor.strip('"\'“”‘’.,;:!? ')
    if not descriptor or len(descriptor) > 80:
        return None
    words = re.findall(r"[a-z]+(?:'[a-z]+)?", descriptor.lower())
    if len(words) < 2 or len(words) > 6:
        return None
    if words[0] not in DESCRIPTOR_ARTICLES:
        return None
    if any(word in VAGUE_DESCRIPTOR_MODIFIERS for word in words[:-1]):
        return None
    return descriptor


def _verb_is_attribution(verb: Any) -> bool:
    forms = {str(verb.text).lower(), str(verb.lemma_).lower()}
    particles = [str(child.text).lower() for child in verb.children if child.dep_ == "prt"]
    forms.update(f"{form} {' '.join(particles)}" for form in tuple(forms) if particles)
    return bool(forms & ATTRIBUTION_VERBS)


def _has_person_reference(verb: Any) -> bool:
    return any(
        token.lower_ in PERSON_REFERENCE_WORDS and token.dep_ != "nsubj"
        for token in verb.subtree
    )


def _descriptor_predicate(chunk: Any) -> Any:
    if chunk.root.dep_ in {"nsubj", "nsubjpass"}:
        return chunk.root.head
    if chunk.root.dep_ == "ROOT":
        return next(
            (
                child
                for child in chunk.root.children
                if child.pos_ == "VERB" and child.dep_ in {"acl", "relcl"}
            ),
            None,
        )
    return None


def _modern_nominal_mentions(
    attribution: Dict[str, object],
    character: Optional[Dict[str, object]],
) -> List[object]:
    if not character:
        return []
    trace = attribution.get("decisionTrace")
    if not isinstance(trace, dict):
        return []
    modern = trace.get("modernBookNLP")
    if not isinstance(modern, dict):
        return []
    selected = str(trace.get("selectedCandidate") or "").strip().casefold()
    character_surfaces = {
        str(value or "").strip().casefold()
        for value in [
            character.get("id"),
            character.get("name"),
            *(character.get("aliases") or []),
        ]
        if str(value or "").strip()
    }
    mentions = modern.get("nominalMentions")
    if selected not in character_surfaces or not isinstance(mentions, list):
        return []
    return mentions


def _extract_subject_descriptors(context: str, *, before_quote: bool) -> List[str]:
    nlp = _load_descriptor_nlp()
    if nlp is None:
        patterns = [
            re.compile(
                rf"(?:^|[.!?,]\s*){SUBJECT_PATTERN}\s+(?:{VERB_PATTERN})\b",
                re.I,
            )
        ]
        if before_quote:
            patterns.append(
                re.compile(
                    rf"(?:^|[.!?]\s*){SUBJECT_PATTERN}\s+[A-Za-z][A-Za-z'’-]*\s+"
                    rf"(?:{PERSON_REFERENCE_PATTERN})\b",
                    re.I,
                )
            )
        return list(dict.fromkeys(
            normalized
            for pattern in patterns
            for match in pattern.finditer(context)
            if (normalized := normalize_descriptor(match.group(1)))
        ))

    candidates: List[str] = []
    document = nlp(context)
    for chunk in document.noun_chunks:
        if not chunk or chunk[0].lower_ not in DESCRIPTOR_ARTICLES:
            continue
        predicate = _descriptor_predicate(chunk)
        if predicate is None or predicate.pos_ not in {"VERB", "AUX"}:
            continue
        if (
            not _verb_is_attribution(predicate)
            and not (
                before_quote
                and predicate.dep_ == "ROOT"
                and _has_person_reference(predicate)
            )
        ):
            continue
        normalized = normalize_descriptor(chunk.text)
        if normalized and normalized.casefold() not in {value.casefold() for value in candidates}:
            candidates.append(normalized)
    return candidates


def extract_nearby_descriptors(
    raw_text: str,
    span: object,
    dialogue_text: object = None,
) -> List[str]:
    """Extract speaker-like subject noun phrases adjacent to a dialogue span."""
    if not isinstance(span, dict):
        return []
    start = int(span.get("start") or 0)
    end = int(span.get("end") or 0)
    if start < 0 or end < start or end > len(raw_text):
        return []

    quote_text = str(dialogue_text or "").strip()
    if quote_text:
        occurrences = [match.start() for match in re.finditer(re.escape(quote_text), raw_text)]
        if occurrences:
            start = min(occurrences, key=lambda position: abs(position - start))
            end = start + len(quote_text)

    paragraph_start = max(raw_text.rfind("\n", 0, start), raw_text.rfind("\r", 0, start)) + 1
    newline_positions = [
        position
        for position in (raw_text.find("\n", end), raw_text.find("\r", end))
        if position >= 0
    ]
    paragraph_end = min(newline_positions) if newline_positions else len(raw_text)
    before = raw_text[paragraph_start:start]
    if before and re.fullmatch(QUOTE_MARKER_PATTERN, before[-1]):
        before = before[:-1]
    before = re.split(QUOTE_MARKER_PATTERN, before)[-1].strip(' \t\'‘’')[-200:]
    after = raw_text[end:paragraph_end]
    if after and re.fullmatch(QUOTE_MARKER_PATTERN, after[0]):
        after = after[1:]
    after = re.split(QUOTE_MARKER_PATTERN, after)[0].strip(' \t\'‘’')[:200]
    before_parts = re.split(r"(?<=[.!?])\s+", before)
    before = before_parts[-1] if before_parts else before
    after_parts = re.split(r"(?<=[.!?])\s+", after, maxsplit=1)
    after = after_parts[0] if after_parts else after

    candidates: List[str] = []
    contexts = [(before, True), (after, False)]
    if quote_text and not _span_starts_inside_quote(raw_text, paragraph_start, start):
        contexts.append((quote_text, False))
    for context, before_quote in contexts:
        for normalized in _extract_subject_descriptors(context, before_quote=before_quote):
            if normalized.casefold() not in {value.casefold() for value in candidates}:
                candidates.append(normalized)
    return candidates


def _write_json_atomic(path: str, payload: Dict[str, object]) -> None:
    directory = os.path.dirname(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        temporary_path = handle.name
    os.replace(temporary_path, path)


def review_chapter(book_root: str, chapter_name: str) -> Dict[str, object]:
    """Mark a chapter reviewed and learn descriptors from confirmed assignments."""
    root = os.path.abspath(book_root)
    chapter_dir = os.path.abspath(os.path.join(root, chapter_name))
    if os.path.commonpath([root, chapter_dir]) != root:
        raise ValueError("Invalid chapter path")

    dialogue_path = os.path.join(chapter_dir, "dialogue.json")
    text_path = os.path.join(chapter_dir, "chapter.txt")
    characters_path = os.path.join(root, "characters.json")
    for required_path in (dialogue_path, text_path, characters_path):
        if not os.path.isfile(required_path):
            raise ValueError(f"Required file not found: {required_path}")

    with open(dialogue_path, "r", encoding="utf-8") as handle:
        dialogue = json.load(handle)
    with open(text_path, "r", encoding="utf-8") as handle:
        raw_text = handle.read()
    with open(characters_path, "r", encoding="utf-8") as handle:
        characters = json.load(handle)

    lines = dialogue.get("lines") or []
    unresolved = [
        line.get("id")
        for line in lines
        if not line.get("isNonSpeaker")
        and not str(line.get("characterId") or "").strip()
    ]
    if unresolved:
        raise ValueError(f"Chapter has {len(unresolved)} unassigned lines")

    characters_by_id = {
        str(character.get("id") or "").strip(): character
        for character in characters.get("characters") or []
        if str(character.get("id") or "").strip()
    }
    identity_surfaces = {
        str(surface or "").strip().casefold()
        for character in characters_by_id.values()
        for surface in [
            character.get("id"),
            character.get("name"),
            *(character.get("aliases") or []),
        ]
        if str(surface or "").strip()
    }

    observations = 0
    additions = 0
    for line in lines:
        character_id = str(line.get("characterId") or "").strip()
        character = characters_by_id.get(character_id)
        attribution = line.get("attribution") if isinstance(line.get("attribution"), dict) else {}
        metadata = line.get("metadata") if isinstance(line.get("metadata"), dict) else {}
        custom_tags = (
            metadata.get("customTags")
            if isinstance(metadata.get("customTags"), dict)
            else {}
        )
        nested_attribution = (
            custom_tags.get("attribution")
            if isinstance(custom_tags.get("attribution"), dict)
            else {}
        )

        source_values: Iterable[object] = [
            *(attribution.get("sourceDescriptors") or []),
            *(nested_attribution.get("sourceDescriptors") or []),
            *_modern_nominal_mentions(attribution, character),
            *extract_nearby_descriptors(raw_text, line.get("span"), line.get("text")),
        ]
        learned: List[str] = []
        for source_value in source_values:
            descriptor = normalize_descriptor(source_value)
            if not descriptor or descriptor.casefold() in identity_surfaces:
                continue
            if descriptor.casefold() not in {value.casefold() for value in learned}:
                learned.append(descriptor)

        attribution["resolutionStatus"] = "user_confirmed"
        attribution["sourceDescriptors"] = learned
        line["attribution"] = attribution
        nested_attribution.update(attribution)
        custom_tags["attribution"] = nested_attribution
        custom_tags["sourceDescriptors"] = learned
        metadata["customTags"] = custom_tags
        line["metadata"] = metadata
        line["isConflict"] = False

        if not character or character_id == "narrator":
            continue
        descriptors = (
            character.get("descriptors")
            if isinstance(character.get("descriptors"), list)
            else []
        )
        known = {str(value).casefold() for value in descriptors}
        for descriptor in learned:
            observations += 1
            if descriptor.casefold() in known:
                continue
            descriptors.append(descriptor)
            known.add(descriptor.casefold())
            additions += 1
        character["descriptors"] = descriptors

    character_breakdown: Dict[str, int] = {}
    for line in lines:
        character_id = str(line.get("characterId") or "").strip()
        if character_id:
            character_breakdown[character_id] = character_breakdown.get(character_id, 0) + 1
    dialogue["stats"] = {
        **(dialogue.get("stats") if isinstance(dialogue.get("stats"), dict) else {}),
        "totalLines": len(lines),
        "conflicts": 0,
        "characterBreakdown": character_breakdown,
    }

    reviewed_at = datetime.now(timezone.utc).isoformat()
    dialogue["reviewed"] = True
    dialogue["reviewedAt"] = reviewed_at
    dialogue["reviewedLineCount"] = len(lines)
    _write_json_atomic(characters_path, characters)
    _write_json_atomic(dialogue_path, dialogue)
    return {
        "chapter": chapter_name,
        "reviewed": True,
        "reviewedAt": reviewed_at,
        "reviewedLineCount": len(lines),
        "descriptorObservations": observations,
        "descriptorsAdded": additions,
    }