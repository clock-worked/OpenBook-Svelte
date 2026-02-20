#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)


@dataclass
class ChapterScore:
    chapter: str
    dialogue_total: int
    dialogue_correct: int

    @property
    def dialogue_accuracy(self) -> float:
        return (self.dialogue_correct / self.dialogue_total) if self.dialogue_total else 0.0


def _normalize_speaker(value: Optional[str]) -> str:
    if value is None:
        return "narrator"
    text = str(value).strip().lower()
    return text if text else "narrator"


def _load_json(path: str) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _iter_chapters(book_root: str, chapter_regex: Optional[str], limit: int) -> List[Tuple[str, str, str]]:
    matcher = re.compile(chapter_regex) if chapter_regex else None
    items: List[Tuple[str, str, str]] = []
    for name in sorted(os.listdir(book_root)):
        chapter_dir = os.path.join(book_root, name)
        if not os.path.isdir(chapter_dir):
            continue
        if matcher and not matcher.search(name):
            continue
        txt_path = os.path.join(chapter_dir, "chapter.txt")
        dialogue_path = os.path.join(chapter_dir, "dialogue.json")
        if os.path.exists(txt_path) and os.path.exists(dialogue_path):
            items.append((name, txt_path, dialogue_path))
    if limit > 0:
        return items[:limit]
    return items


def _load_parser(backend: str):
    if backend == "booknlp":
        from openbook_parser.booknlp_parser_service import BookNLPParserService

        return BookNLPParserService()

    from openbook_parser.dialogue_parser_service import DialogueParserService

    return DialogueParserService()


def _evaluate_chapter(parser_service, chapter_name: str, txt_path: str, dialogue_path: str) -> ChapterScore:
    gold = _load_json(dialogue_path)
    gold_lines = gold.get("lines", [])
    pred_lines, _names, _meta = parser_service.parse_file(txt_path)

    compare_len = min(len(gold_lines), len(pred_lines))
    dialogue_total = 0
    dialogue_correct = 0

    for idx in range(compare_len):
        gold_line = gold_lines[idx]
        gold_speaker = _normalize_speaker(gold_line.get("characterId"))
        if gold_speaker == "narrator":
            continue

        dialogue_total += 1
        pred_speaker = _normalize_speaker(getattr(pred_lines[idx], "speaker", "Narrator"))
        if pred_speaker == gold_speaker:
            dialogue_correct += 1

    return ChapterScore(
        chapter=chapter_name,
        dialogue_total=dialogue_total,
        dialogue_correct=dialogue_correct,
    )


def _summarize(scores: List[ChapterScore], backend: str, book_root: str) -> Dict[str, object]:
    total_dialogue = sum(item.dialogue_total for item in scores)
    total_correct = sum(item.dialogue_correct for item in scores)
    dialogue_accuracy = (total_correct / total_dialogue) if total_dialogue else 0.0

    chapters = {
        item.chapter: {
            "dialogue_total": item.dialogue_total,
            "dialogue_correct": item.dialogue_correct,
            "dialogue_accuracy": item.dialogue_accuracy,
        }
        for item in scores
    }

    return {
        "backend": backend,
        "book_root": book_root,
        "dialogue_total": total_dialogue,
        "dialogue_correct": total_correct,
        "dialogue_accuracy": dialogue_accuracy,
        "chapters": chapters,
    }


def _print_summary(summary: Dict[str, object]) -> None:
    print("=== Curated dialogue gate ===")
    print(f"Backend: {summary['backend']}")
    print(f"Book root: {summary['book_root']}")
    print(
        "Dialogue accuracy: "
        f"{summary['dialogue_accuracy']:.4f} "
        f"({summary['dialogue_correct']}/{summary['dialogue_total']})"
    )

    chapter_rows = summary["chapters"]
    for chapter in sorted(chapter_rows.keys()):
        row = chapter_rows[chapter]
        print(
            f"- {chapter}: {row['dialogue_accuracy']:.4f} "
            f"({row['dialogue_correct']}/{row['dialogue_total']})"
        )


def _compare_to_baseline(summary: Dict[str, object], baseline_path: str) -> Tuple[float, List[str]]:
    baseline = _load_json(baseline_path)
    current_acc = float(summary.get("dialogue_accuracy", 0.0))
    baseline_acc = float(baseline.get("dialogue_accuracy", 0.0))
    delta = current_acc - baseline_acc

    notes: List[str] = []
    notes.append(
        f"Overall dialogue accuracy delta: {delta:+.4f} "
        f"(current={current_acc:.4f}, baseline={baseline_acc:.4f})"
    )

    baseline_chapters = baseline.get("chapters", {})
    current_chapters = summary.get("chapters", {})
    shared_chapters = sorted(set(current_chapters.keys()) & set(baseline_chapters.keys()))
    for chapter in shared_chapters:
        cur = float(current_chapters[chapter].get("dialogue_accuracy", 0.0))
        base = float(baseline_chapters[chapter].get("dialogue_accuracy", 0.0))
        chapter_delta = cur - base
        if abs(chapter_delta) >= 0.0001:
            notes.append(
                f"  {chapter}: {chapter_delta:+.4f} "
                f"(current={cur:.4f}, baseline={base:.4f})"
            )

    return delta, notes


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate parser dialogue attribution against curated dialogue.json and optionally "
            "fail if dialogue accuracy regresses from a saved baseline."
        )
    )
    parser.add_argument("book_root", help="Path to Book-X folder containing chapter directories")
    parser.add_argument(
        "--backend",
        choices=["booknlp", "legacy"],
        default="booknlp",
        help="Parser backend to evaluate (default: booknlp)",
    )
    parser.add_argument("--chapter-regex", default=None, help="Regex filter for chapter directory names")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate only first N matching chapters")
    parser.add_argument(
        "--save-summary",
        default=None,
        help="Write summary JSON to this path (recommended for baseline snapshots)",
    )
    parser.add_argument(
        "--baseline-summary",
        default=None,
        help="Path to prior summary JSON to compare against",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit non-zero when overall dialogue accuracy is worse than baseline",
    )
    args = parser.parse_args()

    book_root = os.path.abspath(args.book_root)
    chapters = _iter_chapters(book_root, args.chapter_regex, args.limit)
    if not chapters:
        raise SystemExit("No chapters found with chapter.txt + dialogue.json")

    parser_service = _load_parser(args.backend)
    scores: List[ChapterScore] = []
    for chapter_name, txt_path, dialogue_path in chapters:
        scores.append(_evaluate_chapter(parser_service, chapter_name, txt_path, dialogue_path))

    summary = _summarize(scores, args.backend, book_root)
    _print_summary(summary)

    regression = False
    if args.baseline_summary:
        delta, notes = _compare_to_baseline(summary, args.baseline_summary)
        print("\n=== Baseline comparison ===")
        for note in notes:
            print(note)
        regression = delta < 0

    if args.save_summary:
        save_path = os.path.abspath(args.save_summary)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        print(f"\nSummary written: {save_path}")

    if args.fail_on_regression and args.baseline_summary and regression:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
