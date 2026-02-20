#!/usr/bin/env python3

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from openbook_parser.dialogue_parser_service import DialogueParserService


@dataclass
class ChapterResult:
    chapter: str
    gold_lines: int
    pred_lines: int
    total: int
    correct: int
    dialogue_total: int
    dialogue_correct: int
    mismatches: List[Dict[str, object]]

    @property
    def overall_accuracy(self) -> float:
        return (self.correct / self.total) if self.total else 0.0

    @property
    def dialogue_accuracy(self) -> float:
        return (self.dialogue_correct / self.dialogue_total) if self.dialogue_total else 0.0

    @property
    def pred_minus_gold(self) -> int:
        return self.pred_lines - self.gold_lines


def load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def normalize_speaker(value: str) -> str:
    if value is None:
        return "narrator"
    text = str(value).strip()
    return text.lower() if text else "narrator"


def iter_chapter_dirs(book_root: str, chapter_regex: str = None, limit: int = 0) -> List[Tuple[str, str, str, str]]:
    pattern = re.compile(chapter_regex) if chapter_regex else None
    items: List[Tuple[str, str, str, str]] = []
    for name in sorted(os.listdir(book_root)):
        chapter_dir = os.path.join(book_root, name)
        if not os.path.isdir(chapter_dir):
            continue
        if pattern and not pattern.search(name):
            continue
        txt_path = os.path.join(chapter_dir, "chapter.txt")
        dialogue_path = os.path.join(chapter_dir, "dialogue.json")
        if os.path.exists(txt_path) and os.path.exists(dialogue_path):
            items.append((name, chapter_dir, txt_path, dialogue_path))
    if limit and limit > 0:
        return items[:limit]
    return items


def evaluate_chapter(
    parser_service: DialogueParserService,
    chapter_name: str,
    txt_path: str,
    dialogue_path: str,
    max_mismatches: int,
    per_character_tp: Dict[str, int],
    per_character_fp: Dict[str, int],
    per_character_fn: Dict[str, int],
) -> ChapterResult:
    gold_json = load_json(dialogue_path)
    gold_lines = gold_json.get("lines", [])
    pred_lines, _ = parser_service.parse_file(txt_path)

    total = 0
    correct = 0
    dialogue_total = 0
    dialogue_correct = 0
    mismatches: List[Dict[str, object]] = []

    compare_len = max(len(gold_lines), len(pred_lines))

    for idx in range(compare_len):
        if idx >= len(gold_lines):
            continue

        gold_line = gold_lines[idx]
        gold_speaker_raw = gold_line.get("characterId", "narrator")
        gold_speaker = normalize_speaker(gold_speaker_raw)
        is_dialogue = gold_speaker != "narrator"

        total += 1
        if is_dialogue:
            dialogue_total += 1

        if idx >= len(pred_lines):
            if is_dialogue:
                per_character_fn[gold_speaker] += 1
                if len(mismatches) < max_mismatches:
                    mismatches.append(
                        {
                            "index": idx + 1,
                            "gold": gold_speaker_raw,
                            "pred": "<missing>",
                            "text": str(gold_line.get("text", ""))[:180],
                        }
                    )
            continue

        pred_speaker_raw = pred_lines[idx].speaker or "Narrator"
        pred_speaker = normalize_speaker(pred_speaker_raw)

        if pred_speaker == gold_speaker:
            correct += 1
            if is_dialogue:
                dialogue_correct += 1
                per_character_tp[gold_speaker] += 1
        else:
            if is_dialogue:
                per_character_fn[gold_speaker] += 1
                if pred_speaker != "narrator":
                    per_character_fp[pred_speaker] += 1
                if len(mismatches) < max_mismatches:
                    mismatches.append(
                        {
                            "index": idx + 1,
                            "gold": gold_speaker_raw,
                            "pred": pred_speaker_raw,
                            "text": str(gold_line.get("text", ""))[:180],
                        }
                    )

    return ChapterResult(
        chapter=chapter_name,
        gold_lines=len(gold_lines),
        pred_lines=len(pred_lines),
        total=total,
        correct=correct,
        dialogue_total=dialogue_total,
        dialogue_correct=dialogue_correct,
        mismatches=mismatches,
    )


