#!/usr/bin/env python3

import argparse
import csv
from typing import Dict, List


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def to_float(value: str) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def to_int(value: str) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge parser v2 eval CSV with BookNLP A/B CSV")
    parser.add_argument("parser_csv", help="Path to evaluation_summary_v2_first18.csv (or compatible)")
    parser.add_argument("booknlp_csv", help="Path to booknlp_ab_eval_first18.csv (or compatible)")
    parser.add_argument("output_csv", help="Path to merged output CSV")
    args = parser.parse_args()

    parser_rows = read_csv(args.parser_csv)
    book_rows = read_csv(args.booknlp_csv)

    parser_by_chapter = {row["chapter"]: row for row in parser_rows}
    book_by_chapter = {row["chapter"]: row for row in book_rows}

    chapters = sorted(set(parser_by_chapter.keys()) | set(book_by_chapter.keys()))

    merged_rows: List[Dict[str, object]] = []
    for chapter in chapters:
        p = parser_by_chapter.get(chapter, {})
        b = book_by_chapter.get(chapter, {})

        parser_dialogue_acc = to_float(p.get("dialogue_accuracy", "0"))
        book_dialogue_acc = to_float(b.get("booknlp_dialogue_acc", "0"))

        parser_overall_acc = to_float(p.get("overall_accuracy", "0"))
        book_overall_acc = to_float(b.get("booknlp_overall_acc", "0"))

        merged_rows.append(
            {
                "chapter": chapter,
                "gold_lines": to_int(p.get("gold_lines", b.get("gold_lines", "0"))),
                "parser_pred_lines": to_int(p.get("pred_lines", b.get("parser_pred_lines", "0"))),
                "parser_line_delta": to_int(p.get("pred_minus_gold", b.get("parser_line_delta", "0"))),
                "booknlp_quotes": to_int(b.get("booknlp_quotes", "0")),
                "booknlp_aligned_quotes": to_int(b.get("booknlp_aligned_quotes", "0")),
                "parser_overall_acc": f"{parser_overall_acc:.6f}",
                "parser_dialogue_acc": f"{parser_dialogue_acc:.6f}",
                "booknlp_overall_acc": f"{book_overall_acc:.6f}",
                "booknlp_dialogue_acc": f"{book_dialogue_acc:.6f}",
                "dialogue_acc_delta_booknlp_minus_parser": f"{(book_dialogue_acc - parser_dialogue_acc):+.6f}",
            }
        )

    with open(args.output_csv, "w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "chapter",
            "gold_lines",
            "parser_pred_lines",
            "parser_line_delta",
            "booknlp_quotes",
            "booknlp_aligned_quotes",
            "parser_overall_acc",
            "parser_dialogue_acc",
            "booknlp_overall_acc",
            "booknlp_dialogue_acc",
            "dialogue_acc_delta_booknlp_minus_parser",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged_rows)

    parser_dialogue_correct = sum(
        to_int(parser_by_chapter.get(ch, {}).get("dialogue_correct", "0")) for ch in chapters
    )
    parser_dialogue_total = sum(
        to_int(parser_by_chapter.get(ch, {}).get("dialogue_total", "0")) for ch in chapters
    )
    book_dialogue_correct = sum(
        int(round(to_float(book_by_chapter.get(ch, {}).get("booknlp_dialogue_acc", "0")) * to_int(parser_by_chapter.get(ch, {}).get("dialogue_total", "0"))))
        for ch in chapters
    )
    book_dialogue_total = parser_dialogue_total

    print("Merged chapters:", len(merged_rows))
    if parser_dialogue_total > 0:
        print(
            f"Parser dialogue accuracy: {parser_dialogue_correct/parser_dialogue_total:.4f} "
            f"({parser_dialogue_correct}/{parser_dialogue_total})"
        )
        print(
            f"BookNLP dialogue accuracy (aligned to parser totals): {book_dialogue_correct/book_dialogue_total:.4f} "
            f"({book_dialogue_correct}/{book_dialogue_total})"
        )
        print(
            f"Delta (BookNLP - parser): {(book_dialogue_correct/book_dialogue_total) - (parser_dialogue_correct/parser_dialogue_total):+.4f}"
        )
    print("Output:", args.output_csv)


if __name__ == "__main__":
    main()
