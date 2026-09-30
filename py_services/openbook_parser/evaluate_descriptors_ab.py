#!/usr/bin/env python3
"""Compare closed-world dialogue accuracy with learned descriptors off and on."""

import argparse
import copy
from contextlib import redirect_stdout
import io
import json
import os
import re
from typing import Dict, List, Sequence, Tuple

from .booknlp_parser_service import BookNLPParserService


def _load_json(path: str) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize(value: object) -> str:
    return str(value or "").strip().lower()


def _span(value: object) -> Tuple[int, int]:
    if isinstance(value, dict):
        return int(value.get("start") or 0), int(value.get("end") or 0)
    return 0, 0


def _overlap(left: Tuple[int, int], right: Tuple[int, int]) -> int:
    return max(0, min(left[1], right[1]) - max(left[0], right[0]))


def _prediction_for_gold(gold: Dict[str, object], predictions: Sequence[object]):
    gold_span = _span(gold.get("span"))
    best = None
    best_overlap = 0
    for prediction in predictions:
        prediction_span = (
            int(getattr(prediction, "span_start", 0) or 0),
            int(getattr(prediction, "span_end", 0) or 0),
        )
        overlap = _overlap(gold_span, prediction_span)
        if overlap > best_overlap:
            best = prediction
            best_overlap = overlap
    return best


def _name_to_id(catalog: Sequence[Dict[str, object]]) -> Dict[str, str]:
    lookup: Dict[str, str] = {"narrator": "narrator"}
    for character in catalog:
        character_id = str(character.get("id") or character.get("characterId") or "").strip()
        name = str(character.get("name") or "").strip()
        if not character_id or not name:
            continue
        for surface in [character_id, name, *(character.get("aliases") or [])]:
            key = _normalize(surface)
            if key:
                lookup[key] = character_id
    return lookup


def _parse_descriptor(value: str) -> Tuple[str, str]:
    character, separator, descriptor = value.partition("=")
    if not separator or not character.strip() or not descriptor.strip():
        raise argparse.ArgumentTypeError("descriptor must use CHARACTER=PHRASE")
    return character.strip(), descriptor.strip()


def _catalog_with_descriptors(
    raw_catalog: Sequence[Dict[str, object]],
    descriptors: Sequence[Tuple[str, str]],
    preserve_existing: bool = False,
) -> List[Dict[str, object]]:
    catalog = copy.deepcopy(list(raw_catalog))
    if not preserve_existing:
        for character in catalog:
            character["descriptors"] = []

    for character_key, descriptor in descriptors:
        target_key = _normalize(character_key)
        target = next(
            (
                character
                for character in catalog
                if target_key in {
                    _normalize(character.get("id")),
                    _normalize(character.get("characterId")),
                    _normalize(character.get("name")),
                }
            ),
            None,
        )
        if target is None:
            raise ValueError(f"Character not found: {character_key}")
        target["descriptors"].append(descriptor)
    return catalog