def write_csv(path: str, chapter_results: List[ChapterResult]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(
            [
                "chapter",
                "gold_lines",
                "pred_lines",
                "pred_minus_gold",
                "overall_accuracy",
                "dialogue_accuracy",
                "total",
                "correct",
                "dialogue_total",
                "dialogue_correct",
                "dialogue_errors",
            ]
        )
        for result in chapter_results:
            writer.writerow(
                [
                    result.chapter,
                    result.gold_lines,
                    result.pred_lines,
                    result.pred_minus_gold,
                    f"{result.overall_accuracy:.6f}",
                    f"{result.dialogue_accuracy:.6f}",
                    result.total,
                    result.correct,
                    result.dialogue_total,
                    result.dialogue_correct,
                    result.dialogue_total - result.dialogue_correct,
                ]
            )


def print_summary(
    chapter_results: List[ChapterResult],
    per_character_tp: Dict[str, int],
    per_character_fp: Dict[str, int],
    per_character_fn: Dict[str, int],
    max_characters: int,
) -> None:
    total = sum(r.total for r in chapter_results)
    correct = sum(r.correct for r in chapter_results)
    dialogue_total = sum(r.dialogue_total for r in chapter_results)
    dialogue_correct = sum(r.dialogue_correct for r in chapter_results)
    mismatch_chapters = sum(1 for r in chapter_results if r.pred_minus_gold != 0)

    print("=== Parser vs curated dialogue.json (v2) ===")
    print(f"Chapters evaluated: {len(chapter_results)}")
    print(f"Overall accuracy: {(correct / total if total else 0.0):.4f} ({correct}/{total})")
    print(
        f"Dialogue accuracy: {(dialogue_correct / dialogue_total if dialogue_total else 0.0):.4f} "
        f"({dialogue_correct}/{dialogue_total})"
    )
    print(f"Chapters with line-count mismatch: {mismatch_chapters}")

    print("\nPer-chapter overview:")
    for result in chapter_results:
        print(
            f"- {result.chapter}: overall={result.overall_accuracy:.3f}, "
            f"dialogue={result.dialogue_accuracy:.3f}, lines={result.gold_lines}, delta={result.pred_minus_gold}"
        )

    print("\nTop mismatches per chapter:")
    for result in chapter_results:
        if not result.mismatches:
            continue
        print(f"\n[{result.chapter}]")
        for mismatch in result.mismatches:
            print(
                f"  idx={mismatch['index']} gold={mismatch['gold']} pred={mismatch['pred']} "
                f"text={mismatch['text']}"
            )

    characters = set(per_character_tp) | set(per_character_fp) | set(per_character_fn)
    ranked = sorted(characters, key=lambda n: (per_character_tp[n] + per_character_fn[n]), reverse=True)
    if max_characters > 0:
        ranked = ranked[:max_characters]

    print("\nPer-character precision/recall (dialogue only):")
    for name in ranked:
        tp = per_character_tp[name]
        fp = per_character_fp[name]
        fn = per_character_fn[name]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        print(f"- {name}: P={precision:.3f} R={recall:.3f} (tp={tp}, fp={fp}, fn={fn})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate parser output against curated dialogue.json (v2 format).")
    parser.add_argument("book_root", help="Path to Book-X directory containing chapter folders with chapter.txt and dialogue.json")
    parser.add_argument("--chapter-regex", default=None, help="Regex filter applied to chapter folder names")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate only the first N matching chapters")
    parser.add_argument("--max-mismatches-per-chapter", type=int, default=5, help="How many mismatches to print per chapter")
    parser.add_argument("--max-characters", type=int, default=25, help="How many characters to include in precision/recall")
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Output CSV path for per-chapter summary (default: <book_root>/evaluation_summary_v2.csv)",
    )
    args = parser.parse_args()

    book_root = os.path.abspath(args.book_root)
    output_csv = args.output_csv or os.path.join(book_root, "evaluation_summary_v2.csv")

    chapter_items = iter_chapter_dirs(book_root, chapter_regex=args.chapter_regex, limit=args.limit)
    if not chapter_items:
        raise SystemExit("No chapter folders found with both chapter.txt and dialogue.json")

    parser_service = DialogueParserService()
    per_character_tp: Dict[str, int] = defaultdict(int)
    per_character_fp: Dict[str, int] = defaultdict(int)
    per_character_fn: Dict[str, int] = defaultdict(int)

    chapter_results: List[ChapterResult] = []
    for chapter_name, _chapter_dir, txt_path, dialogue_path in chapter_items:
        result = evaluate_chapter(
            parser_service=parser_service,
            chapter_name=chapter_name,
            txt_path=txt_path,
            dialogue_path=dialogue_path,
            max_mismatches=args.max_mismatches_per_chapter,
            per_character_tp=per_character_tp,
            per_character_fp=per_character_fp,
            per_character_fn=per_character_fn,
        )
        chapter_results.append(result)

    write_csv(output_csv, chapter_results)
    print_summary(
        chapter_results=chapter_results,
        per_character_tp=per_character_tp,
        per_character_fp=per_character_fp,
        per_character_fn=per_character_fn,
        max_characters=args.max_characters,
    )
    print(f"\nCSV written: {output_csv}")


if __name__ == "__main__":
    main()
