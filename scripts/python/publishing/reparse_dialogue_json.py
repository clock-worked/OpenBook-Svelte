from __future__ import annotations

import argparse
import json
import math
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_PYTHON_DIR = REPO_ROOT / "scripts" / "python"
PY_SERVICES_DIR = REPO_ROOT / "py_services"
for path in (SCRIPTS_PYTHON_DIR, PY_SERVICES_DIR):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from openbook_parser.booknlp_parser_service import BookNLPParserService  # noqa: E402
from openbook_parser.dialogue_parser_service import build_paragraph_ranges, find_paragraph_index_for_offset  # noqa: E402
from update_character_stats import update_character_stats  # noqa: E402


BLOCKED_CHARACTER_NAMES = {
    "he",
    "she",
    "as",
    "it",
    "that",
    "they",
    "them",
    "the",
    "this",
    "these",
    "those",
}
FORCED_BLOCKED_SPEAKERS = {"he", "she", "as"}
SPEECH_VERBS = {
    "said",
    "asked",
    "replied",
    "answered",
    "muttered",
    "shouted",
    "whispered",
    "snapped",
    "yelled",
    "growled",
    "sighed",
    "hissed",
    "called",
    "told",
    "added",
    "continued",
    "remarked",
    "noted",
    "stated",
    "declared",
    "insisted",
}


def read_text_exact(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text_exact(path))


def normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def is_blocked_character_token(value: Any) -> bool:
    return normalize_token(value) in BLOCKED_CHARACTER_NAMES


def slugify_character_id(value: Any) -> str:
    text = normalize_token(value)
    if not text:
        return "narrator"
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "narrator"


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(number):
        return 0.5
    if number < 0.15:
        return 0.15
    if number > 1:
        return 1
    return number


def line_segments_to_text(line: Any, *, pauses_as_space: bool = False) -> str:
    parts: list[str] = []
    for segment in getattr(line, "segments", []) or []:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") == "text":
            parts.append(str(segment.get("value") or ""))
        elif segment.get("type") == "pause":
            if pauses_as_space:
                parts.append(" ")
            else:
                duration = segment.get("duration") or "medium"
                if duration == "short":
                    parts.append("[pause short]")
                elif duration == "long":
                    parts.append("[pause long]")
                else:
                    parts.append("[pause]")
    return "".join(parts)


def count_words(text: str) -> int:
    cleaned = str(text or "").strip()
    return 0 if not cleaned else len(re.split(r"\s+", cleaned))


def has_speech_verb(text: str) -> bool:
    lower = f" {str(text or '').lower()} "
    return any(f" {verb} " in lower for verb in SPEECH_VERBS)


def estimate_dialogue_confidence(line: Any, index: int, all_lines: list[Any], source_suggestions: list[str]) -> float:
    if getattr(line, "line_type", None) != "dialogue":
        return 1

    confidence = 0.62 if getattr(line, "is_suggestion", False) else 0.98
    if len(source_suggestions) > 1:
        confidence -= 0.06
    if not source_suggestions:
        confidence -= 0.04

    previous = all_lines[index - 1] if index - 1 >= 0 else None
    previous_previous = all_lines[index - 2] if index - 2 >= 0 else None
    next_line = all_lines[index + 1] if index + 1 < len(all_lines) else None

    if getattr(previous, "line_type", None) == "dialogue":
        confidence -= 0.05
    if getattr(next_line, "line_type", None) == "dialogue":
        confidence -= 0.03

    if getattr(previous, "line_type", None) == "narration":
        previous_text = line_segments_to_text(previous, pauses_as_space=True)
        previous_lower = previous_text.lower()
        speaker_lower = normalize_token(getattr(line, "speaker", ""))
        mentions_speaker = bool(speaker_lower and speaker_lower in previous_lower)
        verb_nearby = has_speech_verb(previous_text)
        previous_words = count_words(previous_text)

        if mentions_speaker and verb_nearby:
            if getattr(previous_previous, "line_type", None) == "dialogue":
                confidence = min(confidence, 0.95)
            else:
                confidence = max(confidence, 0.99)

        if "\n\n" in previous_text:
            confidence -= 0.08
        if previous_words >= 24:
            confidence -= 0.08

    return clamp_confidence(confidence)


