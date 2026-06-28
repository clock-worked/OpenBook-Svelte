# pylint: disable=C0115,C0116
# ruff: noqa: D101, D103, E402

"""Extract Book-17 dialogue lines containing lvl and split them into reusable segments.

Workflow:
1. Scan Primal-Hunter Book-17 dialogue.json files for lines containing "lvl".
2. Split bracketed entity/level text into reusable segments.
3. Normalize the level wording into spoken English, rendering ??? as Unknown.
4. Write a report with per-line segments plus a deduplicated reusable segment index.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))


CHAPTER_FOLDER_PATTERN = re.compile(r"^(?P<number>\d+)\s*-\s*.+$")
BRACKET_PATTERN = re.compile(r"\[(?P<content>[^\]]+)\]")
BRACKET_PART_PATTERN = re.compile(r"\s*[–-]\s*")
LEVEL_TOKEN_PATTERN = re.compile(
    r"(?i)\blvl\b\s*(?P<value>\d+|\?\?\?)\s*(?P<plus>\+)?"
)
CLAUSE_SPLIT_PATTERN = re.compile(r"\s*(?:[.!?;:]|\s+[–—]\s+)\s*")

ONES = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}

TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}


@dataclass
class LvlEntry:
    chapter_dir_name: str
    chapter_path: str
    line_id: int
    character_id: str
    source_text: str
    normalized_text: str
    segment_preview: str
    speech_text: str
    segments: list[dict[str, Any]]


def parse_args() -> argparse.Namespace:
    default_book_dir = (
        Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources")
        / "Primal-Hunter"
        / "Book-17"
    )
    default_output_dir = default_book_dir / "_lvl_audio_segments"

    parser = argparse.ArgumentParser(
        description=(
            "Extract lvl-bearing dialogue lines from Book-17, split them into reusable "
            "segments, and write a normalization report."
        )
    )
    parser.add_argument("--book-dir", type=Path, default=default_book_dir)
    parser.add_argument("--output-dir", type=Path, default=default_output_dir)
    parser.add_argument(
        "--max-lines",
        type=int,
        default=0,
        help="Optional cap for quick tests (0 means all matching lines).",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_ascii_punctuation(text: str) -> str:
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return " ".join(text.split()).strip()


def strip_wrapping_markers(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[\s*•]+", "", text)
    text = re.sub(r"[\s*•]+$", "", text)
    return text.strip()


def two_digit_words(value: int) -> str:
    if value < 20:
        return ONES[value]
    tens_value = (value // 10) * 10
    remainder = value % 10
    if remainder == 0:
        return TENS[tens_value]
    return f"{TENS[tens_value]} {ONES[remainder]}"


def cardinal_number_words(value: int) -> str:
    if value < 0:
        raise ValueError("Negative values are not supported.")
    if value < 100:
        return two_digit_words(value)
    if value < 1000:
        hundreds = value // 100
        tail = value % 100
        if tail == 0:
            return f"{ONES[hundreds]} hundred"
        return f"{ONES[hundreds]} hundred {two_digit_words(tail)}"

    thousands = value // 1000
    remainder = value % 1000
    if remainder == 0:
        return f"{cardinal_number_words(thousands)} thousand"
    return (
        f"{cardinal_number_words(thousands)} thousand "
        f"{cardinal_number_words(remainder)}"
    )


def level_number_words(value: int) -> str:
    """Convert level values to short spoken style (350 -> three fifty)."""

    if value < 0:
        raise ValueError("Negative values are not supported.")
    if value < 100:
        return two_digit_words(value)
    if value < 1000:
        hundreds = value // 100
        tail = value % 100
        if tail == 0:
            return f"{ONES[hundreds]} hundred"
        if tail < 10:
            return f"{ONES[hundreds]} oh {ONES[tail]}"
        return f"{ONES[hundreds]} {two_digit_words(tail)}"
    return cardinal_number_words(value)


def render_level_phrase(raw_value: str, has_plus: bool) -> str:
    if raw_value == "???":
        return "Unknown"
    spoken_value = level_number_words(int(raw_value))
    phrase = f"level {spoken_value}"
    if has_plus:
        phrase = f"{phrase} plus"
    return phrase


def discover_chapter_dirs(book_dir: Path) -> list[Path]:
    chapters: list[tuple[int, Path]] = []
    for child in sorted(book_dir.iterdir()):
        if not child.is_dir():
            continue
        match = CHAPTER_FOLDER_PATTERN.match(child.name)
        if match is None:
            continue
        if not (child / "dialogue.json").exists():
            continue
        chapters.append((int(match.group("number")), child))

    chapters.sort(key=lambda item: (item[0], item[1].name.lower()))
    return [item[1] for item in chapters]


def split_plain_clause(text: str) -> list[str]:
    pieces = [piece.strip(" \t\r\n,;:-") for piece in CLAUSE_SPLIT_PATTERN.split(text)]
    return [piece for piece in pieces if piece]


def normalize_bracket_content(content: str) -> list[dict[str, Any]]:
    parts = [part.strip() for part in BRACKET_PART_PATTERN.split(content) if part.strip()]
    segments: list[dict[str, Any]] = []
    entity_parts: list[str] = []
    saw_level = False

    for part in parts:
        match = LEVEL_TOKEN_PATTERN.search(part)
        if match is None:
            if saw_level:
                segments.extend(
                    {
                        "kind": "detail",
                        "text": clause,
                    }
                    for clause in split_plain_clause(part)
                )
            else:
                entity_parts.append(part)
            continue

        before = part[: match.start()].strip(" \t\r\n,;:-")
        if before:
            entity_parts.append(before)

        entity_text = ""
        if entity_parts:
            entity_text = normalize_ascii_punctuation(" ".join(entity_parts))
            entity_parts = []

        raw_level = match.group("value")
        level_phrase = render_level_phrase(raw_level, bool(match.group("plus")))
        if raw_level == "???":
            level_phrase = "Level Unknown"

        segments.append(
            {
                "kind": "entity_level" if entity_text else "level",
                "text": (
                    f"{entity_text}, {level_phrase}" if entity_text else level_phrase
                ),
                "entity": entity_text or None,
                "levelText": level_phrase,
                "raw": raw_level,
            }
        )
        saw_level = True

        after = part[match.end() :].strip(" \t\r\n,;:-")
        if after:
            segments.extend(
                {
                    "kind": "detail",
                    "text": clause,
                }
                for clause in split_plain_clause(after)
            )

    if entity_parts:
        segments.append(
            {
                "kind": "detail" if saw_level else "plain",
                "text": normalize_ascii_punctuation(" ".join(entity_parts)),
            }
        )

    return segments


def normalize_lvl_segments(text: str) -> tuple[list[dict[str, Any]], str, str]:
    normalized = normalize_ascii_punctuation(strip_wrapping_markers(text))
    segments: list[dict[str, Any]] = []
    cursor = 0

    for match in BRACKET_PATTERN.finditer(normalized):
        before = normalized[cursor : match.start()].strip()
        if before:
            segments.extend(
                {
                    "kind": "prefix" if not segments else "separator",
                    "text": clause,
                }
                for clause in split_plain_clause(before)
            )

        segments.extend(normalize_bracket_content(match.group("content")))
        cursor = match.end()

    tail = normalized[cursor:].strip()
    if tail:
        segments.extend(
            {
                "kind": "suffix" if segments else "plain",
                "text": clause,
            }
            for clause in split_plain_clause(tail)
        )

    if not segments:
        segments = [
            {
                "kind": "plain",
                "text": normalized,
            }
        ]

    speech_text = " ".join(segment["text"] for segment in segments if segment["text"])
    segment_preview = " ... ".join(
        segment["text"] for segment in segments if segment["text"]
    )
    return segments, speech_text, segment_preview


def discover_lvl_entries(book_dir: Path, max_lines: int = 0) -> list[LvlEntry]:
    entries: list[LvlEntry] = []

    for chapter_path in discover_chapter_dirs(book_dir):
        dialogue_path = chapter_path / "dialogue.json"
        with dialogue_path.open("r", encoding="utf-8") as handle:
            dialogue_data = json.load(handle)

        if isinstance(dialogue_data, dict):
            dialogue = dialogue_data.get("lines", [])
        else:
            dialogue = dialogue_data

        for item in dialogue:
            if not isinstance(item, dict):
                continue
            source_text = str(item.get("text", ""))
            if not re.search(r"(?i)\blvl\b", source_text):
                continue

            segments, speech_text, segment_preview = normalize_lvl_segments(source_text)
            entries.append(
                LvlEntry(
                    chapter_dir_name=chapter_path.name,
                    chapter_path=str(chapter_path),
                    line_id=int(item.get("id", -1)),
                    character_id=str(item.get("characterId", "")),
                    source_text=source_text,
                    normalized_text=normalize_ascii_punctuation(
                        strip_wrapping_markers(source_text)
                    ),
                    segment_preview=segment_preview,
                    speech_text=speech_text,
                    segments=segments,
                )
            )

            if max_lines > 0 and len(entries) >= max_lines:
                return entries

    return entries


def build_segment_library(entries: list[LvlEntry]) -> list[dict[str, Any]]:
    library: dict[tuple[str, str], dict[str, Any]] = {}

    for entry in entries:
        for segment in entry.segments:
            key = (segment["kind"], segment["text"].casefold())
            bucket = library.get(key)
            location = {
                "chapter": entry.chapter_dir_name,
                "lineId": entry.line_id,
            }
            if bucket is None:
                library[key] = {
                    "kind": segment["kind"],
                    "text": segment["text"],
                    "count": 1,
                    "examples": [location],
                }
                continue

            bucket["count"] += 1
            if len(bucket["examples"]) < 5:
                bucket["examples"].append(location)

    return sorted(
        library.values(),
        key=lambda item: (-item["count"], item["kind"], item["text"].casefold()),
    )


def write_reports(
    output_dir: Path,
    entries: list[LvlEntry],
    segment_library: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "entryCount": len(entries),
        "segmentCount": sum(len(entry.segments) for entry in entries),
        "entries": [asdict(entry) for entry in entries],
        "segmentLibrary": segment_library,
    }

    report_path = output_dir / "lvl_dialogue_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    text_lines = [
        f"Generated at: {report['generatedAt']}",
        f"Entries: {len(entries)}",
        f"Segments: {report['segmentCount']}",
        "",
        "Entries:",
    ]
    for entry in entries:
        text_lines.append(
            f"- {entry.chapter_dir_name} #{entry.line_id} "
            f"({entry.character_id}): {entry.segment_preview}"
        )
        text_lines.append(f"  speech: {entry.speech_text}")

    text_lines.extend(["", "Reusable segments:"])
    for segment in segment_library:
        text_lines.append(
            f"- {segment['kind']}: {segment['text']} (x{segment['count']})"
        )

    (output_dir / "lvl_dialogue_report.txt").write_text(
        "\n".join(text_lines) + "\n",
        encoding="utf-8",
    )

    (output_dir / "lvl_segment_library.json").write_text(
        json.dumps(segment_library, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    entries = discover_lvl_entries(args.book_dir, max_lines=args.max_lines)
    segment_library = build_segment_library(entries)

    if args.dry_run:
        print(f"Found {len(entries)} lvl-bearing dialogue lines.")
        print(f"Generated {sum(len(entry.segments) for entry in entries)} segments.")
        if entries:
            first = entries[0]
            print(f"First entry: {first.chapter_dir_name} #{first.line_id}")
            print(f"Preview: {first.segment_preview}")
        return 0

    write_reports(args.output_dir, entries, segment_library)
    print(f"Wrote lvl segment report to {args.output_dir}")
    print(f"Found {len(entries)} lvl-bearing dialogue lines.")
    print(f"Reusable segment forms: {len(segment_library)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
