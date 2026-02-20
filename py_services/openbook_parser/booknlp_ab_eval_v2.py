#!/usr/bin/env python3

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

from openbook_parser.knowledge_store import load_knowledge_for_file


@dataclass
class MethodMetrics:
    total: int = 0
    correct: int = 0
    dialogue_total: int = 0
    dialogue_correct: int = 0

    @property
    def overall_acc(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def dialogue_acc(self) -> float:
        return self.dialogue_correct / self.dialogue_total if self.dialogue_total else 0.0


@dataclass
class ChapterAB:
    chapter: str
    gold_lines: int
    parser_pred_lines: int
    parser: MethodMetrics
    booknlp: MethodMetrics
    parser_delta_lines: int
    quotes_count: int
    aligned_quotes_count: int


def normalize_speaker(value: Optional[str]) -> str:
    if value is None:
        return "narrator"
    text = str(value).strip()
    if not text:
        return "narrator"
    return text.lower()


def normalize_text(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"^[\s\"'`“”‘’]+|[\s\"'`“”‘’]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def read_tsv(path: str) -> List[List[str]]:
    rows: List[List[str]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            rows.append(line.split("\t"))
    return rows


def iter_chapters(book_root: str, chapter_regex: Optional[str], limit: int) -> List[Tuple[str, str, str]]:
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


def safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return slug or "chapter"


def load_booknlp() -> "BookNLP":
    try:
        from booknlp.booknlp import BookNLP
    except Exception as exc:
        raise SystemExit(
            "BookNLP is not installed in this environment. Install with: "
            "pip install booknlp && python -m spacy download en_core_web_sm"
        ) from exc
    return BookNLP


def load_parser_service() -> Optional["DialogueParserService"]:
    try:
        from openbook_parser.dialogue_parser_service import DialogueParserService
    except Exception:
        return None
    try:
        return DialogueParserService()
    except Exception:
        return None


def run_booknlp_for_chapter(
    booknlp,
    txt_path: str,
    output_root: str,
    chapter_name: str,
    rerun: bool,
) -> Tuple[str, str]:
    slug = safe_slug(chapter_name)
    chapter_out_dir = os.path.join(output_root, slug)
    os.makedirs(chapter_out_dir, exist_ok=True)
    book_id = "chapter"
    quotes_path = os.path.join(chapter_out_dir, f"{book_id}.quotes")
    entities_path = os.path.join(chapter_out_dir, f"{book_id}.entities")

    normalized_input = os.path.join(chapter_out_dir, f"{book_id}.input.utf8.txt")
    with open(txt_path, "rb") as source_handle:
        raw_bytes = source_handle.read()
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        decoded = raw_bytes.decode("cp1252", errors="replace")
    with open(normalized_input, "w", encoding="utf-8", errors="replace", newline="") as normalized_handle:
        normalized_handle.write(decoded)

    if rerun or not (os.path.exists(quotes_path) and os.path.exists(entities_path)):
        booknlp.process(normalized_input, chapter_out_dir, book_id)

    return quotes_path, entities_path


def build_coref_name_map(entities_path: str) -> Dict[str, str]:
    rows = read_tsv(entities_path)
    if not rows:
        return {}
    start_idx = 1 if rows and rows[0] and rows[0][0].upper() == "COREF" else 0
    names: Dict[str, Counter] = {}
    for cols in rows[start_idx:]:
        if len(cols) < 6:
            continue
        coref = cols[0]
        prop = cols[3]
        cat = cols[4]
        text = cols[5].strip()
        if cat != "PER" or not text:
            continue
        if coref not in names:
            names[coref] = Counter()
        weight = 10.0 if prop == "PROP" else (1.0 if prop == "NOM" else 0.001)
        names[coref][text] += weight

    best: Dict[str, str] = {}
    for coref, counter in names.items():
        if counter:
            best[coref] = counter.most_common(1)[0][0]
    return best


def canonicalize_name(raw_name: str, aliases: Dict[str, str], canonicals: Dict[str, str]) -> str:
    if not raw_name:
        return "narrator"
    name = re.sub(r"\s+", " ", raw_name.strip())
    lowered = name.lower()

    if lowered in aliases:
        return aliases[lowered]
    if lowered in canonicals:
        return canonicals[lowered]

    simplified = re.sub(r"[^a-z0-9\s'-]", "", lowered)
    if simplified in aliases:
        return aliases[simplified]
    if simplified in canonicals:
        return canonicals[simplified]

    return lowered


def load_booknlp_quotes(
    quotes_path: str,
    coref_name_map: Dict[str, str],
    aliases: Dict[str, str],
    canonicals: Dict[str, str],
) -> List[Dict[str, str]]:
    rows = read_tsv(quotes_path)
    if not rows:
        return []
    start_idx = 1 if rows and rows[0] and rows[0][0] == "quote_start" else 0
    out: List[Dict[str, str]] = []
    for cols in rows[start_idx:]:
        if len(cols) < 7:
            continue
        char_id = cols[5].strip()
        quote_text = cols[6].strip()
        raw_name = coref_name_map.get(char_id) or cols[4].strip() or "narrator"
        canonical = canonicalize_name(raw_name, aliases=aliases, canonicals=canonicals)
        out.append(
            {
                "quote": quote_text,
                "quote_norm": normalize_text(quote_text),
                "speaker": canonical,
            }
        )
    return out


def align_quotes_to_dialogue(gold_lines: List[Dict[str, object]], quotes: List[Dict[str, str]]) -> Tuple[Dict[int, str], int]:
    speaker_by_line: Dict[int, str] = {}
    qpos = 0
    aligned = 0

    for i, line in enumerate(gold_lines):
        gold_speaker = normalize_speaker(line.get("characterId"))
        if gold_speaker == "narrator":
            continue

        line_norm = normalize_text(str(line.get("text", "")))
        chosen = None

        lookahead = min(qpos + 8, len(quotes))
        for j in range(qpos, lookahead):
            if quotes[j]["quote_norm"] == line_norm and line_norm:
                chosen = j
                break

        if chosen is None and qpos < len(quotes):
            chosen = qpos

        if chosen is None:
            speaker_by_line[i] = "narrator"
            continue

        speaker_by_line[i] = normalize_speaker(quotes[chosen]["speaker"])
        aligned += 1
        qpos = chosen + 1

    return speaker_by_line, aligned


def evaluate_methods(
    gold_lines: List[Dict[str, object]],
    parser_pred_lines,
    booknlp_speaker_by_line: Dict[int, str],
    parser_available: bool,
) -> Tuple[MethodMetrics, MethodMetrics]:
    parser_metrics = MethodMetrics()
    booknlp_metrics = MethodMetrics()

    total_len = len(gold_lines)
    for idx in range(total_len):
        g = gold_lines[idx]
        gspeaker = normalize_speaker(g.get("characterId"))
        is_dialogue = gspeaker != "narrator"

        if parser_available:
            parser_metrics.total += 1
        booknlp_metrics.total += 1
        if is_dialogue:
            if parser_available:
                parser_metrics.dialogue_total += 1
            booknlp_metrics.dialogue_total += 1

        # Current parser
        if parser_available:
            parser_pred = "narrator"
            if parser_pred_lines is not None and idx < len(parser_pred_lines):
                parser_pred = normalize_speaker(getattr(parser_pred_lines[idx], "speaker", None))
            if parser_pred == gspeaker:
                parser_metrics.correct += 1
                if is_dialogue:
                    parser_metrics.dialogue_correct += 1

        # BookNLP mapped
        book_pred = booknlp_speaker_by_line.get(idx, "narrator")
        if book_pred == gspeaker:
            booknlp_metrics.correct += 1
            if is_dialogue:
                booknlp_metrics.dialogue_correct += 1

    return parser_metrics, booknlp_metrics


def write_csv(path: str, rows: List[ChapterAB]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
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
        )
        for row in rows:
            writer.writerow(
                [
                    row.chapter,
                    row.gold_lines,
                    row.parser_pred_lines,
                    row.parser_delta_lines,
                    row.quotes_count,
                    row.aligned_quotes_count,
                    f"{row.parser.overall_acc:.6f}",
                    f"{row.parser.dialogue_acc:.6f}",
                    f"{row.booknlp.overall_acc:.6f}",
                    f"{row.booknlp.dialogue_acc:.6f}",
                    f"{(row.booknlp.dialogue_acc - row.parser.dialogue_acc):.6f}",
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B evaluate current parser vs BookNLP on curated dialogue.json chapters")
    parser.add_argument("book_root", help="Path to Book-X folder with chapter subfolders")
    parser.add_argument("--chapter-regex", default=None, help="Regex filter for chapter folder names")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate first N matching chapters")
    parser.add_argument(
        "--booknlp-output-root",
        default=None,
        help="Folder to write BookNLP intermediate outputs (default: <book_root>/.booknlp_ab)",
    )
    parser.add_argument("--booknlp-model", choices=["small", "big"], default="small", help="BookNLP model size")
    parser.add_argument("--booknlp-rerun", action="store_true", help="Force rerunning BookNLP even if outputs exist")
    parser.add_argument("--output-csv", default=None, help="Output CSV path (default: <book_root>/booknlp_ab_eval.csv)")
    args = parser.parse_args()

    book_root = os.path.abspath(args.book_root)
    output_root = args.booknlp_output_root or os.path.join(book_root, ".booknlp_ab")
    output_csv = args.output_csv or os.path.join(book_root, "booknlp_ab_eval.csv")

    chapter_items = iter_chapters(book_root, args.chapter_regex, args.limit)
    if not chapter_items:
        raise SystemExit("No chapters found with chapter.txt + dialogue.json")

    BookNLP = load_booknlp()
    model_params = {
        "pipeline": "entity,quote,coref",
        "model": args.booknlp_model,
    }
    booknlp = BookNLP("en", model_params)

    parser_service = load_parser_service()
    parser_available = parser_service is not None
    rows: List[ChapterAB] = []

    agg_parser = MethodMetrics()
    agg_booknlp = MethodMetrics()

    for chapter_name, txt_path, dialogue_path in chapter_items:
        with open(dialogue_path, "r", encoding="utf-8") as handle:
            gold_lines = json.load(handle).get("lines", [])

        parser_pred_lines = None
        if parser_available:
            parser_pred_lines, _ = parser_service.parse_file(txt_path)

        quotes_path, entities_path = run_booknlp_for_chapter(
            booknlp=booknlp,
            txt_path=txt_path,
            output_root=output_root,
            chapter_name=chapter_name,
            rerun=args.booknlp_rerun,
        )

        coref_name_map = build_coref_name_map(entities_path)

        ks = load_knowledge_for_file(txt_path)
        aliases: Dict[str, str] = {}
        canonicals: Dict[str, str] = {}
        if ks:
            for alias, canonical in ks.aliases.items():
                aliases[normalize_text(alias)] = normalize_speaker(canonical)
            for canonical in ks.genders.keys():
                canonicals[normalize_text(canonical)] = normalize_speaker(canonical)

        quotes = load_booknlp_quotes(
            quotes_path=quotes_path,
            coref_name_map=coref_name_map,
            aliases=aliases,
            canonicals=canonicals,
        )
        booknlp_speaker_by_line, aligned_quotes = align_quotes_to_dialogue(gold_lines, quotes)

        parser_metrics, booknlp_metrics = evaluate_methods(
            gold_lines=gold_lines,
            parser_pred_lines=parser_pred_lines,
            booknlp_speaker_by_line=booknlp_speaker_by_line,
            parser_available=parser_available,
        )

        agg_parser.total += parser_metrics.total
        agg_parser.correct += parser_metrics.correct
        agg_parser.dialogue_total += parser_metrics.dialogue_total
        agg_parser.dialogue_correct += parser_metrics.dialogue_correct

        agg_booknlp.total += booknlp_metrics.total
        agg_booknlp.correct += booknlp_metrics.correct
        agg_booknlp.dialogue_total += booknlp_metrics.dialogue_total
        agg_booknlp.dialogue_correct += booknlp_metrics.dialogue_correct

        rows.append(
            ChapterAB(
                chapter=chapter_name,
                gold_lines=len(gold_lines),
                parser_pred_lines=(len(parser_pred_lines) if parser_pred_lines is not None else 0),
                parser=parser_metrics,
                booknlp=booknlp_metrics,
                parser_delta_lines=((len(parser_pred_lines) - len(gold_lines)) if parser_pred_lines is not None else 0),
                quotes_count=len(quotes),
                aligned_quotes_count=aligned_quotes,
            )
        )

    write_csv(output_csv, rows)

    print("=== A/B Evaluation: Current Parser vs BookNLP ===")
    print(f"Chapters evaluated: {len(rows)}")
    if not parser_available:
        print("Current parser unavailable in this environment; parser metrics are N/A.")
    if parser_available:
        print(
            f"Current parser dialogue accuracy: {agg_parser.dialogue_acc:.4f} "
            f"({agg_parser.dialogue_correct}/{agg_parser.dialogue_total})"
        )
    else:
        print("Current parser dialogue accuracy: N/A")
    print(
        f"BookNLP dialogue accuracy: {agg_booknlp.dialogue_acc:.4f} "
        f"({agg_booknlp.dialogue_correct}/{agg_booknlp.dialogue_total})"
    )
    if parser_available:
        print(f"Dialogue delta (BookNLP - parser): {(agg_booknlp.dialogue_acc - agg_parser.dialogue_acc):+.4f}")
    else:
        print("Dialogue delta (BookNLP - parser): N/A")

    print("\nPer-chapter dialogue deltas (BookNLP - parser):")
    for row in rows:
        delta = row.booknlp.dialogue_acc - row.parser.dialogue_acc if parser_available else None
        parser_text = f"{row.parser.dialogue_acc:.3f}" if parser_available else "N/A"
        delta_text = f"{delta:+.3f}" if parser_available else "N/A"
        print(
            f"- {row.chapter}: parser={parser_text}, "
            f"booknlp={row.booknlp.dialogue_acc:.3f}, delta={delta_text}, "
            f"quotes={row.quotes_count}, aligned={row.aligned_quotes_count}"
        )

    print(f"\nCSV written: {output_csv}")


if __name__ == "__main__":
    main()
