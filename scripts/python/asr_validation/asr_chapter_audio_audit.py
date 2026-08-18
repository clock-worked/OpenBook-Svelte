"""Audit chapter audio clips against dialogue and produce regenerate manifests."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

    def tqdm(iterable, **_kwargs):
        return iterable

from asr_word_timestamps import (
    collect_audio_files,
    create_pipeline,
    parse_clip_name,
    transcribe_many_with_words,
    transcribe_with_words,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def tokenize_for_compare(text: str) -> list[str]:
    return [item for item in " ".join(text.lower().split()).split(" ") if item]


def similarity_ratio(expected: str, actual: str) -> float:
    expected_tokens = " ".join(tokenize_for_compare(expected))
    actual_tokens = " ".join(tokenize_for_compare(actual))
    if not expected_tokens and not actual_tokens:
        return 1.0
    return round(SequenceMatcher(None, expected_tokens, actual_tokens, autojunk=False).ratio(), 4)


def load_dialogue_lines(dialogue_path: Path) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(dialogue_path.read_text(encoding="utf-8"))
    lines = payload.get("lines")
    if not isinstance(lines, list):
        raise ValueError("dialogue.json must include a 'lines' array")

    by_id: dict[int, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        line_id = line.get("id")
        text = line.get("text")
        if not isinstance(line_id, int):
            continue
        if not isinstance(text, str):
            text = ""
        normalized = {
            "id": line_id,
            "text": text.strip(),
            "characterId": line.get("characterId"),
        }
        by_id[line_id] = normalized
        ordered.append(normalized)

    return by_id, ordered


def top_similarity_candidates(
    transcript_text: str,
    expected_line_id: int,
    dialogue_lines: list[dict[str, Any]],
    max_candidates: int,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, int, str]] = []
    for line in dialogue_lines:
        candidate_id = int(line["id"])
        candidate_text = str(line.get("text", ""))
        score = similarity_ratio(candidate_text, transcript_text)
        scored.append((score, candidate_id, candidate_text))

    scored.sort(key=lambda item: item[0], reverse=True)
    output: list[dict[str, Any]] = []
    for score, candidate_id, candidate_text in scored[:max_candidates]:
        output.append(
            {
                "lineId": candidate_id,
                "score": score,
                "isExpected": candidate_id == expected_line_id,
                "text": candidate_text,
            }
        )
    return output


def analyze_clip(
    *,
    audio_path: Path,
    line_id: int,
    dialogue_by_id: dict[int, dict[str, Any]],
    dialogue_lines: list[dict[str, Any]],
    asr_pipeline: Any,
    chunk_length_s: int,
    batch_size: int,
    mismatch_threshold: float,
    min_wrong_line_delta: float,
    top_k: int,
) -> dict[str, Any]:
    try:
        asr_result = transcribe_with_words(
            asr_pipeline=asr_pipeline,
            audio_path=audio_path,
            chunk_length_s=chunk_length_s,
            batch_size=batch_size,
        )
    except Exception as exc:
        asr_result = {"_asr_error": str(exc), "text": ""}

    return analyze_clip_result(
        audio_path=audio_path,
        line_id=line_id,
        dialogue_by_id=dialogue_by_id,
        dialogue_lines=dialogue_lines,
        mismatch_threshold=mismatch_threshold,
        min_wrong_line_delta=min_wrong_line_delta,
        top_k=top_k,
        asr_result=asr_result,
    )


def analyze_clip_result(
    *,
    audio_path: Path,
    line_id: int,
    dialogue_by_id: dict[int, dict[str, Any]],
    dialogue_lines: list[dict[str, Any]],
    mismatch_threshold: float,
    min_wrong_line_delta: float,
    top_k: int,
    asr_result: dict[str, Any],
) -> dict[str, Any]:
    expected_line = dialogue_by_id.get(line_id)
    if expected_line is None:
        return {
            "audioPath": str(audio_path),
            "lineIdFromFile": line_id,
            "status": "line_id_not_in_dialogue",
            "reason": f"line id {line_id} not found in dialogue.json",
            "markForRegenerate": True,
        }

    asr_error = asr_result.get("_asr_error")
    if asr_error:
        return {
            "audioPath": str(audio_path),
            "audioFile": audio_path.name,
            "lineIdFromFile": line_id,
            "expected": {
                "lineId": line_id,
                "characterId": expected_line.get("characterId"),
                "text": str(expected_line.get("text", "")),
            },
            "asr": {
                "text": "",
            },
            "candidates": [],
            "status": "audio_read_error",
            "markForRegenerate": True,
            "reasons": [f"ASR failed to read audio: {asr_error}"],
            "bestNonExpected": None,
            "bestOverall": None,
        }

    transcript_text = str(asr_result.get("text", "")).strip()
    expected_text = str(expected_line.get("text", ""))
    expected_similarity = similarity_ratio(expected_text, transcript_text)

    candidates = top_similarity_candidates(
        transcript_text=transcript_text,
        expected_line_id=line_id,
        dialogue_lines=dialogue_lines,
        max_candidates=top_k,
    )

    best = candidates[0] if candidates else None
    likely_wrong_line = False
    best_non_expected = None
    for candidate in candidates:
        if not candidate["isExpected"]:
            best_non_expected = candidate
            break

    if best_non_expected is not None and (best_non_expected["score"] - expected_similarity) >= min_wrong_line_delta:
        likely_wrong_line = True

    status = "ok"
    mark_for_regenerate = False
    reasons: list[str] = []

    if expected_similarity < mismatch_threshold:
        status = "mismatch"
        mark_for_regenerate = True
        reasons.append(
            f"expected similarity {expected_similarity} is below threshold {mismatch_threshold}")

    if likely_wrong_line:
        status = "likely_wrong_line"
        mark_for_regenerate = True
        reasons.append(
            (
                "best non-expected line "
                f"{best_non_expected['lineId']} scores {best_non_expected['score']} "
                f"(delta {round(best_non_expected['score'] - expected_similarity, 4)})"
            )
        )

    if not transcript_text:
        status = "empty_transcript"
        mark_for_regenerate = True
        reasons.append("ASR returned empty transcript")

    return {
        "audioPath": str(audio_path),
        "audioFile": audio_path.name,
        "lineIdFromFile": line_id,
        "expected": {
            "lineId": line_id,
            "characterId": expected_line.get("characterId"),
            "text": expected_text,
            "similarity": expected_similarity,
        },
        "asr": {
            "text": transcript_text,
        },
        "candidates": candidates,
        "status": status,
        "markForRegenerate": mark_for_regenerate,
        "reasons": reasons,
        "bestNonExpected": best_non_expected,
        "bestOverall": best,
    }


def _float_or_default(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _line_match_score_for_target(clip: dict[str, Any], target_line_id: int) -> float:
    expected = clip.get("expected") if isinstance(
        clip.get("expected"), dict) else {}
    best = clip.get("bestOverall") if isinstance(
        clip.get("bestOverall"), dict) else {}
    transcript = clip.get("asr") if isinstance(clip.get("asr"), dict) else {}

    expected_line_id = expected.get("lineId")
    expected_similarity = _float_or_default(expected.get("similarity"), 0.0)
    best_line_id = best.get("lineId")
    best_score = _float_or_default(best.get("score"), 0.0)

    transcript_text = str(transcript.get("text", ""))
    transcript_words = len(tokenize_for_compare(transcript_text))
    expected_words = len(tokenize_for_compare(str(expected.get("text", ""))))
    fullness_bonus = 0.0
    if expected_words > 0:
        fullness_bonus = min(0.05, 0.05 * (transcript_words / expected_words))

    if expected_line_id == target_line_id:
        return expected_similarity + fullness_bonus
    if best_line_id == target_line_id:
        return best_score + fullness_bonus
    return 0.0


def apply_auto_conflict_detection(clip_reports: list[dict[str, Any]]) -> dict[str, Any]:
    line_id_to_indexes: dict[int, list[int]] = {}
    for index, clip in enumerate(clip_reports):
        line_id = clip.get("lineIdFromFile")
        if isinstance(line_id, int):
            line_id_to_indexes.setdefault(line_id, []).append(index)

    duplicate_group_count = 0
    duplicate_loser_count = 0

    for line_id, indexes in line_id_to_indexes.items():
        if len(indexes) <= 1:
            continue
        duplicate_group_count += 1

        scored = sorted(
            [
                (
                    _line_match_score_for_target(
                        clip_reports[item_index], line_id),
                    item_index,
                )
                for item_index in indexes
            ],
            key=lambda item: item[0],
            reverse=True,
        )

        keeper_index = scored[0][1]
        keeper_clip = clip_reports[keeper_index]
        keeper_audio = keeper_clip.get("audioFile")
        keeper_score = scored[0][0]

        for loser_score, loser_index in scored[1:]:
            loser_clip = clip_reports[loser_index]
            reasons = loser_clip.get("reasons") if isinstance(
                loser_clip.get("reasons"), list) else []
            reasons.append(
                (
                    "duplicate line id conflict: weaker match kept for regenerate; "
                    f"preferred {keeper_audio} (score {round(keeper_score, 4)}) over "
                    f"{loser_clip.get('audioFile')} (score {round(loser_score, 4)})"
                )
            )
            loser_clip["reasons"] = reasons
            loser_clip["status"] = "duplicate_weaker_match"
            loser_clip["markForRegenerate"] = True
            loser_clip["autoSuggestedTargetLineId"] = line_id
            duplicate_loser_count += 1

    adjacent_candidates_by_target: dict[int, list[int]] = {}
    source_to_target: dict[int, int] = {}
    for index, clip in enumerate(clip_reports):
        best = clip.get("bestOverall") if isinstance(
            clip.get("bestOverall"), dict) else {}
        expected = clip.get("expected") if isinstance(
            clip.get("expected"), dict) else {}
        source_line = expected.get("lineId")
        target_line = best.get("lineId")
        if not isinstance(source_line, int) or not isinstance(target_line, int):
            continue
        if source_line == target_line:
            continue
        if abs(target_line - source_line) != 1:
            continue

        clip["autoSuggestedTargetLineId"] = target_line
        adjacent_candidates_by_target.setdefault(target_line, []).append(index)
        source_to_target[source_line] = target_line

    adjacent_shift_conflicts = 0
    stronger_owner_rejections = 0
    for target_line, shifted_indexes in adjacent_candidates_by_target.items():
        owner_indexes = line_id_to_indexes.get(target_line, [])
        owner_best_score = 0.0
        owner_audio = None
        if owner_indexes:
            best_owner_index = max(
                owner_indexes,
                key=lambda item_index, target=target_line: _line_match_score_for_target(
                    clip_reports[item_index],
                    target,
                ),
            )
            owner_clip = clip_reports[best_owner_index]
            owner_best_score = _line_match_score_for_target(
                owner_clip, target_line)
            owner_audio = owner_clip.get("audioFile")

        shifted_scored = sorted(
            [
                (
                    _line_match_score_for_target(
                        clip_reports[item_index], target_line),
                    item_index,
                )
                for item_index in shifted_indexes
            ],
            key=lambda item: item[0],
            reverse=True,
        )

        if shifted_scored and owner_best_score >= shifted_scored[0][0] + 0.05:
            for shifted_score, shifted_index in shifted_scored:
                shifted_clip = clip_reports[shifted_index]
                reasons = shifted_clip.get("reasons") if isinstance(
                    shifted_clip.get("reasons"), list) else []
                reasons.append(
                    (
                        "adjacent line shift conflict: target line already has a stronger owner; "
                        f"target {target_line} owner {owner_audio} ({round(owner_best_score, 4)}) "
                        f"> candidate {shifted_clip.get('audioFile')} ({round(shifted_score, 4)})"
                    )
                )
                shifted_clip["reasons"] = reasons
                shifted_clip["status"] = "adjacent_shift_lower_match"
                shifted_clip["markForRegenerate"] = True
                adjacent_shift_conflicts += 1
            stronger_owner_rejections += len(shifted_scored)
            continue

        if len(shifted_scored) <= 1:
            continue

        winner_score, winner_index = shifted_scored[0]
        winner_audio = clip_reports[winner_index].get("audioFile")
        for loser_score, loser_index in shifted_scored[1:]:
            loser_clip = clip_reports[loser_index]
            reasons = loser_clip.get("reasons") if isinstance(
                loser_clip.get("reasons"), list) else []
            reasons.append(
                (
                    "multiple shifted clips target the same line; keeping stronger candidate "
                    f"{winner_audio} ({round(winner_score, 4)}) over "
                    f"{loser_clip.get('audioFile')} ({round(loser_score, 4)})"
                )
            )
            loser_clip["reasons"] = reasons
            loser_clip["status"] = "adjacent_shift_lower_match"
            loser_clip["markForRegenerate"] = True
            adjacent_shift_conflicts += 1

    shift_chain_count = 0
    for source_line, target_line in source_to_target.items():
        if target_line != source_line - 1:
            continue
        next_source = source_line + 1
        if source_to_target.get(next_source) != source_line:
            continue
        shift_chain_count += 1
        for clip in clip_reports:
            expected = clip.get("expected") if isinstance(
                clip.get("expected"), dict) else {}
            expected_line_id = expected.get("lineId")
            if expected_line_id not in {source_line, next_source}:
                continue
            reasons = clip.get("reasons") if isinstance(
                clip.get("reasons"), list) else []
            reasons.append(
                (
                    "detected adjacent shift chain pattern: "
                    f"{source_line}->{target_line} and {next_source}->{source_line}"
                )
            )
            clip["reasons"] = reasons
            clip["autoDetectedShiftChain"] = True

    return {
        "duplicateLineIdGroups": duplicate_group_count,
        "duplicateWeakerMatches": duplicate_loser_count,
        "adjacentShiftLowerMatches": adjacent_shift_conflicts,
        "adjacentShiftRejectedByOwner": stronger_owner_rejections,
        "adjacentShiftChains": shift_chain_count,
    }


def discover_chapter_dirs(book_dir: Path, chapter_regex: str | None) -> list[Path]:
    pattern = re.compile(chapter_regex) if chapter_regex else None
    chapter_dirs: list[Path] = []
    for candidate in sorted(book_dir.iterdir()):
        if not candidate.is_dir():
            continue
        if not (candidate / "dialogue.json").exists():
            continue
        if not (candidate / "audio_lines").exists():
            continue
        if pattern and not pattern.search(candidate.name):
            continue
        chapter_dirs.append(candidate.resolve())
    return chapter_dirs


def is_direct_character_line_audio(audio_path: Path, audio_dir: Path) -> bool:
    """Return True only for direct `audio_lines/<Character>/<line>-<Character>.*` clips."""
    try:
        rel = audio_path.resolve().relative_to(audio_dir.resolve())
    except ValueError:
        return False

    if any(part in {"_chunk_pipeline", "generation_chunks"} for part in rel.parts):
        return False

    if len(rel.parts) != 2:
        return False

    parent_dir = rel.parts[0]
    if parent_dir.startswith("_"):
        return False

    return True


def collect_chapter_audit_inputs(chapter_dir: Path) -> dict[str, Any]:
    dialogue_path = (chapter_dir / "dialogue.json").resolve()
    audio_dir = (chapter_dir / "audio_lines").resolve()

    dialogue_by_id, dialogue_lines = load_dialogue_lines(dialogue_path)
    raw_audio_files = collect_audio_files([], audio_dir)
    audio_files = [
        item for item in raw_audio_files if is_direct_character_line_audio(item, audio_dir)
    ]
    excluded_audio_files = [
        str(item) for item in raw_audio_files if not is_direct_character_line_audio(item, audio_dir)
    ]

    parsed_audio: list[tuple[Path, int]] = []
    unparsable_files: list[str] = []
    for audio_path in audio_files:
        line_id, _character = parse_clip_name(audio_path)
        if isinstance(line_id, int):
            parsed_audio.append((audio_path, line_id))
        else:
            unparsable_files.append(str(audio_path))

    present_ids = {line_id for _, line_id in parsed_audio}
    missing_line_ids = sorted(
        [line_id for line_id in dialogue_by_id.keys() if line_id not in present_ids]
    )

    return {
        "dialoguePath": dialogue_path,
        "audioDir": audio_dir,
        "dialogueById": dialogue_by_id,
        "dialogueLines": dialogue_lines,
        "rawAudioFiles": raw_audio_files,
        "audioFiles": audio_files,
        "excludedAudioFiles": excluded_audio_files,
        "parsedAudio": parsed_audio,
        "unparseableFiles": unparsable_files,
        "missingLineIds": missing_line_ids,
    }


def run_chapter_audit(
    *,
    chapter_dir: Path,
    output_dir: Path,
    model: str,
    device: str,
    torch_dtype: str,
    chunk_length_s: int,
    batch_size: int,
    mismatch_threshold: float,
    min_wrong_line_delta: float,
    top_k: int,
    dry_run: bool,
    asr_pipeline: Any,
    chapter_inputs: dict[str, Any] | None = None,
    precomputed_asr_by_audio_path: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    audit_inputs = chapter_inputs or collect_chapter_audit_inputs(chapter_dir)

    dialogue_path = audit_inputs["dialoguePath"]
    audio_dir = audit_inputs["audioDir"]
    dialogue_by_id = audit_inputs["dialogueById"]
    dialogue_lines = audit_inputs["dialogueLines"]
    raw_audio_files = audit_inputs["rawAudioFiles"]
    audio_files = audit_inputs["audioFiles"]
    excluded_audio_files = audit_inputs["excludedAudioFiles"]
    parsed_audio = audit_inputs["parsedAudio"]
    unparsable_files = audit_inputs["unparseableFiles"]
    missing_line_ids = audit_inputs["missingLineIds"]

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{chapter_dir.name}] dialogue lines: {len(dialogue_by_id)}")
    print(f"[{chapter_dir.name}] audio clips discovered: {len(raw_audio_files)}")
    print(f"[{chapter_dir.name}] direct character line clips: {len(audio_files)}")
    print(f"[{chapter_dir.name}] excluded non-line clips: {len(excluded_audio_files)}")
    print(f"[{chapter_dir.name}] parseable line id clips: {len(parsed_audio)}")
    print(f"[{chapter_dir.name}] missing clips by line id: {len(missing_line_ids)}")

    if dry_run:
        regenerate_reasons = []
        for line_id in missing_line_ids:
            expected = dialogue_by_id.get(line_id, {})
            regenerate_reasons.append(
                {
                    "lineId": line_id,
                    "audioPath": None,
                    "status": "missing_audio",
                    "reasons": ["No audio clip found for dialogue line id"],
                    "expectedText": expected.get("text"),
                    "characterId": expected.get("characterId"),
                }
            )

        dry_run_payload = {
            "createdAt": utc_now_iso(),
            "chapterDir": str(chapter_dir),
            "dialoguePath": str(dialogue_path),
            "audioDir": str(audio_dir),
            "model": model,
            "counts": {
                "dialogueLines": len(dialogue_by_id),
                "audioDiscovered": len(raw_audio_files),
                "audioDirectCharacterLine": len(audio_files),
                "audioExcludedNonLine": len(excluded_audio_files),
                "audioParseable": len(parsed_audio),
                "audioUnparseable": len(unparsable_files),
                "missingAudio": len(missing_line_ids),
                "markedForRegenerate": len(regenerate_reasons),
            },
            "missingLineIds": missing_line_ids,
            "excludedNonLineAudioFiles": excluded_audio_files,
            "unparseableAudioFiles": unparsable_files,
            "regenerate": regenerate_reasons,
        }

        output_path = output_dir / "chapter_asr_audit.json"
        regenerate_path = output_dir / "regenerate_manifest.json"
        regenerate_text_path = output_dir / "regenerate_line_ids.txt"

        output_path.write_text(json.dumps(
            dry_run_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        regenerate_path.write_text(
            json.dumps({"regenerate": regenerate_reasons},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        regenerate_text_path.write_text(
            "\n".join(str(line_id) for line_id in missing_line_ids),
            encoding="utf-8",
        )

        print(f"[{chapter_dir.name}] dry-run report written: {output_path}")
        print(f"[{chapter_dir.name}] dry-run regenerate manifest: {regenerate_path}")
        print(
            f"[{chapter_dir.name}] dry-run regenerate line ids: {regenerate_text_path}")
        return dry_run_payload

    clip_reports: list[dict[str, Any]] = []

    if precomputed_asr_by_audio_path is None:
        print(f"[{chapter_dir.name}] Transcribing {len(parsed_audio)} clips with pipeline batch size {batch_size}")
        all_asr_results = transcribe_many_with_words(
            asr_pipeline=asr_pipeline,
            audio_paths=[audio_path for audio_path, _line_id in parsed_audio],
            chunk_length_s=chunk_length_s,
            batch_size=batch_size,
        )
    else:
        print(f"[{chapter_dir.name}] Reusing precomputed ASR for {len(parsed_audio)} clips")
        all_asr_results = [
            precomputed_asr_by_audio_path.get(
                str(audio_path),
                {"_asr_error": "Missing precomputed ASR result", "text": ""},
            )
            for audio_path, _line_id in parsed_audio
        ]

    for (audio_path, line_id), asr_result in zip(parsed_audio, all_asr_results):
        clip_reports.append(
            analyze_clip_result(
                audio_path=audio_path,
                line_id=line_id,
                dialogue_by_id=dialogue_by_id,
                dialogue_lines=dialogue_lines,
                mismatch_threshold=mismatch_threshold,
                min_wrong_line_delta=min_wrong_line_delta,
                top_k=top_k,
                asr_result=asr_result,
            )
        )

    auto_detection = apply_auto_conflict_detection(clip_reports)

    regenerate_reasons: list[dict[str, Any]] = []
    for clip_report in clip_reports:
        if not clip_report.get("markForRegenerate"):
            continue
        regenerate_reasons.append(
            {
                "lineId": clip_report.get("lineIdFromFile"),
                "audioPath": clip_report.get("audioPath"),
                "status": clip_report.get("status"),
                "reasons": clip_report.get("reasons", []),
                "bestNonExpected": clip_report.get("bestNonExpected"),
                "autoSuggestedTargetLineId": clip_report.get("autoSuggestedTargetLineId"),
            }
        )

    for line_id in missing_line_ids:
        expected = dialogue_by_id.get(line_id, {})
        regenerate_reasons.append(
            {
                "lineId": line_id,
                "audioPath": None,
                "status": "missing_audio",
                "reasons": ["No audio clip found for dialogue line id"],
                "expectedText": expected.get("text"),
                "characterId": expected.get("characterId"),
            }
        )

    summary = {
        "createdAt": utc_now_iso(),
        "chapterDir": str(chapter_dir),
        "dialoguePath": str(dialogue_path),
        "audioDir": str(audio_dir),
        "engine": {
            "task": "automatic-speech-recognition",
            "model": model,
            "device": device,
            "torchDtype": torch_dtype,
            "chunkLengthS": chunk_length_s,
            "batchSize": batch_size,
        },
        "thresholds": {
            "mismatchThreshold": mismatch_threshold,
            "minWrongLineDelta": min_wrong_line_delta,
        },
        "counts": {
            "dialogueLines": len(dialogue_by_id),
            "audioDiscovered": len(raw_audio_files),
            "audioDirectCharacterLine": len(audio_files),
            "audioExcludedNonLine": len(excluded_audio_files),
            "audioParseable": len(parsed_audio),
            "audioUnparseable": len(unparsable_files),
            "missingAudio": len(missing_line_ids),
            "markedForRegenerate": len(regenerate_reasons),
        },
        "autoDetection": auto_detection,
        "missingLineIds": missing_line_ids,
        "excludedNonLineAudioFiles": excluded_audio_files,
        "unparseableAudioFiles": unparsable_files,
        "clips": clip_reports,
        "regenerate": regenerate_reasons,
    }

    audit_path = output_dir / "chapter_asr_audit.json"
    regenerate_path = output_dir / "regenerate_manifest.json"
    regenerate_text_path = output_dir / "regenerate_line_ids.txt"

    audit_path.write_text(json.dumps(
        summary, indent=2, ensure_ascii=False), encoding="utf-8")
    regenerate_path.write_text(json.dumps(
        {"regenerate": regenerate_reasons}, indent=2, ensure_ascii=False), encoding="utf-8")

    unique_line_ids = sorted(
        {
            int(item["lineId"])
            for item in regenerate_reasons
            if isinstance(item.get("lineId"), int)
        }
    )
    regenerate_text_path.write_text(
        "\n".join(str(line_id) for line_id in unique_line_ids), encoding="utf-8")

    print(f"[{chapter_dir.name}] audit report: {audit_path}")
    print(f"[{chapter_dir.name}] regenerate manifest: {regenerate_path}")
    print(f"[{chapter_dir.name}] regenerate line ids: {regenerate_text_path}")

    return summary


def load_existing_asr_results(output_dir: Path) -> dict[str, dict[str, Any]]:
    audit_path = output_dir / "chapter_asr_audit.json"
    if not audit_path.exists():
        return {}

    try:
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    clips = payload.get("clips")
    if not isinstance(clips, list):
        return {}

    results: dict[str, dict[str, Any]] = {}
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        audio_path = clip.get("audioPath")
        asr_result = clip.get("asr")
        status = clip.get("status")
        if not isinstance(audio_path, str) or not isinstance(asr_result, dict):
            continue
        if status == "audio_read_error" or "text" not in asr_result:
            continue
        results[audio_path] = asr_result
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit chapter audio lines with ASR and produce regenerate lists for missing/mismatched clips."
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--chapter-dir", type=Path,
                       help="One chapter directory containing dialogue.json and audio_lines")
    scope.add_argument("--book-dir", type=Path,
                       help="Book directory containing chapter folders")
    parser.add_argument(
        "--chapter-regex",
        default=None,
        help="Optional regex to filter chapter folder names (book runs only).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for audit reports (default: chapter audio_lines/asr_audit or book/asr_audit_book)",
    )
    parser.add_argument(
        "--model",
        default="distil-whisper/distil-small.en",
        help="ASR model id or local path.",
    )
    parser.add_argument(
        "--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument(
        "--torch-dtype", choices=["float16", "bfloat16", "float32"], default="float16")
    parser.add_argument("--chunk-length-s", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument(
        "--mismatch-threshold",
        type=float,
        default=0.74,
        help="Expected line similarity below this is marked for regenerate.",
    )
    parser.add_argument(
        "--min-wrong-line-delta",
        type=float,
        default=0.08,
        help="Best non-expected similarity must exceed expected by this amount to mark likely wrong-line.",
    )
    parser.add_argument("--top-k", type=int, default=3,
                        help="How many candidate matching lines to keep in report.")
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="Reuse clip ASR results from existing chapter_asr_audit.json files and only transcribe missing clips.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate inputs and print summary without ASR inference.")
    args = parser.parse_args()

    chapter_dirs: list[Path] = []
    if args.chapter_dir:
        chapter_dir = args.chapter_dir.resolve()
        if not chapter_dir.exists() or not chapter_dir.is_dir():
            raise SystemExit(f"chapter dir not found: {chapter_dir}")
        chapter_dirs = [chapter_dir]
    elif args.book_dir:
        book_dir = args.book_dir.resolve()
        if not book_dir.exists() or not book_dir.is_dir():
            raise SystemExit(f"book dir not found: {book_dir}")
        chapter_dirs = discover_chapter_dirs(book_dir, args.chapter_regex)
        if not chapter_dirs:
            raise SystemExit(
                "No chapter folders found with dialogue.json + audio_lines")

    base_output_dir = args.output_dir.resolve() if args.output_dir else None

    existing_asr_by_audio_path: dict[str, dict[str, Any]] = {}
    if args.resume_existing and not args.dry_run:
        for chapter_dir in chapter_dirs:
            chapter_output_dir = (
                (base_output_dir / chapter_dir.name).resolve()
                if base_output_dir
                else (chapter_dir / "audio_lines" / "asr_audit").resolve()
            )
            existing_asr_by_audio_path.update(
                load_existing_asr_results(chapter_output_dir))
        print(
            f"[resume] Reusing {len(existing_asr_by_audio_path)} existing clip ASR results")

    asr_pipeline = None
    if not args.dry_run:
        asr_pipeline = create_pipeline(
            args.model, args.device, args.torch_dtype)

    chapter_inputs_by_dir: dict[str, dict[str, Any]] = {}
    precomputed_asr_by_audio_path: dict[str, dict[str, Any]] | None = None
    if not args.dry_run and len(chapter_dirs) > 1:
        flattened_audio_paths: list[Path] = []
        for chapter_dir in chapter_dirs:
            chapter_inputs = collect_chapter_audit_inputs(chapter_dir)
            chapter_inputs_by_dir[str(chapter_dir)] = chapter_inputs
            flattened_audio_paths.extend(
                    [
                        audio_path
                        for audio_path, _line_id in chapter_inputs["parsedAudio"]
                        if str(audio_path) not in existing_asr_by_audio_path
                    ]
            )

        if flattened_audio_paths:
            book_chunk_size = max(args.batch_size * 32, 128)
            book_chunk_count = (len(flattened_audio_paths) + book_chunk_size - 1) // book_chunk_size
            print(
                (
                    f"[book] Transcribing {len(flattened_audio_paths)} clips across {len(chapter_dirs)} chapters "
                    f"in {book_chunk_count} ASR batches of up to {book_chunk_size} clips "
                    f"with pipeline batch size {args.batch_size}"
                )
            )
            flattened_results: list[dict[str, Any]] = []
            book_batch_iterator = tqdm(
                range(0, len(flattened_audio_paths), book_chunk_size),
                total=book_chunk_count,
                desc="Book ASR",
                unit="batch",
                dynamic_ncols=True,
                leave=True,
            )
            for batch_index, batch_start in enumerate(book_batch_iterator, start=1):
                audio_batch = flattened_audio_paths[batch_start: batch_start + book_chunk_size]
                batch_end = batch_start + len(audio_batch)
                if HAS_TQDM:
                    book_batch_iterator.set_postfix_str(
                        f"clips {batch_start + 1}-{batch_end}/{len(flattened_audio_paths)}"
                    )
                else:
                    print(
                        (
                            f"[book] ASR batch {batch_index}/{book_chunk_count}: "
                            f"clips {batch_start + 1}-{batch_end}/{len(flattened_audio_paths)}"
                        )
                    )
                flattened_results.extend(
                    transcribe_many_with_words(
                        asr_pipeline=asr_pipeline,
                        audio_paths=audio_batch,
                        chunk_length_s=args.chunk_length_s,
                        batch_size=args.batch_size,
                    )
                )
            precomputed_asr_by_audio_path = {
                str(audio_path): result
                for audio_path, result in zip(flattened_audio_paths, flattened_results)
            }
            precomputed_asr_by_audio_path.update(existing_asr_by_audio_path)
        elif existing_asr_by_audio_path:
            precomputed_asr_by_audio_path = existing_asr_by_audio_path

    all_results: list[dict[str, Any]] = []
    all_regenerate: list[dict[str, Any]] = []

    chapter_iterator = tqdm(
        chapter_dirs,
        total=len(chapter_dirs),
        desc="Chapters",
        unit="chapter",
        dynamic_ncols=True,
        leave=True,
    )

    for chapter_dir in chapter_iterator:
        if HAS_TQDM:
            chapter_iterator.set_postfix_str(chapter_dir.name)
        chapter_output_dir = (
            (base_output_dir / chapter_dir.name).resolve()
            if base_output_dir
            else (chapter_dir / "audio_lines" / "asr_audit").resolve()
        )
        chapter_result = run_chapter_audit(
            chapter_dir=chapter_dir,
            output_dir=chapter_output_dir,
            model=args.model,
            device=args.device,
            torch_dtype=args.torch_dtype,
            chunk_length_s=args.chunk_length_s,
            batch_size=args.batch_size,
            mismatch_threshold=args.mismatch_threshold,
            min_wrong_line_delta=args.min_wrong_line_delta,
            top_k=args.top_k,
            dry_run=args.dry_run,
            asr_pipeline=asr_pipeline,
            chapter_inputs=chapter_inputs_by_dir.get(str(chapter_dir)),
            precomputed_asr_by_audio_path=precomputed_asr_by_audio_path,
        )
        all_results.append(chapter_result)
        for item in chapter_result.get("regenerate", []):
            with_chapter = dict(item)
            with_chapter["chapterDir"] = str(chapter_dir)
            all_regenerate.append(with_chapter)

    if len(chapter_dirs) > 1:
        if base_output_dir:
            book_output_dir = base_output_dir.resolve()
        else:
            root_book_dir = chapter_dirs[0].parent
            book_output_dir = (root_book_dir / "asr_audit_book").resolve()
        book_output_dir.mkdir(parents=True, exist_ok=True)

        consolidated = {
            "createdAt": utc_now_iso(),
            "bookDir": str(chapter_dirs[0].parent),
            "chapterCount": len(chapter_dirs),
            "engine": {
                "task": "automatic-speech-recognition",
                "model": args.model,
                "device": args.device,
                "torchDtype": args.torch_dtype,
                "chunkLengthS": args.chunk_length_s,
                "batchSize": args.batch_size,
            },
            "thresholds": {
                "mismatchThreshold": args.mismatch_threshold,
                "minWrongLineDelta": args.min_wrong_line_delta,
            },
            "counts": {
                "markedForRegenerate": len(all_regenerate),
            },
            "chapters": all_results,
            "regenerate": all_regenerate,
        }

        consolidated_path = book_output_dir / "book_asr_audit.json"
        consolidated_manifest_path = book_output_dir / "book_regenerate_manifest.json"
        consolidated_text_path = book_output_dir / "book_regenerate_line_ids.txt"

        consolidated_path.write_text(json.dumps(
            consolidated, indent=2, ensure_ascii=False), encoding="utf-8")
        consolidated_manifest_path.write_text(
            json.dumps({"regenerate": all_regenerate},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        unique_pairs = sorted(
            {
                f"{item.get('chapterDir')}::{item.get('lineId')}"
                for item in all_regenerate
                if isinstance(item.get("lineId"), int)
            }
        )
        consolidated_text_path.write_text(
            "\n".join(unique_pairs), encoding="utf-8")

        print(f"Book audit report: {consolidated_path}")
        print(f"Book regenerate manifest: {consolidated_manifest_path}")
        print(f"Book regenerate list: {consolidated_text_path}")


if __name__ == "__main__":
    main()
