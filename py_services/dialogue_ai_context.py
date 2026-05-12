"""Helpers for assembling deterministic chapter context for dialogue AI review."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from api_models import DialogueAiCharacterRef, DialogueJson, DialogueLine
from dialogue_ai_enrichment import (
    build_recent_turn_history,
    build_scene_alias_memory,
    extract_quote_structure,
)


def _normalize_alias(value: Any) -> str:
    return str(value or "").strip()


def _title_from_id(character_id: Optional[str]) -> Optional[str]:
    if not character_id:
        return None
    return str(character_id).replace("_", " ").replace("-", " ").title()


def _normalize_lookup(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _normalize_quote_match(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("â€™", "'")
    text = text.replace("\u2019", "'")
    text = re.sub(r"[\"â€œâ€]", "", text)
    text = re.sub(r"[\u201c\u201d]", "", text)
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalized_contains_phrase(normalized_text: str, normalized_phrase: str) -> bool:
    if not normalized_text or not normalized_phrase:
        return False
    phrase_pattern = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    return bool(
        re.search(
            rf"(?<![a-z0-9']){phrase_pattern}(?![a-z0-9'])",
            normalized_text,
        )
    )


def _quoted_text_units(value: Any) -> List[str]:
    text = str(value or "")
    if not text:
        return []
    pattern = re.compile(r'["â€œ\u201c](.+?)["â€\u201d]', re.DOTALL)
    return [_normalize_quote_match(match.group(1)) for match in pattern.finditer(text)]


def _strip_title_prefix(value: str) -> str:
    tokens = [token for token in str(value or "").split(" ") if token]
    while len(tokens) > 1 and tokens[0] in {"king", "queen", "lord", "lady", "sir", "head", "high"}:
        tokens = tokens[1:]
    return " ".join(tokens)


def _build_book_character_lookup(
    book_characters: List[DialogueAiCharacterRef],
) -> Dict[str, DialogueAiCharacterRef]:
    lookup: Dict[str, DialogueAiCharacterRef] = {}
    for character in book_characters:
        keys = {
            _normalize_lookup(character.characterId),
            _normalize_lookup(character.name),
            _normalize_lookup(_title_from_id(character.characterId)),
            *(_normalize_lookup(alias) for alias in character.aliases),
        }
        for key in keys:
            if key:
                lookup.setdefault(key, character)
    return lookup


def _resolve_book_character(
    character_id: str,
    book_lookup: Dict[str, DialogueAiCharacterRef],
) -> Optional[DialogueAiCharacterRef]:
    if not character_id:
        return None
    for key in (
        _normalize_lookup(character_id),
        _normalize_lookup(_title_from_id(character_id)),
    ):
        if key and key in book_lookup:
            return book_lookup[key]
        stripped_key = _strip_title_prefix(key)
        if stripped_key and stripped_key in book_lookup:
            return book_lookup[stripped_key]
    return None


def split_paragraphs(chapter_text: str) -> List[Dict[str, Any]]:
    """Split source text into paragraph ranges using the app's non-empty-line semantics."""

    text = str(chapter_text or "")
    if not text.strip():
        return []

    paragraphs: List[Dict[str, Any]] = []
    text_length = len(text)
    cursor = 0
    paragraph_index = 0

    while cursor < text_length:
        line_start = cursor
        line_end = line_start
        while line_end < text_length and text[line_end] not in {"\n", "\r"}:
            line_end += 1

        line_text = text[line_start:line_end]
        if line_text.strip():
            paragraphs.append(
                {
                    "index": paragraph_index,
                    "start": line_start,
                    "end": line_end,
                    "text": line_text,
                }
            )
            paragraph_index += 1

        next_line_start = line_end
        if next_line_start < text_length:
            has_windows_newline = (
                text[next_line_start] == "\r"
                and next_line_start + 1 < text_length
                and text[next_line_start + 1] == "\n"
            )
            if has_windows_newline:
                next_line_start += 2
            else:
                next_line_start += 1
        cursor = next_line_start

    return paragraphs


def _distance_to_paragraph(center: float, paragraph: Dict[str, Any]) -> float:
    if paragraph["start"] <= center <= paragraph["end"]:
        return 0.0
    return min(abs(center - paragraph["start"]), abs(center - paragraph["end"]))


def _span_paragraph_index(line: DialogueLine, paragraphs: Sequence[Dict[str, Any]]) -> Optional[int]:
    if not paragraphs or line.span is None:
        return None

    start = line.span.start
    end = line.span.end
    for paragraph in paragraphs:
        if start < paragraph["end"] and end > paragraph["start"]:
            return int(paragraph["index"])

    center = (start + end) / 2
    closest = min(paragraphs, key=lambda paragraph: _distance_to_paragraph(center, paragraph))
    return int(closest["index"])


