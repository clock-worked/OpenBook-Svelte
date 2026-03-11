#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any


def text_len(value: Any) -> int:
    if not isinstance(value, str):
        return 0
    return len(value.strip())


def is_dialogue_line(line: dict[str, Any], narrator_id: str) -> bool:
    character_id = line.get("characterId")
    return isinstance(character_id, str) and character_id != narrator_id


def is_narration_line(line: dict[str, Any], narrator_id: str) -> bool:
    return line.get("characterId") == narrator_id


def matches_role(line: dict[str, Any], role: str, narrator_id: str) -> bool:
    if role == "dialogue":
        return is_dialogue_line(line, narrator_id)
    if role == "narration":
        return is_narration_line(line, narrator_id)
    return True


def find_same_speaker_followup(
    *,
    lines: list[Any],
    first_character: str | None,
    narrator_id: str,
    start_index: int,
    followup_window: int,
    followup_scope: str,
) -> dict[str, Any] | None:
    if not isinstance(first_character, str) or not first_character:
        return None

    if followup_scope == "next-dialogue":
        for look_ahead in range(start_index, len(lines)):
            candidate = lines[look_ahead]
            if not isinstance(candidate, dict):
                continue
            if is_narration_line(candidate, narrator_id):
                continue
            if not is_dialogue_line(candidate, narrator_id):
                continue
            if candidate.get("characterId") != first_character:
                return None
            return {
                "lineIndex": look_ahead,
                "lineId": candidate.get("id"),
                "characterId": candidate.get("characterId"),
                "textLength": text_len(candidate.get("text", "")),
                "text": candidate.get("text", ""),
            }
        return None

    for look_ahead in range(start_index, min(start_index + followup_window, len(lines))):
        candidate = lines[look_ahead]
        if not isinstance(candidate, dict):
            continue
        if candidate.get("characterId") != first_character:
            continue
        if is_narration_line(candidate, narrator_id):
            continue

        return {
            "lineIndex": look_ahead,
            "lineId": candidate.get("id"),
            "characterId": candidate.get("characterId"),
            "textLength": text_len(candidate.get("text", "")),
            "text": candidate.get("text", ""),
        }

    return None


def scan_dialogue_file(
    dialogue_path: Path,
    short_dialogue_max: int,
    long_narration_max: int,
    narrator_id: str,
    followup_window: int,
    followup_scope: str,
    first_role: str,
    second_role: str,
) -> list[dict[str, Any]]:
    data = json.loads(dialogue_path.read_text(encoding="utf-8"))
    lines = data.get("lines", [])
    if not isinstance(lines, list):
        return []

    chapter_id = data.get("chapterId") or dialogue_path.parent.name
    hits: list[dict[str, Any]] = []

    for index in range(len(lines) - 1):
        first = lines[index]
        second = lines[index + 1]

        if not isinstance(first, dict) or not isinstance(second, dict):
            continue

        if not matches_role(first, first_role, narrator_id):
            continue
        if not matches_role(second, second_role, narrator_id):
            continue

        first_text = first.get("text", "")
        second_text = second.get("text", "")
        first_len = text_len(first_text)
        second_len = text_len(second_text)

        if first_len >= short_dialogue_max:
            continue
        if second_len >= long_narration_max:
            continue

        first_character = first.get("characterId")
        followup = find_same_speaker_followup(
            lines=lines,
            first_character=first_character,
            narrator_id=narrator_id,
            start_index=index + 2,
            followup_window=followup_window,
            followup_scope=followup_scope,
        )

        hit = {
            "chapterId": chapter_id,
            "dialoguePath": str(dialogue_path),
            "firstDialogue": {
                "lineIndex": index,
                "lineId": first.get("id"),
                "characterId": first_character,
                "textLength": first_len,
                "text": first_text,
            },
            "followingNarration": {
                "lineIndex": index + 1,
                "lineId": second.get("id"),
                "characterId": second.get("characterId"),
                "textLength": second_len,
                "text": second_text,
            },
            "sameSpeakerFollowup": followup,
        }
        hits.append(hit)

    return hits


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find short dialogue lines followed by longer narration in Book-14 dialogue.json files, "
            "with optional same-speaker follow-up dialogue."
        )
    )
    parser.add_argument(
        "--book-dir",
        type=Path,
        default=Path(
            "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14"
        ),
        help="Path to Book-14 directory",
    )
    parser.add_argument(
        "--short-dialogue-max",
        type=int,
        default=15,
        help="Dialogue line must be shorter than this many chars",
    )
    parser.add_argument(
        "--long-narration-max",
        type=int,
        default=100,
        help="Following narration must be shorter than this many chars",
    )
    parser.add_argument(
        "--first-role",
        choices=["dialogue", "narration", "any"],
        default="dialogue",
        help="Role required for the first line in the pair.",
    )
    parser.add_argument(
        "--second-role",
        choices=["dialogue", "narration", "any"],
        default="narration",
        help="Role required for the second line in the pair.",
    )
    parser.add_argument(
        "--narrator-id",
        type=str,
        default="narrator",
        help="Character ID used for narration lines",
    )
    parser.add_argument(
        "--followup-window",
        type=int,
        default=6,
        help="How many lines after narration to scan for same-speaker dialogue",
    )
    parser.add_argument(
        "--followup-scope",
        choices=["window", "next-dialogue"],
        default="window",
        help=(
            "How follow-up is evaluated: 'window' scans N lines; 'next-dialogue' only checks "
            "the very next non-narrator dialogue line after narration."
        ),
    )
    followup_filter = parser.add_mutually_exclusive_group()
    followup_filter.add_argument(
        "--require-followup",
        action="store_true",
        help="Keep only matches that include a same-speaker follow-up dialogue line.",
    )
    followup_filter.add_argument(
        "--require-no-followup",
        action="store_true",
        help="Keep only matches where no same-speaker follow-up dialogue line is found.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("C:/tmp/book14_short_dialogue_then_narration_hits.json"),
        help="Output JSON report path",
    )
    args = parser.parse_args()

    dialogue_files = sorted(args.book_dir.glob("*/dialogue.json"))
    all_hits: list[dict[str, Any]] = []

    for dialogue_file in dialogue_files:
        all_hits.extend(
            scan_dialogue_file(
                dialogue_path=dialogue_file,
                short_dialogue_max=args.short_dialogue_max,
                long_narration_max=args.long_narration_max,
                narrator_id=args.narrator_id,
                followup_window=args.followup_window,
                followup_scope=args.followup_scope,
                first_role=args.first_role,
                second_role=args.second_role,
            )
        )

    if args.require_followup:
        all_hits = [item for item in all_hits if item.get("sameSpeakerFollowup")]
    elif args.require_no_followup:
        all_hits = [item for item in all_hits if not item.get("sameSpeakerFollowup")]

    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "bookDir": str(args.book_dir),
                "shortDialogueMaxExclusive": args.short_dialogue_max,
                "longNarrationMaxExclusive": args.long_narration_max,
                "followupWindow": args.followup_window,
                "followupScope": args.followup_scope,
                "firstRole": args.first_role,
                "secondRole": args.second_role,
                "requireFollowup": bool(args.require_followup),
                "requireNoFollowup": bool(args.require_no_followup),
                "matches": all_hits,
                "matchCount": len(all_hits),
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )

    with_followup = sum(1 for item in all_hits if item.get("sameSpeakerFollowup"))
    print(f"Scanned files: {len(dialogue_files)}")
    print(f"Matches: {len(all_hits)}")
    print(f"Matches with same-speaker follow-up: {with_followup}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()