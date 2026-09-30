#!/usr/bin/env python3

"""Evaluate parser-only and parser-plus-AI dialogue attribution against curated dialogue.json."""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from api_models import (  # noqa: E402
    Attribution,
    AttributionCandidate,
    Candidate,
    DialogueAiAssistRequest,
    DialogueAiAssistResponse,
    DialogueAiAssistSummary,
    DialogueAiLineResult,
    DialogueJson,
    DialogueLine as ApiDialogueLine,
    LocalDialogueAiRequest,
    Metadata,
    Span,
    Stats,
)
from dialogue_ai_service import DialogueAiAssistService  # noqa: E402
from openbook_parser.booknlp_parser_service import BookNLPParserService  # noqa: E402
from openbook_parser.dialogue_parser_service import (  # noqa: E402
    DEFAULT_BLOCKED_SPEAKERS,
    DialogueParserService,
    build_paragraph_ranges,
    find_paragraph_index_for_offset,
)


UNKNOWN_SPEAKERS = {"unknown", "unassigned"}


@dataclass
class StageScore:
    label: str
    dialogue_total: int
    dialogue_correct: int
    mismatches: List[Dict[str, object]]

    @property
    def dialogue_accuracy(self) -> float:
        return (self.dialogue_correct / self.dialogue_total) if self.dialogue_total else 0.0


@dataclass
class ChapterEvaluation:
    chapter: str
    parser_only: StageScore
    ai_assisted: StageScore
    ai_auto_apply: StageScore