def resolve_line_alignment(
    line: DialogueLine,
    paragraphs: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Resolve the paragraph for a dialogue line, preferring quote text over stale spans."""

    span_index = _span_paragraph_index(line, paragraphs)
    normalized_line = _normalize_quote_match(line.text)
    span_center = (line.span.start + line.span.end) / 2 if line.span is not None else 0.0

    if normalized_line:
        quoted_matches: List[Dict[str, Any]] = []
        for paragraph in paragraphs:
            if normalized_line not in _quoted_text_units(paragraph.get("text")):
                continue
            distance = _distance_to_paragraph(span_center, paragraph) if line.span else 0.0
            quoted_matches.append(
                {
                    "paragraphIndex": int(paragraph["index"]),
                    "method": "quote_unit",
                    "confidence": 1.0,
                    "spanParagraphIndex": span_index,
                    "spanDistance": distance,
                    "isSpanFallback": False,
                }
            )
        if quoted_matches:
            quoted_matches.sort(
                key=lambda item: (
                    item["spanDistance"],
                    abs(int(item["paragraphIndex"]) - int(span_index or item["paragraphIndex"])),
                )
            )
            best = quoted_matches[0]
            if span_index is not None and best["paragraphIndex"] != span_index:
                best["method"] = "quote_unit_span_corrected"
            return best

        matches: List[Dict[str, Any]] = []
        for paragraph in paragraphs:
            normalized_paragraph = _normalize_quote_match(paragraph.get("text"))
            if not _normalized_contains_phrase(normalized_paragraph, normalized_line):
                continue
            distance = _distance_to_paragraph(span_center, paragraph) if line.span else 0.0
            matches.append(
                {
                    "paragraphIndex": int(paragraph["index"]),
                    "method": "quote_text",
                    "confidence": 1.0 if len(normalized_line) >= 6 else 0.92,
                    "spanParagraphIndex": span_index,
                    "spanDistance": distance,
                    "isSpanFallback": False,
                }
            )

        if matches:
            matches.sort(
                key=lambda item: (
                    item["spanDistance"],
                    abs(int(item["paragraphIndex"]) - int(span_index or item["paragraphIndex"])),
                )
            )
            best = matches[0]
            if span_index is not None and best["paragraphIndex"] != span_index:
                best["method"] = "quote_text_span_corrected"
            return best

    if span_index is not None:
        return {
            "paragraphIndex": span_index,
            "method": "span_fallback",
            "confidence": 0.45,
            "spanParagraphIndex": span_index,
            "spanDistance": 0.0,
            "isSpanFallback": True,
        }

    return {
        "paragraphIndex": None,
        "method": "unresolved",
        "confidence": 0.0,
        "spanParagraphIndex": None,
        "spanDistance": None,
        "isSpanFallback": True,
    }


def find_paragraph_index(line: DialogueLine, paragraphs: List[Dict[str, Any]]) -> Optional[int]:
    """Resolve the best matching paragraph index for a line span."""

    return resolve_line_alignment(line, paragraphs).get("paragraphIndex")


def load_book_characters(book_root: Optional[str]) -> List[DialogueAiCharacterRef]:
    """Load canonical book characters from the active book root when available."""

    if not book_root:
        return []

    characters_path = Path(book_root) / "characters.json"
    if not characters_path.exists():
        return []

    try:
        data = json.loads(characters_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []

    characters: List[DialogueAiCharacterRef] = []
    for raw_character in data.get("characters", []):
        character_id = str(raw_character.get("id") or "").strip()
        name = str(raw_character.get("name") or character_id).strip()
        if not character_id or not name:
            continue
        aliases = [_normalize_alias(alias) for alias in raw_character.get("aliases", [])]
        aliases = [alias for alias in aliases if alias]
        role_labels = [_normalize_alias(label) for label in raw_character.get("roleLabels", [])]
        role_labels = [label for label in role_labels if label]
        notes = str(raw_character.get("notes") or "").strip() or None
        characters.append(
            DialogueAiCharacterRef(
                characterId=character_id,
                name=name,
                aliases=aliases,
                roleLabels=role_labels,
                notes=notes,
            )
        )
    return characters


def build_chapter_characters(
    dialogue: DialogueJson,
    book_characters: List[DialogueAiCharacterRef],
) -> List[DialogueAiCharacterRef]:
    """Build the chapter-level character set from current assignments."""

    by_id = {character.characterId: character for character in book_characters}
    by_lookup = _build_book_character_lookup(book_characters)
    seen: Dict[str, DialogueAiCharacterRef] = {}
    for line in dialogue.lines:
        character_id = str(line.characterId or "").strip()
        if not character_id or character_id == "narrator":
            continue
        if character_id in seen:
            continue
        if character_id in by_id:
            seen[character_id] = by_id[character_id]
            continue
        resolved = _resolve_book_character(character_id, by_lookup)
        if resolved is not None:
            seen[character_id] = resolved
            continue
        seen[character_id] = DialogueAiCharacterRef(
            characterId=character_id,
            name=_title_from_id(character_id) or character_id,
            aliases=[],
        )
    return list(seen.values())


def build_target_lines(dialogue: DialogueJson) -> List[DialogueLine]:
    """Return only currently assigned non-narrator lines for phase-one review."""

    return [
        line
        for line in dialogue.lines
        if line.characterId not in (None, "", "narrator")
    ]


def _character_name_for_id(
    character_id: Optional[str],
    chapter_characters: Sequence[DialogueAiCharacterRef],
) -> Optional[str]:
    if not character_id:
        return None
    for character in chapter_characters:
        if character.characterId == character_id:
            return character.name
    return _title_from_id(character_id)


def _build_scene_roster(
    lines: Sequence[Dict[str, Any]],
    current_index: int,
    chapter_characters: Sequence[DialogueAiCharacterRef],
    paragraph_window: int = 6,
) -> Dict[str, Any]:
    target = lines[current_index]
    current_paragraph_index = target.get("paragraphIndex")
    present_ids: List[str] = []

    def add_character(character_id: Optional[str]) -> None:
        normalized = str(character_id or "").strip()
        if not normalized or normalized == "narrator" or normalized in present_ids:
            return
        present_ids.append(normalized)

    if current_paragraph_index is not None:
        for candidate in lines:
            candidate_paragraph_index = candidate.get("paragraphIndex")
            if candidate_paragraph_index is None:
                continue
            if abs(int(candidate_paragraph_index) - int(current_paragraph_index)) <= paragraph_window:
                add_character(getattr(candidate.get("line"), "characterId", None))

    for candidate in lines[max(0, current_index - 4): min(len(lines), current_index + 3)]:
        add_character(getattr(candidate.get("line"), "characterId", None))

    return {
        "paragraphWindow": paragraph_window,
        "presentCharacterIds": present_ids,
        "presentCharacters": [
            {
                "characterId": character_id,
                "characterName": _character_name_for_id(character_id, chapter_characters),
            }
            for character_id in present_ids
        ],
    }


def build_assist_context(
    book_root: Optional[str],
    chapter_text: str,
    dialogue: DialogueJson,
) -> Dict[str, Any]:
    """Assemble paragraph and character context for dialogue AI assist requests."""

    paragraphs = split_paragraphs(chapter_text)
    book_characters = load_book_characters(book_root)
    chapter_characters = build_chapter_characters(dialogue, book_characters)
    known_surfaces = [character.name for character in chapter_characters] + [character.name for character in book_characters]
    paragraph_by_index = {int(paragraph["index"]): paragraph for paragraph in paragraphs}
    scene_alias_memory = build_scene_alias_memory(chapter_text, chapter_characters, book_characters)

    target_lines = build_target_lines(dialogue)
    alignments_by_line_id = {
        line.id: resolve_line_alignment(line, paragraphs)
        for line in target_lines
    }

    paragraph_lines: Dict[int, List[DialogueLine]] = {}
    for line in target_lines:
        paragraph_index = alignments_by_line_id[line.id].get("paragraphIndex")
        if paragraph_index is None:
            continue
        paragraph_lines.setdefault(paragraph_index, []).append(line)

    lines: List[Dict[str, Any]] = []
    for target_index, line in enumerate(target_lines):
        alignment = alignments_by_line_id[line.id]
        paragraph_index = alignment.get("paragraphIndex")
        current_paragraph = (
            paragraph_by_index.get(paragraph_index)
            if paragraph_index is not None
            else None
        )
        previous_paragraph = (
            paragraph_by_index.get(paragraph_index - 1)
            if paragraph_index is not None
            else None
        )
        next_paragraph = (
            paragraph_by_index.get(paragraph_index + 1)
            if paragraph_index is not None
            else None
        )
        lines.append(
            {
                "line": line,
                "targetIndex": target_index,
                "paragraphIndex": paragraph_index,
                "alignment": alignment,
                "currentParagraphText": (
                    current_paragraph["text"] if current_paragraph else line.text
                ),
                "previousParagraphText": previous_paragraph["text"] if previous_paragraph else None,
                "nextParagraphText": next_paragraph["text"] if next_paragraph else None,
                "currentParagraphLines": paragraph_lines.get(paragraph_index, []),
                "previousParagraphLines": paragraph_lines.get((paragraph_index - 1) if paragraph_index is not None else -1, []),
                "nextParagraphLines": paragraph_lines.get((paragraph_index + 1) if paragraph_index is not None else -1, []),
                "currentQuoteStructure": extract_quote_structure(
                    current_paragraph["text"] if current_paragraph else line.text,
                    known_surfaces,
                ),
                "previousQuoteStructure": extract_quote_structure(
                    previous_paragraph["text"] if previous_paragraph else "",
                    known_surfaces,
                ),
                "nextQuoteStructure": extract_quote_structure(
                    next_paragraph["text"] if next_paragraph else "",
                    known_surfaces,
                ),
            }
        )

    for index, target in enumerate(lines):
        target["recentTurnHistory"] = build_recent_turn_history(
            lines,
            index,
            chapter_characters,
        )
        target["sceneRoster"] = _build_scene_roster(
            lines,
            index,
            chapter_characters,
        )

    return {
        "paragraphs": paragraphs,
        "chapterCharacters": chapter_characters,
        "bookCharacters": book_characters,
        "sceneAliasMemory": scene_alias_memory,
        "targets": lines,
        "skippedNarratorLines": max(0, len(dialogue.lines) - len(lines)),
    }