def merge_reason_lists(*lists: list[str] | None) -> list[str] | None:
    merged: list[str] = []
    seen: set[str] = set()
    for values in lists:
        for reason in values or []:
            value = str(reason or "").strip()
            if value and value not in seen:
                seen.add(value)
                merged.append(value)
    return merged or None


def collapse_mapped_candidates(
    raw_candidates: list[dict[str, Any]],
    name_to_character_id: dict[str, str],
    id_to_name: dict[str, str],
) -> list[dict[str, Any]]:
    collapsed: dict[str, dict[str, Any]] = {}

    for index, candidate in enumerate(raw_candidates):
        name = str(candidate.get("name") or "").strip()
        normalized_name = normalize_token(name)
        if not normalized_name or is_blocked_character_token(normalized_name):
            continue

        mapped_id = name_to_character_id.get(normalized_name)
        canonical_name = id_to_name.get(normalize_token(mapped_id), name) if mapped_id else name
        key = f"id:{normalize_token(mapped_id)}" if mapped_id else f"name:{normalized_name}"
        reasons = [str(reason) for reason in candidate.get("reasons") or [] if str(reason or "").strip()]

        if key not in collapsed:
            collapsed[key] = {
                "name": canonical_name,
                "characterId": mapped_id,
                "confidence": clamp_confidence(candidate.get("confidence")),
                "reasons": merge_reason_lists(reasons),
                "order": index,
            }
            continue

        existing = collapsed[key]
        confidence = clamp_confidence(candidate.get("confidence"))
        if confidence > existing["confidence"]:
            existing["name"] = canonical_name
            existing["confidence"] = confidence
        existing["characterId"] = existing.get("characterId") or mapped_id
        existing["reasons"] = merge_reason_lists(existing.get("reasons"), reasons)
        existing["order"] = min(existing["order"], index)

    rows = sorted(collapsed.values(), key=lambda item: (-float(item["confidence"]), int(item["order"])))
    for row in rows:
        row.pop("order", None)
    return rows


