from __future__ import annotations

import gc
import importlib
import logging
import os
import tempfile
import warnings
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np

from .audio_ops import write_audio_excerpt
from .text_utils import (
    normalize_chapter_text,
    similarity_ratio,
    to_float_or_none,
    token_list_similarity,
    tokenize,
    utc_now_iso,
)


def detect_leading_silence_seconds(
    audio_samples: Any,
    sample_rate: int,
    silence_db: float = -40.0,
    frame_ms: float = 10.0,
) -> float:
    """Estimate leading silence by scanning frame RMS against a dB threshold."""
    if len(audio_samples) == 0:
        return 0.0

    frame_size = max(1, int(sample_rate * (frame_ms / 1000.0)))
    threshold = 10 ** (silence_db / 20.0)
    total_frames = len(audio_samples) // frame_size
    if total_frames == 0:
        return 0.0

    for frame_idx in range(total_frames):
        start = frame_idx * frame_size
        end = start + frame_size
        frame = audio_samples[start:end]
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms >= threshold:
            return start / sample_rate

    return 0.0


def build_token_alignment_stats(
    expected_tokens: list[str],
    transcript_tokens: list[str],
) -> dict[str, Any]:
    matcher = SequenceMatcher(None, expected_tokens,
                              transcript_tokens, autojunk=False)

    matched_token_count = 0
    first_expected_match: int | None = None
    last_expected_match: int | None = None
    first_transcript_match: int | None = None
    last_transcript_match: int | None = None

    for block in matcher.get_matching_blocks():
        if block.size <= 0:
            continue
        matched_token_count += block.size
        block_expected_end = block.a + block.size - 1
        block_transcript_end = block.b + block.size - 1

        first_expected_match = block.a if first_expected_match is None else min(
            first_expected_match, block.a)
        last_expected_match = (
            block_expected_end if last_expected_match is None else max(
                last_expected_match, block_expected_end)
        )
        first_transcript_match = (
            block.b if first_transcript_match is None else min(
                first_transcript_match, block.b)
        )
        last_transcript_match = (
            block_transcript_end if last_transcript_match is None else max(
                last_transcript_match, block_transcript_end)
        )

    if first_expected_match is None or last_expected_match is None:
        missing_start_tokens = len(expected_tokens)
        missing_end_tokens = len(expected_tokens)
        extra_leading_tokens = 0
        extra_trailing_tokens = 0
    else:
        missing_start_tokens = first_expected_match
        missing_end_tokens = max(
            0, len(expected_tokens) - last_expected_match - 1)
        extra_leading_tokens = first_transcript_match or 0
        extra_trailing_tokens = max(
            0, len(transcript_tokens) - (last_transcript_match or 0) - 1)

    expected_token_count = len(expected_tokens)
    transcript_token_count = len(transcript_tokens)
    matched_token_ratio = round(
        (matched_token_count / expected_token_count), 4) if expected_token_count else 1.0
    transcript_focus_ratio = (
        round((matched_token_count / transcript_token_count),
              4) if transcript_token_count else 0.0
    )

    return {
        "expectedTokenCount": expected_token_count,
        "transcriptTokenCount": transcript_token_count,
        "matchedTokenCount": matched_token_count,
        "matchedTokenRatio": matched_token_ratio,
        "transcriptFocusRatio": transcript_focus_ratio,
        "missingStartTokens": missing_start_tokens,
        "missingEndTokens": missing_end_tokens,
        "extraLeadingTokens": extra_leading_tokens,
        "extraTrailingTokens": extra_trailing_tokens,
    }


