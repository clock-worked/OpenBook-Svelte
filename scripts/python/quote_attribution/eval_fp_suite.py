#!/usr/bin/env python3
"""FP-aware evaluation suite for dialogue attribution experiments.

Extends the classic accuracy metric with the decomposition the accuracy
number hides:

    accuracy = assert rate x assert precision

Metrics per chapter and aggregate:
- accuracy          correct / total gold-dialogue lines (ledger-compatible)
- assert_rate       fraction of gold-dialogue lines where we assert a speaker
- assert_precision  correct / asserted  (THE false-positive metric, primary)
- recall            correct / total (== accuracy here, kept explicit)
- guess_acc         correct / total among NON-asserted lines (right suggestions)
                    identity: accuracy = assertRate x assertPrecision + guessAcc
- guess_hit@3       unknown lines whose gold speaker appears in the top-3
                    candidates (the "unknown with a good guess" success mode)

Gold layout: <book>/<chapter>/dialogue.json (characterId + span per line).
Predictions: JSON file produced by jev_verify_cli.py
  {"book": ..., "gate": ..., "chapters": {name: {"lines": [...], "summary": ...}}}
Alignment: span overlap, identical to evaluate_dialogue_ai_against_curations.py.

Usage:
  python eval_fp_suite.py <book_root> --predictions predictions.json
  python eval_fp_suite.py <book_root> --predictions a.json --baseline-summary b.json
"""

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PY_SERVICES_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "py_services"))
if PY_SERVICES_DIR not in sys.path:
    sys.path.insert(0, PY_SERVICES_DIR)

from openbook_parser.dialogue_parser_service import DEFAULT_BLOCKED_SPEAKERS  # noqa: E402

