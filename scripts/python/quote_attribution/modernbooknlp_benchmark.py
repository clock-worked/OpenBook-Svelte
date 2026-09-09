#!/usr/bin/env python
"""Run and score ModernBookNLP quotation attribution on curated OpenBook books."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys

# Long fantasy tokens (for example malformed no-space passages) can exceed the
# csv module's conservative default field size.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class ChapterMap:
    chapter: str
    byte_start: int
    byte_end: int


@dataclass(frozen=True)
class GoldQuote:
    chapter: str
    line_id: int
    character_id: Optional[str]
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class PredictedQuote:
    start: int
    end: int
    character_id: Optional[str]
    coref_id: Optional[str]
    text: str


@dataclass(frozen=True)
class InputCorpus:
    text: str
    chapters: list[ChapterMap]
    gold_quotes: list[GoldQuote]


def normalize_surface(value: object) -> str:
    text = str(value or "").casefold().replace("_", " ").replace("-", " ")
    return " ".join(re.findall(r"[\w']+", text, flags=re.UNICODE))


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def build_canonical_lookup(characters: Mapping[str, object]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    ambiguous: set[str] = set()
    for character in characters.get("characters", []):
        if not isinstance(character, Mapping):
            continue
        character_id = str(character.get("id") or "").strip()
        if not character_id:
            continue
        surfaces = [character_id, character.get("name"), *(character.get("aliases") or [])]
        for surface in surfaces:
            key = normalize_surface(surface)
            if not key:
                continue
            if key in lookup and lookup[key] != character_id:
                ambiguous.add(key)
            else:
                lookup[key] = character_id
    for key in ambiguous:
        lookup.pop(key, None)
    return lookup


def _chapter_dirs(book_dir: Path) -> list[Path]:
    return sorted(
        chapter
        for chapter in book_dir.iterdir()
        if chapter.is_dir()
        and not chapter.name.startswith(".")
        and (chapter / "chapter.txt").is_file()
        and (chapter / "dialogue.json").is_file()
    )


def _character_id_for_line(line: Mapping[str, object], lookup: Mapping[str, str]) -> Optional[str]:
    raw_value = line.get("characterId") if "characterId" in line else line.get("chosenSpeaker")
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    return lookup.get(normalize_surface(raw), raw)


def build_input_corpus(book_dir: Path) -> InputCorpus:
    book_dir = book_dir.resolve()
    characters_path = book_dir / "characters.json"
    characters = load_json(characters_path) if characters_path.is_file() else {"characters": []}
    lookup = build_canonical_lookup(characters)
    pieces: list[str] = []
    chapters: list[ChapterMap] = []
    gold: list[GoldQuote] = []
    byte_cursor = 0

    for chapter in _chapter_dirs(book_dir):
        text = (chapter / "chapter.txt").read_text(encoding="utf-8-sig")
        if pieces:
            pieces.append("\n\n")
            byte_cursor += 2
        chapter_start = byte_cursor
        pieces.append(text)
        chapter_size = len(text)
        chapters.append(ChapterMap(chapter.name, chapter_start, chapter_start + chapter_size))

        dialogue = load_json(chapter / "dialogue.json")
        search_cursor = 0
        for line in dialogue.get("lines", []):
            if not isinstance(line, Mapping):
                continue
            character_id = _character_id_for_line(line, lookup)
            is_legacy = "characterId" not in line
            if (is_legacy and not line.get("chosenSpeaker")) or (
                character_id and character_id.casefold() == "narrator"
            ):
                continue
            line_text = str(line.get("text") or "")
            local_start = text.find(line_text, search_cursor) if line_text else -1
            if local_start < 0 and line_text:
                local_start = text.find(line_text)
            if local_start >= 0:
                local_end = local_start + len(line_text)
                search_cursor = local_end
                start, end = chapter_start + local_start, chapter_start + local_end
            else:
                # Keep unalignable gold in the denominator and provenance.
                start = end = -1
            gold.append(GoldQuote(
                chapter=chapter.name,
                line_id=int(line.get("id", -1)),
                character_id=character_id,
                start=start,
                end=end,
                text=line_text,
            ))
        byte_cursor += chapter_size

    return InputCorpus("".join(pieces), chapters, gold)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def map_coref_clusters(
    entities: Iterable[Mapping[str, object]],
    canonical_lookup: Mapping[str, str],
) -> dict[str, Optional[str]]:
    scores: dict[str, Counter[str]] = defaultdict(Counter)
    seen_corefs: set[str] = set()
    for entity in entities:
        coref = str(entity.get("coref") or entity.get("COREF") or "").strip()
        if not coref:
            continue
        seen_corefs.add(coref)
        canonical = canonical_lookup.get(normalize_surface(entity.get("text")))
        if not canonical:
            continue
        prop = str(entity.get("prop") or "").upper()
        weight = 10 if prop == "PROP" else 2 if prop == "NOM" else 1
        scores[coref][canonical] += weight

    result: dict[str, Optional[str]] = {}
    for coref in seen_corefs:
        ranked = scores[coref].most_common()
        result[coref] = ranked[0][0] if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]) else None
    return result


def load_modernbooknlp_predictions(
    quotes_path: Path,
    tokens_path: Path,
    coref_mapping: Mapping[str, Optional[str]],
) -> list[PredictedQuote]:
    # Quote indices use token_ID_within_document, and quote_end is exclusive.
    # The token export can omit IDs after long-token splitting, so use the
    # nearest exported token to preserve source-text boundaries.
    token_spans: dict[int, tuple[int, int]] = {}
    for token in read_tsv(tokens_path):
        try:
            token_id = int(token["token_ID_within_document"])
            token_spans[token_id] = (int(token["byte_onset"]), int(token["byte_offset"]))
        except (KeyError, TypeError, ValueError):
            continue
    token_ids = sorted(token_spans)

    predictions: list[PredictedQuote] = []
    for quote in read_tsv(quotes_path):
        try:
            start_token = int(quote["quote_start"])
            end_token = int(quote["quote_end"])
        except (KeyError, TypeError, ValueError):
            continue
        start_id = next((token_id for token_id in token_ids if token_id >= start_token), None)
        end_id = next((token_id for token_id in reversed(token_ids) if token_id < end_token), None)
        if start_id is None or end_id is None or start_id > end_id:
            continue
        start = token_spans[start_id][0]
        end = token_spans[end_id][1]
        raw_coref = str(quote.get("char_id") or "").strip()
        coref_id = raw_coref if raw_coref and raw_coref.casefold() != "none" else None
        predictions.append(PredictedQuote(
            start=start,
            end=end,
            character_id=coref_mapping.get(coref_id) if coref_id else None,
            coref_id=coref_id,
            text=str(quote.get("quote") or ""),
        ))
    return predictions


def _overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> int:
    return max(0, min(left_end, right_end) - max(left_start, right_start))


def align_by_overlap(
    gold: Sequence[GoldQuote],
    predicted: Sequence[PredictedQuote],
) -> list[tuple[int, int, int]]:
    # Ordered dynamic programming: maximize match count first, then overlap.
    n, m = len(gold), len(predicted)
    previous = [0.0] * (m + 1)
    directions = [bytearray(m + 1) for _ in range(n + 1)]
    overlaps: dict[tuple[int, int], int] = {}
    for i, gold_quote in enumerate(gold, 1):
        current = [0.0] * (m + 1)
        for j, predicted_quote in enumerate(predicted, 1):
            overlap = _overlap(gold_quote.start, gold_quote.end, predicted_quote.start, predicted_quote.end)
            min_length = min(gold_quote.end - gold_quote.start, predicted_quote.end - predicted_quote.start)
            eligible = min_length > 0 and overlap / min_length >= 0.5
            match_score = previous[j - 1] + 1_000_000 + overlap if eligible else -1.0
            if match_score >= previous[j] and match_score >= current[j - 1]:
                current[j] = match_score
                directions[i][j] = 3
                overlaps[(i - 1, j - 1)] = overlap
            elif previous[j] >= current[j - 1]:
                current[j] = previous[j]
                directions[i][j] = 1
            else:
                current[j] = current[j - 1]
                directions[i][j] = 2
        previous = current

    matches: list[tuple[int, int, int]] = []
    i, j = n, m
    while i and j:
        direction = directions[i][j]
        if direction == 3:
            matches.append((i - 1, j - 1, overlaps[(i - 1, j - 1)]))
            i -= 1
            j -= 1
        elif direction == 1:
            i -= 1
        else:
            j -= 1
    matches.reverse()
    return matches


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_predictions(
    gold: Sequence[GoldQuote],
    predicted: Sequence[PredictedQuote],
) -> dict[str, object]:
    matches = align_by_overlap(gold, predicted)
    matched = len(matches)
    precision = _ratio(matched, len(predicted))
    recall = _ratio(matched, len(gold))
    labeled_matches = [(g, p, o) for g, p, o in matches if gold[g].character_id is not None]
    correct = sum(
        gold[gold_index].character_id == predicted[predicted_index].character_id
        for gold_index, predicted_index, _ in labeled_matches
    )
    resolved = sum(predicted[predicted_index].character_id is not None for _, predicted_index, _ in labeled_matches)
    details = [
        {
            "gold": asdict(gold[gold_index]),
            "prediction": asdict(predicted[predicted_index]),
            "overlap_bytes": overlap,
            "speaker_correct": gold[gold_index].character_id == predicted[predicted_index].character_id,
        }
        for gold_index, predicted_index, overlap in matches
    ]
    return {
        "gold_quotes": len(gold),
        "predicted_quotes": len(predicted),
        "matched_quotes": matched,
        "quote_detection": {
            "precision": precision,
            "recall": recall,
            "f1": _ratio(2 * matched, len(gold) + len(predicted)),
        },
        "speaker_attribution": {
            "correct": correct,
            "resolved": resolved,
            "labeled_matched": len(labeled_matches),
            "unresolved_gold": sum(quote.character_id is None for quote in gold),
            "accuracy_on_matched": _ratio(correct, len(labeled_matches)),
            "accuracy_on_resolved_matched": _ratio(correct, resolved),
        },
        "end_to_end_correct_speaker_recall": _ratio(correct, len(gold)),
        "matches": details,
    }


def run_modernbooknlp(input_path: Path, output_dir: Path, book_id: str, direct: bool = False) -> None:
    try:
        from booknlp.english.english_booknlp import EnglishBookNLP
    except ImportError as exc:
        raise RuntimeError("ModernBookNLP is not installed in this Python environment") from exc

    params = {
        "pipeline": "entity,quote,coref",
        "model": "big",
        "modern_qa": True,
        "direct_qa": direct,
    }
    model = EnglishBookNLP(params)
    output_dir.mkdir(parents=True, exist_ok=True)
    model.process(str(input_path), str(output_dir), book_id)


def score_existing(book_dir: Path, modern_output: Path, book_id: str) -> dict[str, object]:
    corpus = build_input_corpus(book_dir)
    characters = load_json(book_dir / "characters.json")
    lookup = build_canonical_lookup(characters)
    entities = read_tsv(modern_output / f"{book_id}.entities")
    coref_mapping = map_coref_clusters(entities, lookup)
    predictions = load_modernbooknlp_predictions(
        modern_output / f"{book_id}.quotes",
        modern_output / f"{book_id}.tokens",
        coref_mapping,
    )
    result = evaluate_predictions(corpus.gold_quotes, predictions)
    result["book"] = book_dir.name
    result["coref_clusters"] = len(coref_mapping)
    result["mapped_coref_clusters"] = sum(value is not None for value in coref_mapping.values())
    return result


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--book-id", default=None)
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--direct", action="store_true", help="Use direct rather than joint ModernBookNLP scoring")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    book_dir = args.book_dir.resolve()
    work_dir = args.work_dir.resolve()
    book_id = args.book_id or normalize_surface(book_dir.name).replace(" ", "_")
    work_dir.mkdir(parents=True, exist_ok=True)
    input_path = work_dir / f"{book_id}.txt"
    output_dir = work_dir / "modernbooknlp"

    corpus = build_input_corpus(book_dir)
    input_path.write_text(corpus.text, encoding="utf-8", newline="")
    (work_dir / f"{book_id}.chapters.json").write_text(
        json.dumps([asdict(chapter) for chapter in corpus.chapters], indent=2), encoding="utf-8"
    )
    if not args.score_only:
        run_modernbooknlp(input_path, output_dir, book_id, direct=args.direct)
    result = score_existing(book_dir, output_dir, book_id)
    result["openbook_frozen_baseline"] = {"Book-14": 0.594468, "Book-15": 0.700331}.get(book_dir.name)
    result_path = work_dir / f"{book_id}.benchmark.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    summary = {key: value for key, value in result.items() if key != "matches"}
    print(json.dumps(summary, indent=2))
    print(f"Detailed results: {result_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