def score_line_validation_report(report: dict[str, Any]) -> float:
    alignment = report.get("alignment") if isinstance(
        report.get("alignment"), dict) else {}
    neighbor_hints = report.get("neighborHints") if isinstance(
        report.get("neighborHints"), dict) else {}

    score = float(report.get("expectedSimilarity", 0.0))
    score += min(0.05, float(alignment.get("matchedTokenRatio", 0.0)) * 0.05)
    score -= float(report.get("boundaryPressureBefore", 0.0))
    score -= float(report.get("boundaryPressureAfter", 0.0))

    missing_tokens = int(alignment.get("missingStartTokens", 0)) + \
        int(alignment.get("missingEndTokens", 0))
    score -= min(0.12, float(missing_tokens) * 0.015)

    if int(alignment.get("extraLeadingTokens", 0)) > 0:
        score -= min(0.08, float(neighbor_hints.get("previousTailSimilarity", 0.0)) * 0.04)
    if int(alignment.get("extraTrailingTokens", 0)) > 0:
        score -= min(0.08,
                     float(neighbor_hints.get("nextHeadSimilarity", 0.0)) * 0.04)

    if not str(report.get("transcriptText", "")).strip():
        score -= 0.25

    return round(score, 4)


def build_line_validation_report(
    *,
    lines: list[dict[str, Any]],
    line_index: int,
    transcript_text: str,
    audio_path: Path,
    similarity_threshold: float,
    neighbor_delta: float,
) -> dict[str, Any]:
    expected_line = lines[line_index]
    expected_text = str(expected_line["text"])
    previous_text = str(lines[line_index - 1]["text"]
                        ) if line_index > 0 else ""
    next_text = str(lines[line_index + 1]["text"]
                    ) if line_index + 1 < len(lines) else ""

    normalized_transcript = normalize_chapter_text(transcript_text)
    transcript_tokens = tokenize(normalized_transcript)
    expected_tokens = tokenize(expected_text)
    previous_tokens = tokenize(previous_text)
    next_tokens = tokenize(next_text)

    alignment = build_token_alignment_stats(expected_tokens, transcript_tokens)

    extra_leading_tokens = transcript_tokens[: alignment["extraLeadingTokens"]]
    extra_trailing_tokens = (
        transcript_tokens[-alignment["extraTrailingTokens"]:]
        if alignment["extraTrailingTokens"] > 0
        else []
    )

    previous_tail_similarity = 0.0
    if previous_tokens and extra_leading_tokens:
        previous_tail_similarity = token_list_similarity(
            previous_tokens[-len(extra_leading_tokens):],
            extra_leading_tokens,
        )

    next_head_similarity = 0.0
    if next_tokens and extra_trailing_tokens:
        next_head_similarity = token_list_similarity(
            next_tokens[: len(extra_trailing_tokens)],
            extra_trailing_tokens,
        )

    expected_similarity = similarity_ratio(
        expected_text, normalized_transcript)
    previous_plus_current_similarity = (
        similarity_ratio(
            f"{previous_text} {expected_text}".strip(), normalized_transcript)
        if previous_text
        else None
    )
    current_plus_next_similarity = (
        similarity_ratio(
            f"{expected_text} {next_text}".strip(), normalized_transcript)
        if next_text
        else None
    )

    boundary_pressure_before = round(
        max(0.0, ((previous_plus_current_similarity or 0.0) -
            expected_similarity - neighbor_delta)),
        4,
    )
    boundary_pressure_after = round(
        max(0.0, ((current_plus_next_similarity or 0.0) -
            expected_similarity - neighbor_delta)),
        4,
    )

    likely_boundary_drift = (
        boundary_pressure_before > 0.0
        or boundary_pressure_after > 0.0
        or (alignment["extraLeadingTokens"] > 0 and previous_tail_similarity >= 0.65)
        or (alignment["extraTrailingTokens"] > 0 and next_head_similarity >= 0.65)
    )

    status = "ok"
    needs_boundary_adjustment = False
    if not normalized_transcript:
        status = "empty-transcript"
        needs_boundary_adjustment = True
    elif expected_similarity < similarity_threshold or likely_boundary_drift:
        status = "likely-boundary-drift" if likely_boundary_drift else "needs-review"
        needs_boundary_adjustment = True

    report = {
        "lineIndex": line_index,
        "lineId": int(expected_line["id"]),
        "audioPath": str(audio_path),
        "expectedText": expected_text,
        "transcriptText": normalized_transcript,
        "expectedSimilarity": expected_similarity,
        "previousPlusCurrentSimilarity": previous_plus_current_similarity,
        "currentPlusNextSimilarity": current_plus_next_similarity,
        "boundaryPressureBefore": boundary_pressure_before,
        "boundaryPressureAfter": boundary_pressure_after,
        "alignment": alignment,
        "neighborHints": {
            "previousTailSimilarity": previous_tail_similarity,
            "nextHeadSimilarity": next_head_similarity,
        },
        "status": status,
        "needsBoundaryAdjustment": needs_boundary_adjustment,
    }
    report["score"] = score_line_validation_report(report)
    return report