def _parse_chapter(
    txt_path: str,
    catalog: Sequence[Dict[str, object]],
    episode_joint_decode: bool,
):
    options = {
        "parser_backend": "legacy",
        "pov_mode": "first_person",
        "protagonists": ["Catherine"],
        "closed_world_characters": True,
        "character_catalog": list(catalog),
        "episode_joint_decode": episode_joint_decode,
        "heuristics": {"coreference": False},
    }
    with redirect_stdout(io.StringIO()):
        lines, _names, _metadata = BookNLPParserService().parse_file(
            txt_path,
            options=options,
            source_path=txt_path,
        )
    return [line for line in lines if line.line_type == "dialogue"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_root")
    parser.add_argument(
        "--descriptor",
        action="append",
        default=[],
        type=_parse_descriptor,
        help="Treatment mapping in CHARACTER=PHRASE form; repeat as needed",
    )
    parser.add_argument("--chapter-regex", default=None)
    parser.add_argument("--disable-episode-decoder", action="store_true")
    parser.add_argument(
        "--use-catalog-descriptors",
        action="store_true",
        help="Use descriptors already present in characters.json for treatment",
    )
    args = parser.parse_args()

    book_root = os.path.abspath(args.book_root)
    character_payload = _load_json(os.path.join(book_root, "characters.json"))
    raw_catalog = list(character_payload.get("characters") or [])
    baseline_catalog = _catalog_with_descriptors(raw_catalog, [])
    treatment_catalog = _catalog_with_descriptors(
        raw_catalog,
        args.descriptor,
        preserve_existing=args.use_catalog_descriptors,
    )
    name_to_id = _name_to_id(raw_catalog)
    matcher = re.compile(args.chapter_regex) if args.chapter_regex else None

    total = baseline_correct = treatment_correct = unmatched = 0
    changes: List[Dict[str, object]] = []
    parse_errors: List[str] = []

    for chapter_name in sorted(os.listdir(book_root)):
        chapter_dir = os.path.join(book_root, chapter_name)
        txt_path = os.path.join(chapter_dir, "chapter.txt")
        dialogue_path = os.path.join(chapter_dir, "dialogue.json")
        if not os.path.isdir(chapter_dir) or not os.path.exists(txt_path) or not os.path.exists(dialogue_path):
            continue
        if matcher and not matcher.search(chapter_name):
            continue

        try:
            episode_joint_decode = not args.disable_episode_decoder
            baseline_lines = _parse_chapter(txt_path, baseline_catalog, episode_joint_decode)
            treatment_lines = _parse_chapter(txt_path, treatment_catalog, episode_joint_decode)
        except (OSError, RuntimeError, ValueError) as error:
            parse_errors.append(f"{chapter_name}: {error}")
            continue

        gold_lines = list(_load_json(dialogue_path).get("lines") or [])
        for gold in gold_lines:
            gold_id = _normalize(gold.get("characterId"))
            if not gold_id or gold_id == "narrator":
                continue
            total += 1
            baseline = _prediction_for_gold(gold, baseline_lines)
            treatment = _prediction_for_gold(gold, treatment_lines)
            if baseline is None or treatment is None:
                unmatched += 1
                continue

            baseline_id = name_to_id.get(_normalize(baseline.speaker), _normalize(baseline.speaker))
            treatment_id = name_to_id.get(_normalize(treatment.speaker), _normalize(treatment.speaker))
            baseline_correct += int(baseline_id == gold_id)
            treatment_correct += int(treatment_id == gold_id)
            if baseline_id == treatment_id:
                continue

            attribution = treatment.attribution if isinstance(treatment.attribution, dict) else {}
            trace = attribution.get("decisionTrace") if isinstance(attribution.get("decisionTrace"), dict) else {}
            changes.append({
                "chapter": chapter_name,
                "span": gold.get("span"),
                "text": str(gold.get("text") or "")[:160],
                "gold": gold_id,
                "baseline": baseline_id,
                "treatment": treatment_id,
                "result": "improved" if treatment_id == gold_id else "regressed" if baseline_id == gold_id else "changed",
                "isSuggestion": bool(treatment.is_suggestion),
                "reasons": trace.get("selectedReasons") or [],
            })

    baseline_accuracy = baseline_correct / total if total else 0.0
    treatment_accuracy = treatment_correct / total if total else 0.0
    result = {
        "dialogueTotal": total,
        "baseline": {"correct": baseline_correct, "accuracy": baseline_accuracy},
        "treatment": {"correct": treatment_correct, "accuracy": treatment_accuracy},
        "delta": treatment_accuracy - baseline_accuracy,
        "changedPredictions": len(changes),
        "improvements": sum(change["result"] == "improved" for change in changes),
        "regressions": sum(change["result"] == "regressed" for change in changes),
        "otherChanges": sum(change["result"] == "changed" for change in changes),
        "unmatchedGoldLines": unmatched,
        "parseErrors": parse_errors,
        "changes": changes,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()