def _load_json(path: str) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _normalize_key(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _slugify_character_id(value: Optional[str]) -> str:
    text = _normalize_key(value)
    if not text:
        return "narrator"
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "narrator"


def _load_character_lookup(book_root: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    characters_path = os.path.join(book_root, "characters.json")
    name_lookup: Dict[str, str] = {"narrator": "narrator"}
    id_to_name: Dict[str, str] = {"narrator": "Narrator"}
    if not os.path.exists(characters_path):
        return name_lookup, id_to_name

    payload = _load_json(characters_path)
    for entry in payload.get("characters", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        character_id = str(entry.get("id") or _slugify_character_id(name)).strip()
        id_to_name[character_id] = name

        for candidate in [name, character_id, *(entry.get("aliases") or [])]:
            normalized = _normalize_key(candidate)
            if normalized:
                name_lookup[normalized] = character_id

    return name_lookup, id_to_name


def _resolve_character_id(raw_value: Optional[str], name_lookup: Dict[str, str]) -> Optional[str]:
    normalized = _normalize_key(raw_value)
    if not normalized:
        return None
    if normalized in DEFAULT_BLOCKED_SPEAKERS or normalized in UNKNOWN_SPEAKERS:
        return None
    if normalized.startswith("[") and normalized.endswith("]"):
        return None
    return name_lookup.get(normalized) or _slugify_character_id(raw_value)


def _build_attribution_candidates(line, name_lookup: Dict[str, str]) -> List[AttributionCandidate]:
    attribution = getattr(line, "attribution", None)
    if isinstance(attribution, dict) and isinstance(attribution.get("candidates"), list):
        rows = []
        for item in attribution["candidates"]:
            name = str((item or {}).get("name") or "").strip()
            if not name:
                continue
            rows.append(
                AttributionCandidate(
                    characterId=_resolve_character_id(name, name_lookup),
                    name=name,
                    confidence=float((item or {}).get("confidence") or 0.0),
                    reasons=list((item or {}).get("reasons") or []),
                )
            )
        if rows:
            return rows

    rows = []
    suggestions = list(getattr(line, "suggestions", []) or [])
    for index, suggestion in enumerate(suggestions):
        name = str(suggestion or "").strip()
        if not name:
            continue
        rows.append(
            AttributionCandidate(
                characterId=_resolve_character_id(name, name_lookup),
                name=name,
                confidence=max(0.15, 1.0 - (index * 0.1)),
            )
        )
    return rows


def _compute_returning_flags(parser_lines, raw_text: str) -> List[bool]:
    paragraph_ranges = build_paragraph_ranges(raw_text)
    paragraph_indexes: List[int] = []
    paragraphs_with_dialogue = set()
    last_entry_by_paragraph: Dict[int, int] = {}

    for index, line in enumerate(parser_lines):
        span_start = getattr(line, "span_start", None)
        paragraph_index = find_paragraph_index_for_offset(paragraph_ranges, span_start)
        paragraph_indexes.append(paragraph_index)
        if paragraph_index >= 0:
            last_entry_by_paragraph[paragraph_index] = index
        if paragraph_index >= 0 and getattr(line, "line_type", None) == "dialogue":
            paragraphs_with_dialogue.add(paragraph_index)

    flags: List[bool] = []
    for index, paragraph_index in enumerate(paragraph_indexes):
        flags.append(
            paragraph_index >= 0
            and paragraph_index in paragraphs_with_dialogue
            and last_entry_by_paragraph.get(paragraph_index) == index
        )
    return flags


def _build_dialogue_json(
    chapter_name: str,
    raw_text: str,
    parser_lines,
    name_lookup: Dict[str, str],
) -> DialogueJson:
    returning_flags = _compute_returning_flags(parser_lines, raw_text)
    lines: List[ApiDialogueLine] = []
    character_breakdown: Dict[str, int] = defaultdict(int)
    conflict_count = 0

    for index, parser_line in enumerate(parser_lines):
        speaker_name = str(getattr(parser_line, "speaker", "") or "").strip()
        is_narration = getattr(parser_line, "line_type", None) != "dialogue"
        current_character_id = "narrator" if is_narration else _resolve_character_id(speaker_name, name_lookup)
        candidate_rows = _build_attribution_candidates(parser_line, name_lookup)
        line_candidates = [
            Candidate(characterId=candidate.characterId, confidence=candidate.confidence)
            for candidate in candidate_rows
            if candidate.characterId
        ]

        top_confidence = candidate_rows[0].confidence if candidate_rows else (1.0 if current_character_id else 0.0)
        second_confidence = candidate_rows[1].confidence if len(candidate_rows) > 1 else 0.0
        margin = max(0.0, top_confidence - second_confidence)
        is_conflict = not is_narration and current_character_id is None
        if is_conflict:
            conflict_count += 1

        if current_character_id:
            character_breakdown[current_character_id] += 1

        attribution_source = getattr(parser_line, "attribution", None)
        lines.append(
            ApiDialogueLine(
                id=index + 1,
                characterId=current_character_id,
                text=parser_line.text,
                span=Span(
                    start=int(getattr(parser_line, "span_start", 0) or 0),
                    end=int(getattr(parser_line, "span_end", 0) or 0),
                ),
                metadata=Metadata(
                    emotion=None,
                    intensity=1.0,
                    pacing=None,
                    prefix=None,
                    customTags={
                        "sourceAlias": speaker_name or None,
                        "sourceCandidates": [candidate.name for candidate in candidate_rows],
                    },
                ),
                candidates=line_candidates,
                isConflict=is_conflict,
                isReturning=returning_flags[index],
                attribution=Attribution(
                    confidence=top_confidence,
                    topCandidateConfidence=top_confidence,
                    marginToSecond=margin,
                    misattributionRisk=max(0.0, 1.0 - top_confidence),
                    resolutionStatus="unknown" if is_conflict else "auto",
                    thresholdUsed=0.62,
                    sourceAlias=speaker_name or None,
                    sourceCandidates=[candidate.name for candidate in candidate_rows],
                    sourceDescriptors=list((attribution_source or {}).get("sourceDescriptors") or []) if isinstance(attribution_source, dict) else None,
                    contextGender=(attribution_source or {}).get("contextGender") if isinstance(attribution_source, dict) else None,
                    contextGenderCue=(attribution_source or {}).get("contextGenderCue") if isinstance(attribution_source, dict) else None,
                    genderConflict=(attribution_source or {}).get("genderConflict") if isinstance(attribution_source, dict) else False,
                    parserBackend=(attribution_source or {}).get("parserBackend") if isinstance(attribution_source, dict) else None,
                    decisionTrace=(attribution_source or {}).get("decisionTrace") if isinstance(attribution_source, dict) else None,
                    candidates=candidate_rows,
                ),
            )
        )

    return DialogueJson(
        formatVersion="2.0",
        chapterId=chapter_name,
        lines=lines,
        stats=Stats(
            totalLines=len(lines),
            conflicts=conflict_count,
            characterBreakdown=dict(character_breakdown),
        ),
    )


def _normalize_gold_character_id(value: Optional[str]) -> str:
    normalized = _normalize_key(value)
    return normalized or "narrator"


def _predicted_id_for_result(result, fallback: Optional[str]) -> Optional[str]:
    if result.suggestedCharacterId:
        return _normalize_gold_character_id(result.suggestedCharacterId)
    if result.aliasToAdd and result.aliasToAdd.characterId:
        return _normalize_gold_character_id(result.aliasToAdd.characterId)
    return fallback


def _extract_span_tuple(span_value) -> Tuple[int, int]:
    if span_value is None:
        return (0, 0)
    if isinstance(span_value, dict):
        return (int(span_value.get("start") or 0), int(span_value.get("end") or 0))
    return (int(getattr(span_value, "start", 0) or 0), int(getattr(span_value, "end", 0) or 0))


def _overlap_size(left_span: Tuple[int, int], right_span: Tuple[int, int]) -> int:
    return max(0, min(left_span[1], right_span[1]) - max(left_span[0], right_span[0]))


def _find_best_matching_prediction(gold_line: Dict[str, object], predicted_lines: Sequence[ApiDialogueLine]) -> Optional[ApiDialogueLine]:
    gold_span = _extract_span_tuple(gold_line.get("span"))
    gold_text = str(gold_line.get("text") or "").strip()
    best_line: Optional[ApiDialogueLine] = None
    best_overlap = -1
    best_exact_text = False

    for predicted_line in predicted_lines:
        predicted_span = _extract_span_tuple(predicted_line.span)
        overlap = _overlap_size(gold_span, predicted_span)
        if overlap <= 0:
            continue
        predicted_text = str(predicted_line.text or "").strip()
        exact_text = bool(gold_text and predicted_text and gold_text == predicted_text)
        if overlap > best_overlap or (overlap == best_overlap and exact_text and not best_exact_text):
            best_line = predicted_line
            best_overlap = overlap
            best_exact_text = exact_text

    return best_line


def _score_predictions(
    label: str,
    gold_lines: Sequence[Dict[str, object]],
    predicted_lines: Sequence[ApiDialogueLine],
    max_mismatches: int,
) -> StageScore:
    dialogue_total = 0
    dialogue_correct = 0
    mismatches: List[Dict[str, object]] = []

    for index, gold_line in enumerate(gold_lines):
        gold_character_id = _normalize_gold_character_id(gold_line.get("characterId"))
        if gold_character_id == "narrator":
            continue

        dialogue_total += 1
        predicted_line = _find_best_matching_prediction(gold_line, predicted_lines)
        predicted_character_id = _normalize_gold_character_id(predicted_line.characterId) if predicted_line else "<missing>"
        if predicted_character_id == gold_character_id:
            dialogue_correct += 1
            continue

        if len(mismatches) < max_mismatches:
            mismatches.append(
                {
                    "index": index + 1,
                    "gold": gold_character_id,
                    "pred": predicted_character_id,
                    "text": str(gold_line.get("text", ""))[:180],
                }
            )

    return StageScore(
        label=label,
        dialogue_total=dialogue_total,
        dialogue_correct=dialogue_correct,
        mismatches=mismatches,
    )


def _iter_chapter_dirs(book_root: str, chapter_regex: Optional[str], limit: int) -> List[Tuple[str, str, str, str]]:
    matcher = re.compile(chapter_regex) if chapter_regex else None
    items: List[Tuple[str, str, str, str]] = []
    for name in sorted(os.listdir(book_root)):
        chapter_dir = os.path.join(book_root, name)
        if not os.path.isdir(chapter_dir):
            continue
        if matcher and not matcher.search(name):
            continue
        txt_path = os.path.join(chapter_dir, "chapter.txt")
        dialogue_path = os.path.join(chapter_dir, "dialogue.json")
        if os.path.exists(txt_path) and os.path.exists(dialogue_path):
            items.append((name, chapter_dir, txt_path, dialogue_path))
    return items[:limit] if limit > 0 else items


def _load_parser(backend: str):
    if backend == "booknlp":
        return BookNLPParserService()
    return DialogueParserService()


def _build_service(provider: str, get_book_root: Callable[[], str]):
    if provider == "gemini":
        # Already imported
        return DialogueAiAssistService(get_book_root=get_book_root)
    if provider == "local":
        from local_dialogue_ai_service import LocalDialogueAiService

        return LocalDialogueAiService(get_book_root=get_book_root)
    if provider == "jev":
        from jev_service import JevService

        return JevService(get_book_root=get_book_root)
    raise ValueError(f"Unknown provider: {provider}")


def _adapt_local_response(response: Any, auto_apply_threshold: float) -> DialogueAiAssistResponse:
    """Convert a LocalDialogueAiResponse to the DialogueAiAssistResponse format."""
    results = []
    for local_result in response.results:
        action: str = "needs_review"
        disposition: str = "review"
        confidence = 0.5  # Default confidence for unresolved

        if local_result.outcome == "keep_existing":
            action = "keep_existing"
            confidence = getattr(local_result, "confidence", None) or 1.0
        elif local_result.outcome == "suggestion":
            action = "reassign_existing"
            confidence = getattr(local_result, "confidence", None) or 0.95
        elif local_result.outcome == "error":
            action = "error"
            disposition = "error"

        if confidence >= auto_apply_threshold and disposition == "review":
            disposition = "auto_apply"

        results.append(
            DialogueAiLineResult(
                lineId=local_result.lineId,
                paragraphIndex=local_result.paragraphIndex,
                currentCharacterId=local_result.currentCharacterId,
                currentCharacterName=local_result.currentCharacterName,
                suggestedCharacterId=local_result.suggestedCharacterId,
                suggestedCharacterName=local_result.suggestedCharacterName,
                action=action,
                disposition=disposition,
                confidence=confidence,
                currentParagraphText=local_result.currentParagraphText,
                error=local_result.error,
            )
        )

    summary = DialogueAiAssistSummary(
        scannedLines=response.summary.scannedLines,
        autoApplyCount=sum(1 for r in results if r.disposition == "auto_apply"),
        reviewCount=sum(1 for r in results if r.disposition == "review"),
        errorCount=response.summary.errorCount,
        skippedNarratorLines=response.summary.skippedNarratorLines,
        modelCalls=response.summary.modelCalls,
        aliasReviewCount=0,
        newCharacterCount=0,
    )
    return DialogueAiAssistResponse(summary=summary, results=results, errors=response.errors)


def _evaluate_chapter(
    provider_name: str,
    ai_service: Any,
    chapter_name: str,
    chapter_dir: str,
    txt_path: str,
    dialogue_path: str,
    name_lookup: Dict[str, str],
    max_mismatches: int,
    auto_apply_threshold: float,
    parser_service,
) -> ChapterEvaluation:
    gold_payload = _load_json(dialogue_path)
    gold_lines = list(gold_payload.get("lines", []))
    raw_text = _read_text(txt_path)

    parser_lines, _character_names, _meta = parser_service.parse_file(
        txt_path,
        source_path=txt_path,
    )
    parser_dialogue = _build_dialogue_json(chapter_name, raw_text, parser_lines, name_lookup)

    parser_score = _score_predictions(
        label="parser_only",
        gold_lines=gold_lines,
        predicted_lines=parser_dialogue.lines,
        max_mismatches=max_mismatches,
    )

    if provider_name == "gemini":
        ai_response = ai_service.run(
            DialogueAiAssistRequest(
                chapter_path=chapter_dir,
                chapter_text=raw_text,
                dialogue=parser_dialogue,
                auto_apply_threshold=auto_apply_threshold,
                dry_run=False,
            )
        )
    else:  # local or jev
        local_response = ai_service.run(
            LocalDialogueAiRequest(
                chapter_path=chapter_dir,
                chapter_text=raw_text,
                dialogue=parser_dialogue,
                context_window=3000,
                include_speaker_context=True,
                include_previous_speaker=True,
            )
        )
        ai_response = _adapt_local_response(local_response, auto_apply_threshold)


    ai_assisted_lines = [line.model_copy(deep=True) for line in parser_dialogue.lines]
    ai_auto_apply_lines = [line.model_copy(deep=True) for line in parser_dialogue.lines]
    line_index_by_id = {line.id: index for index, line in enumerate(parser_dialogue.lines)}

    for result in ai_response.results:
        line_index = line_index_by_id.get(result.lineId)
        if line_index is None:
            continue
        fallback_prediction = ai_assisted_lines[line_index].characterId
        suggested_prediction = _predicted_id_for_result(result, fallback_prediction)

        if result.action in {"keep_existing", "reassign_existing", "propose_alias"}:
            ai_assisted_lines[line_index].characterId = suggested_prediction
        if result.disposition == "auto_apply" and result.action in {"keep_existing", "reassign_existing"}:
            ai_auto_apply_lines[line_index].characterId = suggested_prediction

    ai_assisted_score = _score_predictions(
        label="ai_assisted",
        gold_lines=gold_lines,
        predicted_lines=ai_assisted_lines,
        max_mismatches=max_mismatches,
    )
    ai_auto_apply_score = _score_predictions(
        label="ai_auto_apply",
        gold_lines=gold_lines,
        predicted_lines=ai_auto_apply_lines,
        max_mismatches=max_mismatches,
    )

    return ChapterEvaluation(
        chapter=chapter_name,
        parser_only=parser_score,
        ai_assisted=ai_assisted_score,
        ai_auto_apply=ai_auto_apply_score,
    )


def _write_csv(path: str, chapter_results: Sequence[ChapterEvaluation]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "chapter",
                "dialogue_total",
                "parser_correct",
                "parser_accuracy",
                "ai_assisted_correct",
                "ai_assisted_accuracy",
                "ai_auto_apply_correct",
                "ai_auto_apply_accuracy",
                "ai_assisted_delta",
                "ai_auto_apply_delta",
            ]
        )
        for result in chapter_results:
            writer.writerow(
                [
                    result.chapter,
                    result.parser_only.dialogue_total,
                    result.parser_only.dialogue_correct,
                    f"{result.parser_only.dialogue_accuracy:.6f}",
                    result.ai_assisted.dialogue_correct,
                    f"{result.ai_assisted.dialogue_accuracy:.6f}",
                    result.ai_auto_apply.dialogue_correct,
                    f"{result.ai_auto_apply.dialogue_accuracy:.6f}",
                    f"{result.ai_assisted.dialogue_accuracy - result.parser_only.dialogue_accuracy:+.6f}",
                    f"{result.ai_auto_apply.dialogue_accuracy - result.parser_only.dialogue_accuracy:+.6f}",
                ]
            )


def _build_summary(
    book_root: str,
    backend: str,
    chapter_results: Sequence[ChapterEvaluation],
    auto_apply_threshold: float,
) -> Dict[str, object]:
    parser_total = sum(item.parser_only.dialogue_total for item in chapter_results)
    parser_correct = sum(item.parser_only.dialogue_correct for item in chapter_results)
    ai_total = sum(item.ai_assisted.dialogue_total for item in chapter_results)
    ai_correct = sum(item.ai_assisted.dialogue_correct for item in chapter_results)
    auto_total = sum(item.ai_auto_apply.dialogue_total for item in chapter_results)
    auto_correct = sum(item.ai_auto_apply.dialogue_correct for item in chapter_results)

    def accuracy(correct: int, total: int) -> float:
        return (correct / total) if total else 0.0

    return {
        "backend": backend,
        "book_root": book_root,
        "auto_apply_threshold": auto_apply_threshold,
        "parser_only": {
            "dialogue_total": parser_total,
            "dialogue_correct": parser_correct,
            "dialogue_accuracy": accuracy(parser_correct, parser_total),
        },
        "ai_assisted": {
            "dialogue_total": ai_total,
            "dialogue_correct": ai_correct,
            "dialogue_accuracy": accuracy(ai_correct, ai_total),
        },
        "ai_auto_apply": {
            "dialogue_total": auto_total,
            "dialogue_correct": auto_correct,
            "dialogue_accuracy": accuracy(auto_correct, auto_total),
        },
        "chapters": {
            result.chapter: {
                "parser_only": {
                    "dialogue_total": result.parser_only.dialogue_total,
                    "dialogue_correct": result.parser_only.dialogue_correct,
                    "dialogue_accuracy": result.parser_only.dialogue_accuracy,
                    "mismatches": result.parser_only.mismatches,
                },
                "ai_assisted": {
                    "dialogue_total": result.ai_assisted.dialogue_total,
                    "dialogue_correct": result.ai_assisted.dialogue_correct,
                    "dialogue_accuracy": result.ai_assisted.dialogue_accuracy,
                    "mismatches": result.ai_assisted.mismatches,
                },
                "ai_auto_apply": {
                    "dialogue_total": result.ai_auto_apply.dialogue_total,
                    "dialogue_correct": result.ai_auto_apply.dialogue_correct,
                    "dialogue_accuracy": result.ai_auto_apply.dialogue_accuracy,
                    "mismatches": result.ai_auto_apply.mismatches,
                },
            }
            for result in chapter_results
        },
    }


def _print_summary(summary: Dict[str, object]) -> None:
    parser_row = summary["parser_only"]
    ai_row = summary["ai_assisted"]
    auto_row = summary["ai_auto_apply"]
    print("=== Dialogue AI evaluation ===")
    print(f"Backend: {summary['backend']}")
    print(f"Book root: {summary['book_root']}")
    print(
        f"Parser only: {parser_row['dialogue_accuracy']:.4f} "
        f"({parser_row['dialogue_correct']}/{parser_row['dialogue_total']})"
    )
    print(
        f"AI assisted: {ai_row['dialogue_accuracy']:.4f} "
        f"({ai_row['dialogue_correct']}/{ai_row['dialogue_total']})"
    )
    print(
        f"AI auto-apply: {auto_row['dialogue_accuracy']:.4f} "
        f"({auto_row['dialogue_correct']}/{auto_row['dialogue_total']})"
    )

    for chapter_name in sorted(summary["chapters"].keys()):
        chapter_row = summary["chapters"][chapter_name]
        parser_acc = chapter_row["parser_only"]["dialogue_accuracy"]
        ai_acc = chapter_row["ai_assisted"]["dialogue_accuracy"]
        auto_acc = chapter_row["ai_auto_apply"]["dialogue_accuracy"]
        safe_name = chapter_name.encode('ascii', 'replace').decode()
        print(
            f"- {safe_name}: parser={parser_acc:.4f} ai={ai_acc:.4f} auto={auto_acc:.4f}"
        )


def _compare_to_baseline(summary: Dict[str, object], baseline_path: str) -> Tuple[float, List[str]]:
    baseline = _load_json(baseline_path)
    current_ai = float(summary["ai_assisted"]["dialogue_accuracy"])
    baseline_ai = float(baseline["ai_assisted"]["dialogue_accuracy"])
    current_parser = float(summary["parser_only"]["dialogue_accuracy"])
    baseline_parser = float(baseline["parser_only"]["dialogue_accuracy"])
    current_auto = float(summary["ai_auto_apply"]["dialogue_accuracy"])
    baseline_auto = float(baseline["ai_auto_apply"]["dialogue_accuracy"])

    notes = [
        f"AI assisted delta: {current_ai - baseline_ai:+.4f} (current={current_ai:.4f}, baseline={baseline_ai:.4f})",
        f"Parser-only delta: {current_parser - baseline_parser:+.4f} (current={current_parser:.4f}, baseline={baseline_parser:.4f})",
        f"AI auto-apply delta: {current_auto - baseline_auto:+.4f} (current={current_auto:.4f}, baseline={baseline_auto:.4f})",
    ]
    return current_ai - baseline_ai, notes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate parser-only and parser-plus-AI dialogue attribution against curated dialogue.json."
    )
    parser.add_argument("book_root", help="Path to the book directory containing curated chapter folders")
    parser.add_argument(
        "--backend",
        choices=["booknlp", "legacy"],
        default="booknlp",
        help="Parser backend to evaluate (default: booknlp)",
    )
    parser.add_argument(
        "--provider",
        choices=["gemini", "local", "jev"],
        default="gemini",
        help="The dialogue AI provider to use for evaluation.",
    )
    parser.add_argument("--chapter-regex", default=None, help="Regex filter applied to chapter directory names")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate only the first N matching chapters")
    parser.add_argument("--max-mismatches-per-chapter", type=int, default=5)
    parser.add_argument("--auto-apply-threshold", type=float, default=0.92)
    parser.add_argument(
        "--output-csv",
        default=None,
        help="CSV output path (default: <book_root>/dialogue_ai_eval.csv)",
    )
    parser.add_argument(
        "--save-summary",
        default=None,
        help="Write summary JSON to this path for later comparison",
    )
    parser.add_argument(
        "--baseline-summary",
        default=None,
        help="Compare current summary against a saved baseline JSON",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit with code 2 when AI-assisted dialogue accuracy regresses from baseline",
    )
    args = parser.parse_args()

    book_root = os.path.abspath(args.book_root)
    output_csv = args.output_csv or os.path.join(book_root, "dialogue_ai_eval.csv")
    chapter_items = _iter_chapter_dirs(book_root, args.chapter_regex, args.limit)
    if not chapter_items:
        raise SystemExit("No chapter folders found with chapter.txt and dialogue.json")

    name_lookup, _id_to_name = _load_character_lookup(book_root)
    parser_service = _load_parser(args.backend)
    ai_service = _build_service(args.provider, lambda: book_root)

    chapter_results: List[ChapterEvaluation] = []
    for chapter_name, chapter_dir, txt_path, dialogue_path in chapter_items:
        print(f"Evaluating {chapter_name.encode('ascii', 'replace').decode()}...")
        chapter_results.append(
            _evaluate_chapter(
                provider_name=args.provider,
                ai_service=ai_service,
                chapter_name=chapter_name,
                chapter_dir=chapter_dir,
                txt_path=txt_path,
                dialogue_path=dialogue_path,
                name_lookup=name_lookup,
                max_mismatches=args.max_mismatches_per_chapter,
                auto_apply_threshold=args.auto_apply_threshold,
                parser_service=parser_service,
            )
        )

    summary = _build_summary(book_root, args.backend, chapter_results, args.auto_apply_threshold)
    _write_csv(output_csv, chapter_results)
    _print_summary(summary)
    print(f"\nCSV written: {output_csv}")

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
        print(f"Summary written: {save_path}")

    if args.fail_on_regression and args.baseline_summary and regression:
        raise SystemExit(2)


if __name__ == "__main__":
    main()