def count_boundary_mismatch_tokens(report: dict[str, Any] | None) -> int:
    if not isinstance(report, dict):
        return 0
    alignment = report.get("alignment")
    if not isinstance(alignment, dict):
        return 0

    return max(
        0,
        int(alignment.get("missingStartTokens", 0))
        + int(alignment.get("missingEndTokens", 0))
        + int(alignment.get("extraLeadingTokens", 0))
        + int(alignment.get("extraTrailingTokens", 0)),
    )


def summarize_validation_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    flagged = [report for report in reports if report.get(
        "needsBoundaryAdjustment")]
    return {
        "lineCount": len(reports),
        "flaggedLineCount": len(flagged),
        "flaggedLineIds": [int(report["lineId"]) for report in flagged],
        "okLineCount": len(reports) - len(flagged),
    }


def release_accelerator_cache() -> None:
    gc.collect()
    try:
        import torch  # noqa: WPS433

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (ImportError, OSError):
        return


def create_split_validation_transcriber(
    *,
    validation_engine: str,
    main_engine: str,
    device: str,
    compute_type: str,
    batch_size: int,
    vad_method: str,
    quiet: bool,
    trim_leading_silence: bool,
    silence_db: float,
) -> tuple[dict[str, Any], Any]:
    selected_engine = main_engine if validation_engine == "same" else validation_engine

    if selected_engine == "whisperx":
        try:
            import torch  # noqa: WPS433

            whisperx = importlib.import_module("whisperx")
        except ImportError as exc:
            raise RuntimeError(
                "Split validation requested WhisperX, but whisperx is not installed."
            ) from exc

        selected_device = "cuda" if torch.cuda.is_available() else "cpu"
        if device != "auto":
            selected_device = device

        if selected_device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "Split validation requested CUDA WhisperX, but CUDA is not available."
            )

        selected_compute_type = "float16" if selected_device == "cuda" else "int8"
        if compute_type != "auto":
            selected_compute_type = compute_type

        selected_batch_size = batch_size if batch_size > 0 else (
            32 if selected_device == "cuda" else 8)

        if quiet:
            warnings.simplefilter("ignore")
            os.environ.setdefault("PYTHONWARNINGS", "ignore")
            os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
            logging.getLogger().setLevel(logging.ERROR)
            logging.getLogger("whisperx").setLevel(logging.ERROR)
            logging.getLogger("pyannote").setLevel(logging.ERROR)
            logging.getLogger("faster_whisper").setLevel(logging.ERROR)
            logging.getLogger("torch").setLevel(logging.ERROR)

        model = whisperx.load_model(
            "small.en",
            device=selected_device,
            compute_type=selected_compute_type,
            vad_method=vad_method,
        )

        def transcribe_clip(audio_path: Path) -> dict[str, Any]:
            audio = whisperx.load_audio(str(audio_path))
            result = model.transcribe(audio, batch_size=selected_batch_size)
            result["text"] = " ".join(
                segment.get("text", "").strip()
                for segment in result.get("segments", [])
                if isinstance(segment, dict)
            ).strip()
            return result

        return (
            {
                "engine": "whisperx",
                "model": "small.en",
                "device": selected_device,
                "computeType": selected_compute_type,
                "batchSize": selected_batch_size,
                "vadMethod": vad_method,
                "alignment": False,
            },
            transcribe_clip,
        )

    try:
        import whisper  # noqa: WPS433
    except ImportError as exc:
        raise RuntimeError(
            "Split validation requested OpenAI Whisper, but openai-whisper is not installed."
        ) from exc

    try:
        import torch  # noqa: WPS433
    except (ImportError, OSError):
        torch = None

    selected_device = "cpu"
    if torch is not None and torch.cuda.is_available():
        selected_device = "cuda"
    if device != "auto":
        selected_device = device

    model = whisper.load_model("small.en", device=selected_device)

    def transcribe_clip_openai(audio_path: Path) -> dict[str, Any]:
        audio_input: str | np.ndarray = str(audio_path)
        if trim_leading_silence:
            audio_samples = whisper.load_audio(str(audio_path))
            leading_silence_offset = detect_leading_silence_seconds(
                audio_samples=audio_samples,
                sample_rate=16000,
                silence_db=silence_db,
            )
            if leading_silence_offset > 0:
                start_idx = int(leading_silence_offset * 16000)
                audio_input = audio_samples[start_idx:]
        result = model.transcribe(audio_input, word_timestamps=False)
        result["text"] = str(result.get("text", "")).strip()
        return result

    return (
        {
            "engine": "openai",
            "model": "small.en",
            "device": selected_device,
            "trimLeadingSilence": trim_leading_silence,
            "silenceDb": silence_db,
        },
        transcribe_clip_openai,
    )


