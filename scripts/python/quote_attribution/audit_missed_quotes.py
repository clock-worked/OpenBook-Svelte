"""Export sequential predictions and narration for a corpus-wide error audit."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from py_services.openbook_parser.attribution_passes import build_paragraph_ranges
from py_services.openbook_parser.booknlp_parser_service import BookNLPParserService, _read_text_with_fallback
from scripts.python.quote_attribution.analyze_sequential_chapter import _line_payload
from scripts.python.quote_attribution.sequential_modernbooknlp_benchmark import (
    _align, _catalog, _chapter_dirs, _gold_quotes, _load_json,
    _predicted_quotes, _review_gold, _strip_descriptors, _write_json,
)


def chapter_rows(chapter, characters, lines):
    source = _read_text_with_fallback(str(chapter / "chapter.txt"))
    gold = _gold_quotes(_load_json(chapter / "dialogue.json"), characters)
    predicted = _predicted_quotes(lines, characters)
    aligned = dict(_align(gold, predicted))
    dialogue_lines = [line for line in lines if line.line_type == "dialogue"]
    paragraphs = build_paragraph_ranges(source)
    starts = [paragraph.start for paragraph in paragraphs]
    rows = []
    for gold_index, quote in enumerate(gold):
        prediction_index = aligned.get(gold_index)
        prediction = predicted[prediction_index] if prediction_index is not None else None
        line = dialogue_lines[prediction_index] if prediction_index is not None else None
        start = prediction.start if prediction else quote.start
        end = prediction.end if prediction else quote.end
        paragraph_index = bisect_right(starts, start) - 1
        previous_end = predicted[prediction_index - 1].end if prediction_index else 0
        next_start = (
            predicted[prediction_index + 1].start
            if prediction_index is not None and prediction_index + 1 < len(predicted)
            else len(source)
        )
        paragraph = paragraphs[paragraph_index] if paragraph_index >= 0 else None
        rows.append({
            "book": chapter.parent.name,
            "chapter": chapter.name,
            "gold_line_id": quote.line_id,
            "gold": quote.speaker,
            "predicted": prediction.speaker if prediction else None,
            "correct": bool(prediction and prediction.speaker == quote.speaker),
            "matched": prediction is not None,
            "text": quote.text,
            "span": {"start": start, "end": end},
            "prediction_text": prediction.text if prediction else None,
            "span_text_matches": bool(prediction and source[start:end] == prediction.text),
            "before": source[max(previous_end, start - 1000):start],
            "after": source[end:min(next_start, end + 1000)],
            "same_paragraph_before": source[max(previous_end, paragraph.start):start] if paragraph else "",
            "same_paragraph_after": source[end:min(next_start, paragraph.end)] if paragraph else "",
            "context": source[max(0, start - 700):min(len(source), end + 700)],
            "decision": _line_payload(line) if line else None,
        })
    return rows


def run_book(book_dir, output_dir, limit=None):
    characters = _strip_descriptors(_load_json(book_dir / "characters.json"))
    chapters = _chapter_dirs(book_dir)
    if limit is not None:
        chapters = chapters[:limit]
    parser = BookNLPParserService()
    metrics = {"book": book_dir.name, "total": 0, "correct": 0, "matched": 0, "chapters": []}
    output_path = output_dir / f"{book_dir.name.lower()}_quotes.jsonl"
    with tempfile.TemporaryDirectory(prefix="openbook-miss-audit-") as temp_dir, output_path.open("w", encoding="utf-8") as output, (output_dir / f"{book_dir.name.lower()}_parser.log").open("w", encoding="utf-8") as log:
        review_root = Path(temp_dir)
        for chapter in chapters:
            with redirect_stdout(log):
                lines, _, meta = parser.parse_file(str(chapter / "chapter.txt"), options={
                    "parser_backend": "modernbooknlp",
                    "closed_world_characters": True,
                    "character_catalog": _catalog(characters),
                })
            rows = chapter_rows(chapter, characters, lines)
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            counts = {"total": len(rows), "correct": sum(row["correct"] for row in rows), "matched": sum(row["matched"] for row in rows)}
            metrics["chapters"].append({"chapter": chapter.name, **counts, "meta": meta})
            for key in counts:
                metrics[key] += counts[key]
            with redirect_stdout(log):
                characters, _ = _review_gold(review_root, chapter, _load_json(chapter / "dialogue.json"), lines, characters)
            _write_json(output_dir / f"{book_dir.name.lower()}_progress.json", metrics)
            print(f"{book_dir.name}/{chapter.name}: {counts['correct']}/{counts['total']}", flush=True)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit-chapters", type=int)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    books = [run_book(book.resolve(), args.output_dir, args.limit_chapters) for book in args.book_dir]
    summary = {"books": books, **{key: sum(book[key] for book in books) for key in ("total", "correct", "matched")}}
    _write_json(args.output_dir / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "books"}), flush=True)


if __name__ == "__main__":
    main()