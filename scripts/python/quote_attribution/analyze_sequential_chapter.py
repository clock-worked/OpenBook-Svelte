#!/usr/bin/env python
"""Inspect legacy and ModernBookNLP decisions after sequential gold review."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from py_services.openbook_parser.booknlp_parser_service import BookNLPParserService
from scripts.python.quote_attribution.sequential_modernbooknlp_benchmark import (
    _align,
    _catalog,
    _chapter_dirs,
    _gold_quotes,
    _load_json,
    _normalize,
    _predicted_quotes,
    _review_gold,
    _speaker_lookup,
    _strip_descriptors,
)


def _line_payload(line: object) -> dict:
    attribution = getattr(line, "attribution", None)
    attribution = attribution if isinstance(attribution, dict) else {}
    trace = attribution.get("decisionTrace")
    trace = trace if isinstance(trace, dict) else {}
    return {
        "speaker": str(getattr(line, "speaker", None) or ""),
        "suggestion": bool(getattr(line, "is_suggestion", False)),
        "candidates": deepcopy(attribution.get("candidates") or []),
        "selected_reasons": list(trace.get("selectedReasons") or []),
        "signals": deepcopy(trace.get("signals") or {}),
        "modernbooknlp": deepcopy(trace.get("modernBookNLP") or {}),
        "modernbooknlp_decision": trace.get("modernBookNLPDecision"),
    }


def _aligned_lines(gold: list, predicted: list, lines: Sequence[object]) -> dict[int, object]:
    dialogue_lines = [
        line for line in lines
        if getattr(line, "line_type", None) == "dialogue"
    ]
    return {
        gold_index: dialogue_lines[predicted_index]
        for gold_index, predicted_index in _align(gold, predicted)
    }


def analyze(book_dir: Path, chapter_name: str) -> dict:
    chapters = _chapter_dirs(book_dir)
    target_index = next(
        (index for index, chapter in enumerate(chapters) if chapter.name == chapter_name),
        None,
    )
    if target_index is None:
        raise ValueError(f"Chapter not found: {chapter_name}")

    characters = _strip_descriptors(_load_json(book_dir / "characters.json"))
    parser = BookNLPParserService()
    with tempfile.TemporaryDirectory(prefix="openbook-attribution-analysis-") as temp_dir:
        review_root = Path(temp_dir)
        for chapter in chapters[:target_index]:
            dialogue = _load_json(chapter / "dialogue.json")
            options = {
                "parser_backend": "modernbooknlp",
                "closed_world_characters": True,
                "character_catalog": _catalog(characters),
            }
            lines, _names, _meta = parser.parse_file(str(chapter / "chapter.txt"), options=options)
            characters, _review = _review_gold(
                review_root, chapter, dialogue, lines, characters
            )

        target = chapters[target_index]
        dialogue = _load_json(target / "dialogue.json")
        options = {
            "closed_world_characters": True,
            "character_catalog": _catalog(characters),
        }
        legacy_lines, _legacy_names, legacy_meta = parser.parse_file(
            str(target / "chapter.txt"), options={**options, "parser_backend": "legacy"}
        )
        modern_lines, _modern_names, modern_meta = parser.parse_file(
            str(target / "chapter.txt"), options={**options, "parser_backend": "modernbooknlp"}
        )

    gold = _gold_quotes(dialogue, characters)
    legacy_predictions = _predicted_quotes(legacy_lines, characters)
    modern_predictions = _predicted_quotes(modern_lines, characters)
    legacy_aligned = _aligned_lines(gold, legacy_predictions, legacy_lines)
    modern_aligned = _aligned_lines(gold, modern_predictions, modern_lines)
    source = (target / "chapter.txt").read_text(encoding="utf-8-sig")
    speaker_lookup = _speaker_lookup(characters)
    rows = []
    for gold_index, gold_quote in enumerate(gold):
        legacy_line = legacy_aligned.get(gold_index)
        modern_line = modern_aligned.get(gold_index)
        legacy_speaker = speaker_lookup.get(
            _normalize(getattr(legacy_line, "speaker", None))
        )
        modern_speaker = speaker_lookup.get(
            _normalize(getattr(modern_line, "speaker", None))
        )
        context_start = max(0, gold_quote.start - 180)
        context_end = min(len(source), gold_quote.end + 220)
        row = {
            "gold_line_id": gold_quote.line_id,
            "gold_speaker": gold_quote.speaker,
            "text": gold_quote.text,
            "context": source[context_start:context_end],
            "legacy_speaker": legacy_speaker,
            "modern_speaker": modern_speaker,
            "legacy_correct": legacy_speaker == gold_quote.speaker,
            "modern_correct": modern_speaker == gold_quote.speaker,
            "legacy": _line_payload(legacy_line) if legacy_line else None,
            "modern": _line_payload(modern_line) if modern_line else None,
        }
        rows.append(row)

    return {
        "book": book_dir.name,
        "chapter": chapter_name,
        "descriptors_before_chapter": sum(
            len(character.get("descriptors") or [])
            for character in characters.get("characters", [])
        ),
        "legacy_meta": legacy_meta,
        "modern_meta": modern_meta,
        "summary": {
            "gold_quotes": len(rows),
            "legacy_correct": sum(row["legacy_correct"] for row in rows),
            "modern_correct": sum(row["modern_correct"] for row in rows),
            "modern_wrong_legacy_correct": sum(
                row["legacy_correct"] and not row["modern_correct"] for row in rows
            ),
            "modern_correct_legacy_wrong": sum(
                row["modern_correct"] and not row["legacy_correct"] for row in rows
            ),
            "both_wrong": sum(
                not row["modern_correct"] and not row["legacy_correct"] for row in rows
            ),
        },
        "misses": [row for row in rows if not row["modern_correct"]],
        "rows": rows,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = analyze(args.book_dir.resolve(), args.chapter)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    print(f"Detailed analysis: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
