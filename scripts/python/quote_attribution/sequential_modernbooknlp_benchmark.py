#!/usr/bin/env python
"""Benchmark integrated quote attribution with sequential descriptor learning."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Iterable, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from py_services.chapter_review_service import review_chapter
from py_services.openbook_parser.booknlp_parser_service import BookNLPParserService


@dataclass(frozen=True)
class ScoredQuote:
    line_id: int
    start: int
    end: int
    speaker: Optional[str]
    text: str


@dataclass
class Metrics:
    total: int = 0
    correct: int = 0
    matched: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class ConditionState:
    name: str
    backend: str
    learn_descriptors: bool
    characters: dict
    metrics: Metrics
    chapters: list[dict]
    descriptors_added: int = 0


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize(value: object) -> str:
    text = str(value or "").casefold().replace("_", " ").replace("-", " ")
    return " ".join(re.findall(r"[\w']+", text, flags=re.UNICODE))


def _chapter_dirs(book_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in book_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and (path / "chapter.txt").is_file()
        and (path / "dialogue.json").is_file()
    )


def _strip_descriptors(characters: dict) -> dict:
    result = deepcopy(characters)
    for character in result.get("characters", []):
        if isinstance(character, dict):
            character["descriptors"] = []
    return result


def _descriptor_seed(characters: dict) -> dict[str, list[str]]:
    return {
        str(character.get("id") or "").strip(): list(character.get("descriptors") or [])
        for character in characters.get("characters", [])
        if isinstance(character, dict) and str(character.get("id") or "").strip()
    }


def _merge_seed(characters: dict, seeds: Iterable[Mapping[str, Sequence[str]]]) -> dict:
    result = _strip_descriptors(characters)
    by_id = {
        str(character.get("id") or "").strip(): character
        for character in result.get("characters", [])
        if isinstance(character, dict)
    }
    for seed in seeds:
        for character_id, descriptors in seed.items():
            character = by_id.get(character_id)
            if not character:
                continue
            known = {_normalize(value) for value in character.get("descriptors") or []}
            for descriptor in descriptors:
                if _normalize(descriptor) and _normalize(descriptor) not in known:
                    character.setdefault("descriptors", []).append(descriptor)
                    known.add(_normalize(descriptor))
    return result


def _catalog(characters: dict) -> list[dict]:
    return [
        {
            "characterId": character.get("id"),
            "name": character.get("name"),
            "aliases": list(character.get("aliases") or []),
            "descriptors": list(character.get("descriptors") or []),
            "gender": character.get("gender") or "Unknown",
        }
        for character in characters.get("characters", [])
        if isinstance(character, dict) and character.get("id") and character.get("name")
    ]


def _speaker_lookup(characters: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    ambiguous: set[str] = set()
    for character in characters.get("characters", []):
        if not isinstance(character, dict):
            continue
        character_id = str(character.get("id") or "").strip()
        for surface in [character_id, character.get("name"), *(character.get("aliases") or [])]:
            key = _normalize(surface)
            if not key:
                continue
            if key in lookup and lookup[key] != character_id:
                ambiguous.add(key)
            else:
                lookup[key] = character_id
    for key in ambiguous:
        lookup.pop(key, None)
    return lookup


def _line_span(line: Mapping[str, object]) -> tuple[int, int]:
    span = line.get("span") if isinstance(line.get("span"), Mapping) else {}
    try:
        return int(span.get("start", -1)), int(span.get("end", -1))
    except (TypeError, ValueError):
        return -1, -1


def _gold_speaker(line: Mapping[str, object], characters: dict) -> Optional[str]:
    raw = line.get("characterId") if "characterId" in line else line.get("chosenSpeaker")
    value = str(raw or "").strip()
    if not value:
        return None
    return _speaker_lookup(characters).get(_normalize(value), value)


def _gold_quotes(dialogue: dict, characters: dict) -> list[ScoredQuote]:
    quotes: list[ScoredQuote] = []
    for line in dialogue.get("lines", []):
        if not isinstance(line, Mapping) or line.get("isNonSpeaker"):
            continue
        speaker = _gold_speaker(line, characters)
        if not speaker or speaker.casefold() == "narrator":
            continue
        start, end = _line_span(line)
        quotes.append(ScoredQuote(
            line_id=int(line.get("id", -1)),
            start=start,
            end=end,
            speaker=speaker,
            text=str(line.get("text") or ""),
        ))
    return quotes


def _predicted_quotes(lines: Sequence[object], characters: dict) -> list[ScoredQuote]:
    lookup = _speaker_lookup(characters)
    quotes: list[ScoredQuote] = []
    for line in lines:
        if getattr(line, "line_type", None) != "dialogue":
            continue
        speaker = lookup.get(_normalize(getattr(line, "speaker", None)))
        quotes.append(ScoredQuote(
            line_id=int(getattr(line, "id", -1)),
            start=int(getattr(line, "span_start", -1) or -1),
            end=int(getattr(line, "span_end", -1) or -1),
            speaker=speaker,
            text=str(getattr(line, "text", "") or ""),
        ))
    return quotes


def _overlap(left: ScoredQuote, right: ScoredQuote) -> int:
    return max(0, min(left.end, right.end) - max(left.start, right.start))


def _align(gold: Sequence[ScoredQuote], predicted: Sequence[ScoredQuote]) -> list[tuple[int, int]]:
    candidates: list[tuple[float, int, int, int]] = []
    for gold_index, gold_quote in enumerate(gold):
        for predicted_index, predicted_quote in enumerate(predicted):
            overlap = _overlap(gold_quote, predicted_quote)
            minimum = min(gold_quote.end - gold_quote.start, predicted_quote.end - predicted_quote.start)
            if minimum > 0 and overlap / minimum >= 0.5:
                candidates.append((overlap / minimum, overlap, gold_index, predicted_index))
    aligned: dict[int, int] = {}
    used_predictions: set[int] = set()
    for _coverage, _overlap_size, gold_index, predicted_index in sorted(candidates, reverse=True):
        if gold_index not in aligned and predicted_index not in used_predictions:
            aligned[gold_index] = predicted_index
            used_predictions.add(predicted_index)
    return sorted(aligned.items())


def _score(gold: Sequence[ScoredQuote], predicted: Sequence[ScoredQuote]) -> tuple[Metrics, list[tuple[int, int]]]:
    matches = _align(gold, predicted)
    correct = sum(gold[gold_index].speaker == predicted[predicted_index].speaker for gold_index, predicted_index in matches)
    return Metrics(total=len(gold), correct=correct, matched=len(matches)), matches


def _serializable_line(line: object) -> dict:
    attribution = deepcopy(getattr(line, "attribution", None))
    return {
        "id": int(getattr(line, "id", -1)),
        "characterId": str(getattr(line, "speaker", None) or ""),
        "text": str(getattr(line, "text", "") or ""),
        "span": {
            "start": int(getattr(line, "span_start", -1) or -1),
            "end": int(getattr(line, "span_end", -1) or -1),
        },
        "attribution": attribution if isinstance(attribution, dict) else {},
    }


def _review_gold(
    review_root: Path,
    chapter: Path,
    gold_dialogue: dict,
    predicted_lines: Sequence[object],
    characters: dict,
) -> tuple[dict, dict]:
    chapter_root = review_root / chapter.name
    chapter_root.mkdir(parents=True, exist_ok=True)
    (chapter_root / "chapter.txt").write_bytes((chapter / "chapter.txt").read_bytes())
    predicted_rows = [_serializable_line(line) for line in predicted_lines]
    gold_rows = [line for line in gold_dialogue.get("lines", []) if isinstance(line, dict)]
    gold_scored = [ScoredQuote(int(row.get("id", -1)), *_line_span(row), str(row.get("characterId") or ""), str(row.get("text") or "")) for row in gold_rows]
    predicted_scored = [ScoredQuote(row["id"], row["span"]["start"], row["span"]["end"], row["characterId"], row["text"]) for row in predicted_rows]
    matches = _align(gold_scored, predicted_scored)
    merged = deepcopy(gold_dialogue)
    merged_rows = [line for line in merged.get("lines", []) if isinstance(line, dict)]
    for row in merged_rows:
        speaker = _gold_speaker(row, characters)
        if speaker:
            row["characterId"] = speaker
        elif not row.get("isNonSpeaker"):
            row["isNonSpeaker"] = True
    for gold_index, predicted_index in matches:
        attribution = deepcopy(predicted_rows[predicted_index].get("attribution") or {})
        merged_rows[gold_index]["attribution"] = attribution
        metadata = merged_rows[gold_index].setdefault("metadata", {})
        custom_tags = metadata.setdefault("customTags", {})
        custom_tags["attribution"] = deepcopy(attribution)
    _write_json(review_root / "characters.json", characters)
    _write_json(chapter_root / "dialogue.json", merged)
    review_result = review_chapter(str(review_root), chapter.name)
    return _load_json(review_root / "characters.json"), review_result


def _run_condition(
    book_dir: Path,
    state: ConditionState,
    parser: BookNLPParserService,
    review_root: Path,
) -> None:
    for chapter in _chapter_dirs(book_dir):
        dialogue = _load_json(chapter / "dialogue.json")
        options = {
            "parser_backend": state.backend,
            "closed_world_characters": True,
            "character_catalog": _catalog(state.characters),
        }
        lines, _names, meta = parser.parse_file(str(chapter / "chapter.txt"), options=options)
        gold = _gold_quotes(dialogue, state.characters)
        predicted = _predicted_quotes(lines, state.characters)
        chapter_metrics, _matches = _score(gold, predicted)
        state.metrics.total += chapter_metrics.total
        state.metrics.correct += chapter_metrics.correct
        state.metrics.matched += chapter_metrics.matched
        chapter_row = {
            "chapter": chapter.name,
            "total": chapter_metrics.total,
            "correct": chapter_metrics.correct,
            "matched": chapter_metrics.matched,
            "accuracy": chapter_metrics.accuracy,
            "parser_meta": meta,
        }
        if state.learn_descriptors:
            state.characters, review_result = _review_gold(
                review_root, chapter, dialogue, lines, state.characters
            )
            additions = int(review_result.get("descriptorsAdded") or 0)
            state.descriptors_added += additions
            chapter_row["descriptors_added_after_scoring"] = additions
        state.chapters.append(chapter_row)
        print(
            f"[{book_dir.name}/{state.name}] {chapter.name}: "
            f"{chapter_metrics.correct}/{chapter_metrics.total}, matched={chapter_metrics.matched}",
            flush=True,
        )


def run_book(book_dir: Path, prior_seeds: Sequence[Mapping[str, Sequence[str]]]) -> tuple[dict, dict[str, list[str]]]:
    source_characters = _load_json(book_dir / "characters.json")
    states = [
        ConditionState("legacy_current", "legacy", False, _strip_descriptors(source_characters), Metrics(), []),
        ConditionState("modern_no_descriptors", "modernbooknlp", False, _strip_descriptors(source_characters), Metrics(), []),
        ConditionState("modern_sequential", "modernbooknlp", True, _strip_descriptors(source_characters), Metrics(), []),
        ConditionState("modern_prior_sequential", "modernbooknlp", True, _merge_seed(source_characters, prior_seeds), Metrics(), []),
    ]
    parser = BookNLPParserService()
    with tempfile.TemporaryDirectory(prefix=f"openbook-{book_dir.name}-review-") as temp_dir:
        root = Path(temp_dir)
        for state in states:
            review_root = root / state.name
            review_root.mkdir()
            _run_condition(book_dir, state, parser, review_root)
    result = {
        "book": book_dir.name,
        "book_root": str(book_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "conditions": {
            state.name: {
                "dialogue_total": state.metrics.total,
                "dialogue_correct": state.metrics.correct,
                "dialogue_matched": state.metrics.matched,
                "dialogue_accuracy": state.metrics.accuracy,
                "descriptors_added": state.descriptors_added,
                "descriptor_count_final": sum(len(character.get("descriptors") or []) for character in state.characters.get("characters", [])),
                "chapters": state.chapters,
            }
            for state in states
        },
    }
    learned = _descriptor_seed(next(state.characters for state in states if state.name == "modern_sequential"))
    return result, learned


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prior_seeds: list[dict[str, list[str]]] = []
    summaries: list[dict] = []
    for raw_book_dir in args.book_dir:
        book_dir = raw_book_dir.resolve()
        cache = book_dir / ".modernbooknlp" / "manifest.json"
        if not cache.is_file():
            raise SystemExit(f"ModernBookNLP cache is required before benchmarking: {cache}")
        result, learned = run_book(book_dir, prior_seeds)
        _write_json(output_dir / f"{book_dir.name.lower()}_sequential_modernbooknlp.json", result)
        summaries.append({
            "book": result["book"],
            "conditions": {
                name: {key: value for key, value in condition.items() if key != "chapters"}
                for name, condition in result["conditions"].items()
            },
        })
        prior_seeds.append(learned)
    _write_json(output_dir / "sequential_modernbooknlp_summary.json", {"books": summaries})
    print(json.dumps({"books": summaries}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