UNKNOWN_SPEAKERS = {"unknown", "unassigned"}
GUESS_K = 3


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _norm(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _slugify(value: Optional[str]) -> str:
    text = _norm(value)
    if not text:
        return "narrator"
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "narrator"


def _load_character_lookup(book_root: str) -> Dict[str, str]:
    characters_path = os.path.join(book_root, "characters.json")
    name_lookup: Dict[str, str] = {"narrator": "narrator"}
    if not os.path.exists(characters_path):
        return name_lookup
    payload = _load_json(characters_path)
    for entry in payload.get("characters", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        character_id = str(entry.get("id") or _slugify(name)).strip()
        for candidate in [name, character_id, *(entry.get("aliases") or [])]:
            key = _norm(candidate)
            if key:
                name_lookup[key] = character_id
    return name_lookup


def _resolve_id(raw: Optional[str], name_lookup: Dict[str, str]) -> Optional[str]:
    key = _norm(raw)
    if not key:
        return None
    if key in DEFAULT_BLOCKED_SPEAKERS or key in UNKNOWN_SPEAKERS:
        return None
    if key.startswith("[") and key.endswith("]"):
        return None
    return name_lookup.get(key) or _slugify(raw)


def _extract_span(span_value) -> Tuple[int, int]:
    if not isinstance(span_value, dict):
        return (0, 0)
    if "start" in span_value or "end" in span_value:
        return (int(span_value.get("start") or 0), int(span_value.get("end") or 0))
    return (int(span_value.get("spanStart") or 0), int(span_value.get("spanEnd") or 0))


def _overlap_size(left: Tuple[int, int], right: Tuple[int, int]) -> int:
    return max(0, min(left[1], right[1]) - max(left[0], right[0]))


def _find_best_match(gold_line: dict, predicted_lines: Sequence[dict]) -> Optional[dict]:
    gold_span = _extract_span(gold_line.get("span"))
    gold_text = str(gold_line.get("text") or "").strip()
    best: Optional[dict] = None
    best_overlap = -1
    best_exact = False
    for predicted in predicted_lines:
        overlap = _overlap_size(gold_span, _extract_span(predicted))
        if overlap <= 0:
            continue
        exact = bool(
            gold_text
            and str(predicted.get("text") or "").strip()
            and gold_text == str(predicted.get("text") or "").strip()
        )
        if overlap > best_overlap or (overlap == best_overlap and exact and not best_exact):
            best = predicted
            best_overlap = overlap
            best_exact = exact
    return best


def _top_candidate_ids(predicted: dict, name_lookup: Dict[str, str], k: int) -> List[Optional[str]]:
    attribution = predicted.get("attribution")
    rows = attribution.get("candidates") if isinstance(attribution, dict) else None
    if not isinstance(rows, list) or not rows:
        rows = [{"name": name} for name in (predicted.get("suggestions") or [])]
    ids: List[Optional[str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        char_id = _resolve_id(row.get("name"), name_lookup)
        if char_id and char_id not in ids:
            ids.append(char_id)
        if len(ids) >= k:
            break
    return ids


def score_chapter(
    gold_lines: Sequence[dict],
    predicted_lines: Sequence[dict],
    name_lookup: Dict[str, str],
    max_mismatches: int,
) -> Dict[str, object]:
    dialogue_total = 0
    correct = 0
    asserted = 0
    assert_correct = 0
    guess_hits = 0
    mismatches: List[Dict[str, object]] = []

    for index, gold_line in enumerate(gold_lines):
        gold_id = _norm(gold_line.get("characterId")) or "narrator"
        if gold_id == "narrator":
            continue
        dialogue_total += 1

        predicted = _find_best_match(gold_line, predicted_lines)
        if predicted is None:
            pred_id = "<missing>"
            is_assert = False
            top_ids: List[Optional[str]] = []
        else:
            pred_id = _resolve_id(predicted.get("speaker"), name_lookup)
            pred_id = pred_id or "narrator"
            is_assert = (
                predicted.get("lineType") == "dialogue"
                and not predicted.get("isSuggestion")
                and pred_id not in (None, "narrator")
            )
            top_ids = _top_candidate_ids(predicted, name_lookup, GUESS_K)

        if pred_id == gold_id:
            correct += 1
            if is_assert:
                asserted += 1
                assert_correct += 1
            continue

        if is_assert:
            asserted += 1
        elif gold_id in top_ids:
            guess_hits += 1

        if len(mismatches) < max_mismatches:
            mismatches.append(
                {
                    "index": index + 1,
                    "gold": gold_id,
                    "pred": pred_id,
                    "asserted": is_assert,
                    "guessHit": gold_id in top_ids,
                    "text": str(gold_line.get("text", ""))[:180],
                }
            )

    def ratio(numerator: int, denominator: int) -> float:
        return (numerator / denominator) if denominator else 0.0

    return {
        "dialogue_total": dialogue_total,
        "correct": correct,
        "accuracy": ratio(correct, dialogue_total),
        "asserted": asserted,
        "assert_rate": ratio(asserted, dialogue_total),
        "assert_correct": assert_correct,
        "assert_precision": ratio(assert_correct, asserted),
        "recall": ratio(correct, dialogue_total),
        "correct_non_assert": correct - assert_correct,
        "guess_acc": ratio(correct - assert_correct, dialogue_total),
        "guess_hits": guess_hits,
        "guess_hit_rate": ratio(guess_hits, dialogue_total),
        "mismatches": mismatches,
    }


def _aggregate(rows: Sequence[Dict[str, object]]) -> Dict[str, object]:
    totals = {
        key: sum(int(row.get(key) or 0) for row in rows)
        for key in (
            "dialogue_total",
            "correct",
            "asserted",
            "assert_correct",
            "guess_hits",
        )
    }

    def ratio(numerator: int, denominator: int) -> float:
        return (numerator / denominator) if denominator else 0.0

    correct_non_assert = totals["correct"] - totals["assert_correct"]
    return {
        "dialogue_total": totals["dialogue_total"],
        "correct": totals["correct"],
        "accuracy": ratio(totals["correct"], totals["dialogue_total"]),
        "asserted": totals["asserted"],
        "assert_rate": ratio(totals["asserted"], totals["dialogue_total"]),
        "assert_correct": totals["assert_correct"],
        "assert_precision": ratio(totals["assert_correct"], totals["asserted"]),
        "recall": ratio(totals["correct"], totals["dialogue_total"]),
        "correct_non_assert": correct_non_assert,
        "guess_acc": ratio(correct_non_assert, totals["dialogue_total"]),
        "guess_hits": totals["guess_hits"],
        "guess_hit_rate": ratio(totals["guess_hits"], totals["dialogue_total"]),
    }


def _print_row(label: str, row: Dict[str, object]) -> None:
    print(
        f"{label:<52} acc={row['accuracy']:.4f} assertRate={row['assert_rate']:.4f} "
        f"assertPrec={row['assert_precision']:.4f} guessHit@{GUESS_K}={row['guess_hit_rate']:.4f} "
        f"({row['correct']}/{row['dialogue_total']}, asserted={row['asserted']})"
    )


def main() -> None:
    # Chapter text can contain private-use-area characters (U+F000 block)
    # that crash printing on cp1252 consoles.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="FP-aware evaluation suite for attribution experiment predictions."
    )
    parser.add_argument("book_root", help="Book directory with curated chapter folders")
    parser.add_argument("--predictions", required=True, help="Predictions JSON from jev_verify_cli.py")
    parser.add_argument("--chapter-regex", default=None, help="Regex filter on chapter names")
    parser.add_argument("--out", default=None, help="Summary JSON output path")
    parser.add_argument("--baseline-summary", default=None, help="Saved summary JSON to compare against")
    parser.add_argument("--max-mismatches", type=int, default=10, help="Max mismatches kept per chapter")
    args = parser.parse_args()

    book_root = os.path.abspath(args.book_root)
    predictions = _load_json(args.predictions)
    name_lookup = _load_character_lookup(book_root)
    matcher = re.compile(args.chapter_regex) if args.chapter_regex else None

    chapter_rows: Dict[str, Dict[str, object]] = {}
    for chapter_name, chapter_data in sorted((predictions.get("chapters") or {}).items()):
        if matcher and not matcher.search(chapter_name):
            continue
        dialogue_path = os.path.join(book_root, chapter_name, "dialogue.json")
        if not os.path.exists(dialogue_path):
            print(f"  ! no gold dialogue.json for {chapter_name}; skipped", file=sys.stderr)
            continue
        gold_lines = list(_load_json(dialogue_path).get("lines", []))
        predicted_lines = list(chapter_data.get("lines") or [])
        chapter_rows[chapter_name] = score_chapter(
            gold_lines, predicted_lines, name_lookup, args.max_mismatches
        )

    if not chapter_rows:
        raise SystemExit("No chapters scored (missing gold?)")

    print("=== FP suite ===")
    for chapter_name, row in chapter_rows.items():
        _print_row(chapter_name, row)
    aggregate = _aggregate(chapter_rows.values())
    _print_row("AGGREGATE", aggregate)
    assert_part = aggregate["assert_rate"] * aggregate["assert_precision"]
    print(
        f"decomposition check: assertRate x assertPrecision + guessAcc = "
        f"{aggregate['assert_rate']:.4f} x {aggregate['assert_precision']:.4f} + "
        f"{aggregate['guess_acc']:.4f} = {assert_part + aggregate['guess_acc']:.4f} "
        f"(accuracy={aggregate['accuracy']:.4f})"
    )

    summary = {
        "book_root": book_root,
        "predictions": os.path.abspath(args.predictions),
        "gate": predictions.get("gate"),
        "aggregate": aggregate,
        "chapters": chapter_rows,
    }
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, ensure_ascii=False)
        print(f"\nSummary written: {args.out}")

    if args.baseline_summary:
        baseline = _load_json(args.baseline_summary)
        base = baseline.get("aggregate", baseline)
        print("\n=== Baseline comparison ===")
        for metric in ("accuracy", "assert_rate", "assert_precision", "recall", "guess_hit_rate"):
            current = float(aggregate.get(metric) or 0.0)
            previous = float(base.get(metric) or 0.0)
            print(f"{metric}: {current:.4f} (baseline {previous:.4f}, delta {current - previous:+.4f})")


if __name__ == "__main__":
    main()