def load_character_maps(book_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    characters_path = book_root / "characters.json"
    name_to_character_id = {"narrator": "narrator"}
    id_to_name = {"narrator": "Narrator"}
    if not characters_path.exists():
        return name_to_character_id, id_to_name

    payload = load_json(characters_path)
    for character in payload.get("characters", []):
        if not isinstance(character, dict):
            continue
        character_id = str(character.get("id") or "").strip()
        name = str(character.get("name") or "").strip()
        if character_id and name:
            name_to_character_id[normalize_token(name)] = character_id
            id_to_name[normalize_token(character_id)] = name
        if not character_id:
            continue
        for alias in character.get("aliases") or []:
            key = normalize_token(alias)
            if key and key not in name_to_character_id:
                name_to_character_id[key] = character_id

    return name_to_character_id, id_to_name


def parser_options_from_settings(book_root: Path) -> dict[str, Any]:
    settings_path = book_root / "settings.json"
    if not settings_path.exists():
        return {}

    settings = load_json(settings_path)
    hints = settings.get("parserHints") if isinstance(settings.get("parserHints"), dict) else settings
    if not isinstance(hints, dict):
        return {}

    protagonist_names = hints.get("protagonistNames")
    if not isinstance(protagonist_names, list) or not protagonist_names:
        protagonist = str(hints.get("protagonistName") or "").strip()
        protagonist_names = [protagonist] if protagonist else []

    raw_heuristics = hints.get("heuristics") if isinstance(hints.get("heuristics"), dict) else {}
    heuristics = {
        "protagonist_first_person_tag": raw_heuristics.get("protagonistFirstPersonTag", True),
        "narrator_identity": raw_heuristics.get("narratorIdentity", True),
        "coreference": raw_heuristics.get("coreference", True),
        "explicit_tags": raw_heuristics.get("explicitTags", True),
        "tag_continuation": raw_heuristics.get("tagContinuation", True),
        "contiguous_dialogue": raw_heuristics.get("contiguousDialogue", True),
        "carry_across_short_narration": raw_heuristics.get("carryAcrossShortNarration", True),
        "suggest_alternatives": raw_heuristics.get("suggestAlternatives", True),
        "first_person_override": raw_heuristics.get("firstPersonOverride", True),
        "narrator_fallback": raw_heuristics.get("narratorFallback", True),
        "vocative_guard": raw_heuristics.get("vocativeGuard", True),
    }
    return {
        "pov_mode": hints.get("povMode") or "first_person",
        "protagonists": protagonist_names,
        "learn_verbs": hints.get("learnVerbs", False),
        "heuristics": heuristics,
    }


def compute_returning_flags(parser_lines: list[Any], raw_text: str) -> list[bool]:
    paragraph_ranges = build_paragraph_ranges(raw_text)
    paragraph_indexes: list[int] = []
    paragraphs_with_dialogue: set[int] = set()
    last_entry_by_paragraph: dict[int, int] = {}

    for index, line in enumerate(parser_lines):
        paragraph_index = find_paragraph_index_for_offset(paragraph_ranges, getattr(line, "span_start", None))
        paragraph_indexes.append(paragraph_index)
        if paragraph_index >= 0:
            last_entry_by_paragraph[paragraph_index] = index
        if paragraph_index >= 0 and getattr(line, "line_type", None) == "dialogue":
            paragraphs_with_dialogue.add(paragraph_index)

    return [
        paragraph_index >= 0
        and paragraph_index in paragraphs_with_dialogue
        and last_entry_by_paragraph.get(paragraph_index) == index
        for index, paragraph_index in enumerate(paragraph_indexes)
    ]


def convert_line_to_dialogue_line(
    line: Any,
    line_id: int,
    name_to_character_id: dict[str, str],
    id_to_name: dict[str, str],
    index: int,
    all_lines: list[Any],
    is_returning: bool,
) -> dict[str, Any]:
    is_narration = getattr(line, "line_type", None) == "narration"
    text = line_segments_to_text(line)
    speaker = str(getattr(line, "speaker", "") or "").strip()
    speaker_key = normalize_token(speaker)
    speaker_is_blocked = is_blocked_character_token(speaker_key)
    parser_attribution = getattr(line, "attribution", None)

    if is_narration:
        source_suggestions: list[str] = []
    else:
        source_suggestions = []
        if isinstance(parser_attribution, dict) and isinstance(parser_attribution.get("candidates"), list):
            source_suggestions = [
                str(candidate.get("name") or "").strip()
                for candidate in parser_attribution["candidates"]
                if isinstance(candidate, dict) and str(candidate.get("name") or "").strip()
            ]
        if not source_suggestions:
            source_suggestions = [
                str(name or "").strip()
                for name in (getattr(line, "suggestions", []) or [])
                if str(name or "").strip()
            ]
        if not source_suggestions and speaker:
            source_suggestions = [speaker]
        source_suggestions = [name for name in source_suggestions if not is_blocked_character_token(name)]

    estimated_confidence = estimate_dialogue_confidence(line, index, all_lines, source_suggestions)
    if (
        not is_narration
        and isinstance(parser_attribution, dict)
        and isinstance(parser_attribution.get("candidates"), list)
        and parser_attribution["candidates"]
    ):
        raw_candidates = [
            {
                "name": str(candidate.get("name") or "").strip(),
                "confidence": clamp_confidence(candidate.get("confidence")),
                "reasons": [
                    str(reason)
                    for reason in candidate.get("reasons", [])
                    if str(reason or "").strip()
                ],
            }
            for candidate in parser_attribution["candidates"]
            if isinstance(candidate, dict) and str(candidate.get("name") or "").strip()
        ]
    else:
        raw_candidates = [
            {"name": name, "confidence": max(0.15, estimated_confidence - (candidate_index * 0.1))}
            for candidate_index, name in enumerate(source_suggestions)
        ]

    collapsed_candidates = collapse_mapped_candidates(raw_candidates, name_to_character_id, id_to_name)
    base_confidence = collapsed_candidates[0]["confidence"] if collapsed_candidates else estimated_confidence
    has_named_candidates = any(candidate.get("characterId") for candidate in collapsed_candidates)
    should_abstain = (
        not is_narration
        and (
            bool(getattr(line, "is_suggestion", False))
            or base_confidence < 0.74
            or (len(collapsed_candidates) > 1 and base_confidence < 0.86)
            or not has_named_candidates
            or speaker_is_blocked
        )
    )

    resolved_speaker_id = name_to_character_id.get(speaker_key) if speaker_key else None
    if is_narration:
        character_id = "narrator"
    elif should_abstain:
        character_id = None
    else:
        character_id = resolved_speaker_id

    candidates = [
        {"characterId": candidate["characterId"], "confidence": candidate["confidence"]}
        for candidate in collapsed_candidates
        if candidate.get("characterId")
    ]
    top_confidence = collapsed_candidates[0]["confidence"] if collapsed_candidates else base_confidence
    second_confidence = collapsed_candidates[1]["confidence"] if len(collapsed_candidates) > 1 else 0
    margin_to_second = max(0.0, top_confidence - second_confidence)
    misattribution_risk = clamp_confidence((1 - top_confidence) * 0.7 + (1 - margin_to_second) * 0.3)
    span_start = getattr(line, "span_start", None)
    span_end = getattr(line, "span_end", None)
    span = (
        {"start": int(span_start), "end": int(span_end)}
        if isinstance(span_start, int) and isinstance(span_end, int)
        else None
    )

    if is_narration:
        attribution = {
            "confidence": 1,
            "topCandidateConfidence": 1,
            "marginToSecond": 1,
            "misattributionRisk": 0,
            "resolutionStatus": "auto",
            "thresholdUsed": 0.62,
            "sourceAlias": "Narrator",
            "sourceCandidates": ["Narrator"],
            "candidates": [
                {
                    "characterId": "narrator",
                    "name": "Narrator",
                    "confidence": 1,
                    "reasons": ["non_quoted_narration"],
                }
            ],
        }
    else:
        attribution = {
            "confidence": top_confidence,
            "topCandidateConfidence": top_confidence,
            "marginToSecond": margin_to_second,
            "misattributionRisk": misattribution_risk,
            "resolutionStatus": "unknown" if should_abstain else "auto",
            "thresholdUsed": 0.62,
            "sourceAlias": speaker or None,
            "sourceCandidates": source_suggestions,
            "contextGender": parser_attribution.get("contextGender") if isinstance(parser_attribution, dict) else None,
            "contextGenderCue": parser_attribution.get("contextGenderCue") if isinstance(parser_attribution, dict) else None,
            "genderConflict": parser_attribution.get("genderConflict", False) if isinstance(parser_attribution, dict) else False,
            "parserBackend": parser_attribution.get("parserBackend") if isinstance(parser_attribution, dict) else None,
            "decisionTrace": parser_attribution.get("decisionTrace") if isinstance(parser_attribution, dict) else None,
            "candidates": [
                {
                    "characterId": candidate.get("characterId"),
                    "name": candidate.get("name"),
                    "confidence": candidate.get("confidence"),
                    "reasons": candidate.get("reasons"),
                }
                for candidate in collapsed_candidates
            ],
        }

    return {
        "id": line_id,
        "characterId": character_id,
        "text": text,
        "span": span,
        "metadata": {
            "emotion": None,
            "intensity": 0.5,
            "pacing": None,
            "prefix": None,
            "customTags": {
                "sourceAlias": speaker or None,
                "sourceCandidates": source_suggestions,
            },
        },
        "candidates": candidates,
        "isConflict": False if is_narration else (bool(getattr(line, "is_suggestion", False)) or should_abstain),
        "isReturning": is_returning,
        "attribution": attribution,
    }


def build_dialogue_json(
    chapter_dir: Path,
    parser_lines: list[Any],
    raw_text: str,
    name_to_character_id: dict[str, str],
    id_to_name: dict[str, str],
) -> dict[str, Any]:
    returning_flags = compute_returning_flags(parser_lines, raw_text)
    lines = [
        convert_line_to_dialogue_line(
            line,
            index + 1,
            name_to_character_id,
            id_to_name,
            index,
            parser_lines,
            returning_flags[index],
        )
        for index, line in enumerate(parser_lines)
    ]

    character_breakdown: dict[str, int] = {}
    for line in lines:
        character_id = line.get("characterId")
        if character_id:
            character_breakdown[character_id] = character_breakdown.get(character_id, 0) + 1

    return {
        "formatVersion": "3.2",
        "chapterId": chapter_dir.name,
        "lines": lines,
        "stats": {
            "totalLines": len(lines),
            "conflicts": sum(1 for line in lines if line.get("isConflict")),
            "characterBreakdown": character_breakdown,
        },
    }


def find_chapter_dirs(book_root: Path, start: int, end: int) -> list[Path]:
    chapters: list[tuple[int, Path]] = []
    for child in book_root.iterdir():
        if not child.is_dir():
            continue
        match = re.match(r"^(\d+)\b", child.name)
        if not match:
            continue
        number = int(match.group(1))
        if start <= number <= end and (child / "chapter.txt").exists():
            chapters.append((number, child))
    return [path for _, path in sorted(chapters, key=lambda item: item[0])]


def reparse_chapter(
    chapter_dir: Path,
    parser_service: BookNLPParserService,
    parser_options: dict[str, Any],
    name_to_character_id: dict[str, str],
    id_to_name: dict[str, str],
) -> dict[str, Any]:
    chapter_path = chapter_dir / "chapter.txt"
    raw_text = read_text_exact(chapter_path)
    parser_lines, _character_names, _parse_meta = parser_service.parse_file(
        str(chapter_path),
        options=deepcopy(parser_options),
        manual_blocklist=sorted(FORCED_BLOCKED_SPEAKERS),
        source_path=str(chapter_path),
    )
    return build_dialogue_json(chapter_dir, parser_lines, raw_text, name_to_character_id, id_to_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reparse chapter.txt files and rewrite dialogue.json for a chapter range."
    )
    parser.add_argument("book_root", type=Path, help="Book folder containing chapter subfolders.")
    parser.add_argument("--start", type=int, default=1, help="First numbered chapter folder to process. Default: 1")
    parser.add_argument("--end", type=int, default=20, help="Last numbered chapter folder to process. Default: 20")
    parser.add_argument("--apply", action="store_true", help="Write dialogue.json files. Dry-run without this flag.")
    parser.add_argument(
        "--refresh-character-stats",
        action="store_true",
        help="After --apply, refresh central character stats from dialogue.json files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    book_root = args.book_root.resolve()
    if not book_root.exists():
        raise FileNotFoundError(book_root)
    if args.start > args.end:
        raise ValueError("--start must be less than or equal to --end")

    chapter_dirs = find_chapter_dirs(book_root, args.start, args.end)
    if not chapter_dirs:
        print(f"No chapter folders {args.start}-{args.end} with chapter.txt found under {book_root}")
        return 1

    parser_options = parser_options_from_settings(book_root)
    name_to_character_id, id_to_name = load_character_maps(book_root)
    parser_service = BookNLPParserService()

    changed = 0
    for chapter_dir in chapter_dirs:
        dialogue_path = chapter_dir / "dialogue.json"
        existing_lines = None
        if dialogue_path.exists():
            try:
                existing_lines = len(load_json(dialogue_path).get("lines") or [])
            except (OSError, json.JSONDecodeError):
                existing_lines = None

        dialogue_json = reparse_chapter(
            chapter_dir,
            parser_service,
            parser_options,
            name_to_character_id,
            id_to_name,
        )
        new_lines = len(dialogue_json["lines"])
        conflict_count = int(dialogue_json["stats"]["conflicts"])
        dialogue_count = sum(1 for line in dialogue_json["lines"] if line.get("characterId") != "narrator")
        existing_label = "unknown" if existing_lines is None else str(existing_lines)
        action = "updated" if args.apply else "would update"
        print(
            f"{action}: {chapter_dir.name} "
            f"(lines {existing_label} -> {new_lines}, dialogue/non-narrator {dialogue_count}, conflicts {conflict_count})"
        )

        changed += 1
        if args.apply:
            write_json(dialogue_path, dialogue_json)

    if args.apply and args.refresh_character_stats:
        result = update_character_stats(book_root)
        if result.get("success"):
            print(f"Updated character stats for {result.get('updatedCharacters', 0)} character(s).")
        else:
            print(f"Character stats update failed: {result.get('error')}", file=sys.stderr)
            return 1

    if args.apply:
        print(f"Done. Updated {changed} dialogue.json file(s).")
    else:
        print(f"Dry run. Would update {changed} dialogue.json file(s). Add --apply to write changes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