def validate_split_lines(
    *,
    lines: list[dict[str, Any]],
    split_lines: list[dict[str, Any]],
    transcribe_clip: Any,
    similarity_threshold: float,
    neighbor_delta: float,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    total = len(split_lines)

    for index, split_line in enumerate(split_lines):
        if total <= 12 or index == 0 or index + 1 == total or (index + 1) % 10 == 0:
            print(
                f"Validating split clip {index + 1}/{total}: line {int(split_line['id'])}")

        audio_path = Path(str(split_line["audioPath"]))
        asr_result = transcribe_clip(audio_path)
        transcript_text = str(asr_result.get("text", "")).strip()
        reports.append(
            build_line_validation_report(
                lines=lines,
                line_index=index,
                transcript_text=transcript_text,
                audio_path=audio_path,
                similarity_threshold=similarity_threshold,
                neighbor_delta=neighbor_delta,
            )
        )

    return reports


def build_boundary_candidate_positions(
    boundary_report: dict[str, Any] | None,
    current_boundary_sec: float,
    alternates_per_side: int,
) -> list[float]:
    positions = [round(current_boundary_sec, 4)]
    if not isinstance(boundary_report, dict):
        return positions

    runs = boundary_report.get("candidateSilenceRuns")
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            center_sec = to_float_or_none(run.get("centerSec"))
            if center_sec is not None:
                positions.append(round(center_sec, 4))

    silence_run = boundary_report.get("silenceRun")
    if isinstance(silence_run, dict):
        center_sec = to_float_or_none(silence_run.get("centerSec"))
        if center_sec is not None:
            positions.append(round(center_sec, 4))

    unique_positions = sorted({value for value in positions})
    if len(unique_positions) <= 1 or alternates_per_side <= 0:
        return unique_positions

    earlier = [value for value in unique_positions if value <
               current_boundary_sec]
    later = [value for value in unique_positions if value > current_boundary_sec]

    selected = earlier[-alternates_per_side:]
    selected.append(round(current_boundary_sec, 4))
    selected.extend(later[:alternates_per_side])
    return sorted({value for value in selected})


def attempt_boundary_shift(
    *,
    lines: list[dict[str, Any]],
    validation_reports: list[dict[str, Any]],
    boundary_index: int,
    boundary_report: dict[str, Any] | None,
    full_audio: np.ndarray,
    sample_rate: int,
    transcribe_clip: Any,
    similarity_threshold: float,
    neighbor_delta: float,
    min_line_sec: float,
    alternates_per_side: int,
    min_improvement: float,
    trigger_line_id: int,
) -> dict[str, Any] | None:
    left_index = boundary_index - 1
    right_index = boundary_index
    if left_index < 0 or right_index >= len(lines):
        return None

    left_line_id = int(lines[left_index]["id"])
    right_line_id = int(lines[right_index]["id"])

    trigger_side: str | None = None
    if left_line_id == trigger_line_id:
        trigger_side = "left"
    elif right_line_id == trigger_line_id:
        trigger_side = "right"

    baseline_left_report = validation_reports[left_index]
    baseline_right_report = validation_reports[right_index]
    baseline_trigger_report: dict[str, Any] | None = None
    if trigger_side == "left":
        baseline_trigger_report = baseline_left_report
    elif trigger_side == "right":
        baseline_trigger_report = baseline_right_report

    baseline_trigger_mismatch = count_boundary_mismatch_tokens(
        baseline_trigger_report)
    baseline_trigger_similarity = (
        float(baseline_trigger_report.get("expectedSimilarity", 0.0))
        if isinstance(baseline_trigger_report, dict)
        else 0.0
    )

    current_boundary_sec = round(float(lines[left_index]["endSec"]), 4)
    candidate_positions = build_boundary_candidate_positions(
        boundary_report=boundary_report,
        current_boundary_sec=current_boundary_sec,
        alternates_per_side=alternates_per_side,
    )

    min_candidate_sec = float(lines[left_index]["startSec"]) + min_line_sec
    max_candidate_sec = float(lines[right_index]["endSec"]) - min_line_sec
    if max_candidate_sec < min_candidate_sec:
        return None

    # Include short fallback nudges around the current boundary so a clipped
    # terminal word can still be recovered even when silence runs are sparse.
    nudge_step_sec = max(0.02, min_line_sec * 0.25)
    nudge_count = max(2, alternates_per_side + 1)
    for step_index in range(1, nudge_count + 1):
        nudge = round(nudge_step_sec * step_index, 4)
        candidate_positions.append(round(current_boundary_sec - nudge, 4))
        candidate_positions.append(round(current_boundary_sec + nudge, 4))

    candidate_positions = sorted(
        {
            round(value, 4)
            for value in candidate_positions
            if min_candidate_sec <= float(value) <= max_candidate_sec
        }
    )

    if len(candidate_positions) <= 1:
        return None

    baseline_score = float(
        baseline_left_report["score"]) + float(baseline_right_report["score"])

    def rank_candidate(
        *,
        pair_score: float,
        trigger_report: dict[str, Any] | None,
    ) -> tuple[float, float, float]:
        if not isinstance(trigger_report, dict):
            return (0.0, 0.0, round(pair_score, 4))
        return (
            float(-count_boundary_mismatch_tokens(trigger_report)),
            float(trigger_report.get("expectedSimilarity", 0.0)),
            round(pair_score, 4),
        )

    best_rank = rank_candidate(
        pair_score=baseline_score, trigger_report=baseline_trigger_report)
    best_score = baseline_score
    best_boundary_sec = current_boundary_sec
    best_left_report: dict[str, Any] | None = None
    best_right_report: dict[str, Any] | None = None
    best_trigger_report: dict[str, Any] | None = None

    best_pair_score = baseline_score
    best_pair_boundary_sec = current_boundary_sec
    best_pair_left_report: dict[str, Any] | None = None
    best_pair_right_report: dict[str, Any] | None = None
    best_pair_trigger_report: dict[str, Any] | None = None

    with tempfile.TemporaryDirectory(prefix=f"split-boundary-{boundary_index}-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for candidate_sec in candidate_positions:
            if abs(candidate_sec - current_boundary_sec) < 1e-4:
                continue

            left_line = dict(lines[left_index])
            right_line = dict(lines[right_index])
            left_line["endSec"] = round(candidate_sec, 4)
            right_line["startSec"] = round(candidate_sec, 4)
            left_line["durationSec"] = round(
                float(left_line["endSec"]) - float(left_line["startSec"]), 4)
            right_line["durationSec"] = round(
                float(right_line["endSec"]) - float(right_line["startSec"]), 4)

            if float(left_line["durationSec"]) < min_line_sec or float(right_line["durationSec"]) < min_line_sec:
                continue

            left_audio_path = temp_dir / f"{int(left_line['id'])}-left.wav"
            right_audio_path = temp_dir / f"{int(right_line['id'])}-right.wav"

            write_audio_excerpt(
                audio=full_audio,
                sample_rate=sample_rate,
                start_sec=float(left_line["startSec"]),
                end_sec=float(left_line["endSec"]),
                output_path=left_audio_path,
            )
            write_audio_excerpt(
                audio=full_audio,
                sample_rate=sample_rate,
                start_sec=float(right_line["startSec"]),
                end_sec=float(right_line["endSec"]),
                output_path=right_audio_path,
            )

            left_report = build_line_validation_report(
                lines=lines,
                line_index=left_index,
                transcript_text=str(transcribe_clip(
                    left_audio_path).get("text", "")).strip(),
                audio_path=left_audio_path,
                similarity_threshold=similarity_threshold,
                neighbor_delta=neighbor_delta,
            )
            right_report = build_line_validation_report(
                lines=lines,
                line_index=right_index,
                transcript_text=str(transcribe_clip(
                    right_audio_path).get("text", "")).strip(),
                audio_path=right_audio_path,
                similarity_threshold=similarity_threshold,
                neighbor_delta=neighbor_delta,
            )

            candidate_score = float(
                left_report["score"]) + float(right_report["score"])
            candidate_trigger_report: dict[str, Any] | None = None
            if trigger_side == "left":
                candidate_trigger_report = left_report
            elif trigger_side == "right":
                candidate_trigger_report = right_report

            if candidate_score > best_pair_score:
                best_pair_score = candidate_score
                best_pair_boundary_sec = round(candidate_sec, 4)
                best_pair_left_report = left_report
                best_pair_right_report = right_report
                best_pair_trigger_report = candidate_trigger_report

            candidate_rank = rank_candidate(
                pair_score=candidate_score, trigger_report=candidate_trigger_report)
            if candidate_rank > best_rank:
                best_rank = candidate_rank
                best_score = candidate_score
                best_boundary_sec = round(candidate_sec, 4)
                best_left_report = left_report
                best_right_report = right_report
                best_trigger_report = candidate_trigger_report

    def trigger_deltas(trigger_report: dict[str, Any] | None) -> tuple[int, float]:
        if not isinstance(trigger_report, dict):
            return 0, 0.0
        report_mismatch = count_boundary_mismatch_tokens(trigger_report)
        mismatch_reduction = baseline_trigger_mismatch - report_mismatch
        similarity_delta = round(float(trigger_report.get(
            "expectedSimilarity", 0.0)) - baseline_trigger_similarity, 4)
        return mismatch_reduction, similarity_delta

    selected_boundary_sec = best_boundary_sec
    selected_left_report = best_left_report
    selected_right_report = best_right_report
    selected_trigger_report = best_trigger_report
    selected_improvement = round(best_score - baseline_score, 4)
    trigger_mismatch_reduction, trigger_similarity_improvement = trigger_deltas(
        selected_trigger_report)

    accept_by_score = selected_improvement >= min_improvement
    max_pair_score_regression = max(0.02, min_improvement * 0.5)
    accept_by_trigger_recovery = (
        trigger_mismatch_reduction > 0
        and selected_improvement >= (-1.0 * max_pair_score_regression)
        and (
            trigger_similarity_improvement >= 0.01
            or (
                baseline_trigger_mismatch > 0
                and count_boundary_mismatch_tokens(selected_trigger_report) == 0
            )
        )
    )

    if (selected_left_report is None or selected_right_report is None) or (
        not accept_by_score and not accept_by_trigger_recovery
    ):
        fallback_improvement = round(best_pair_score - baseline_score, 4)
        if (
            best_pair_left_report is None
            or best_pair_right_report is None
            or fallback_improvement < min_improvement
        ):
            return None

        selected_boundary_sec = best_pair_boundary_sec
        selected_left_report = best_pair_left_report
        selected_right_report = best_pair_right_report
        selected_trigger_report = best_pair_trigger_report
        selected_improvement = fallback_improvement
        trigger_mismatch_reduction, trigger_similarity_improvement = trigger_deltas(
            selected_trigger_report)
        accept_by_score = True
        accept_by_trigger_recovery = False

    if not accept_by_score and not accept_by_trigger_recovery:
        return None

    accept_reason = "score-improvement" if accept_by_score else "trigger-token-recovery"

    lines[left_index]["endSec"] = selected_boundary_sec
    lines[right_index]["startSec"] = selected_boundary_sec
    lines[left_index]["durationSec"] = round(
        float(lines[left_index]["endSec"]) -
        float(lines[left_index]["startSec"]),
        4,
    )
    lines[right_index]["durationSec"] = round(
        float(lines[right_index]["endSec"]) -
        float(lines[right_index]["startSec"]),
        4,
    )
    validation_reports[left_index] = selected_left_report
    validation_reports[right_index] = selected_right_report

    return {
        "triggerLineId": trigger_line_id,
        "boundaryIndex": boundary_index,
        "lineIds": [
            int(lines[left_index]["id"]),
            int(lines[right_index]["id"]),
        ],
        "oldBoundarySec": current_boundary_sec,
        "newBoundarySec": selected_boundary_sec,
        "scoreImprovement": selected_improvement,
        "acceptReason": accept_reason,
        "triggerMismatchReduction": trigger_mismatch_reduction,
        "triggerSimilarityImprovement": trigger_similarity_improvement,
        "candidateBoundariesSec": candidate_positions,
    }


def attach_validation_summary_to_split_lines(
    split_lines: list[dict[str, Any]],
    validation_reports: list[dict[str, Any]],
) -> None:
    validation_by_line_id = {
        int(report["lineId"]): report for report in validation_reports}

    for line in split_lines:
        report = validation_by_line_id.get(int(line["id"]))
        if report is None:
            continue
        report["audioPath"] = str(line["audioPath"])
        line["validation"] = {
            "status": report["status"],
            "expectedSimilarity": report["expectedSimilarity"],
            "score": report["score"],
            "needsBoundaryAdjustment": report["needsBoundaryAdjustment"],
        }


def build_split_validation_payload(
    *,
    validation_engine: dict[str, Any],
    initial_summary: dict[str, Any],
    final_summary: dict[str, Any],
    adjustments: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    similarity_threshold: float,
    neighbor_delta: float,
    min_improvement: float,
    alternates_per_side: int,
) -> dict[str, Any]:
    return {
        "formatVersion": "audio-test/split-validation/v1",
        "createdAt": utc_now_iso(),
        "enabled": True,
        "engine": validation_engine,
        "settings": {
            "similarityThreshold": similarity_threshold,
            "neighborDelta": neighbor_delta,
            "minImprovement": min_improvement,
            "alternatesPerSide": alternates_per_side,
        },
        "summary": {
            "initial": initial_summary,
            "final": final_summary,
            "adjustmentCount": len(adjustments),
            "adjustedBoundaryIndexes": [int(item["boundaryIndex"]) for item in adjustments],
        },
        "adjustments": adjustments,
        "lines": reports,
    }
