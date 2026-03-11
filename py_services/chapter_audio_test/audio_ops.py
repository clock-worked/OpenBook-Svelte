from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .text_utils import safe_name


def load_audio_mono_float(audio_path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(
        str(audio_path), dtype="float32", always_2d=False)
    if isinstance(audio, np.ndarray) and audio.ndim == 2:
        audio = audio.mean(axis=1)
    if not isinstance(audio, np.ndarray) or audio.size == 0:
        raise ValueError(f"Unable to read audio samples from: {audio_path}")
    return audio, int(sample_rate)


def load_audio_float_2d(audio_path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(
        str(audio_path), dtype="float32", always_2d=True)
    if not isinstance(audio, np.ndarray) or audio.size == 0:
        raise ValueError(f"Unable to read audio samples from: {audio_path}")
    return audio, int(sample_rate)


def build_rms_envelope_db(
    audio: np.ndarray,
    sample_rate: int,
    frame_ms: int,
) -> tuple[np.ndarray, np.ndarray]:
    frame_samples = max(1, int(sample_rate * (frame_ms / 1000.0)))
    total_samples = audio.size
    frame_count = max(1, int(np.ceil(total_samples / frame_samples)))

    rms = np.empty(frame_count, dtype=np.float32)
    centers_sec = np.empty(frame_count, dtype=np.float32)

    for frame_index in range(frame_count):
        start_idx = frame_index * frame_samples
        end_idx = min(total_samples, start_idx + frame_samples)
        frame = audio[start_idx:end_idx]
        if frame.size == 0:
            frame_rms = 0.0
        else:
            frame_rms = float(
                np.sqrt(np.mean(np.square(frame), dtype=np.float64)))
        rms[frame_index] = frame_rms
        centers_sec[frame_index] = (
            (start_idx + end_idx) / 2.0) / float(sample_rate)

    db = 20.0 * np.log10(np.maximum(rms, 1e-8))
    return centers_sec, db


def collect_silence_runs(
    *,
    centers_sec: np.ndarray,
    db: np.ndarray,
    start_sec: float,
    end_sec: float,
    silence_enter_db: float,
    silence_exit_db: float,
    min_silence_ms: int,
    frame_ms: int,
) -> list[dict[str, float]]:
    if end_sec <= start_sec:
        return []

    frame_sec = max(1e-6, float(frame_ms) / 1000.0)
    min_silence_sec = max(0.0, float(min_silence_ms) / 1000.0)
    in_window = np.where((centers_sec >= start_sec) &
                         (centers_sec <= end_sec))[0]
    if in_window.size == 0:
        return []

    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    run_end: int | None = None
    currently_silent = False

    for raw_idx in in_window:
        idx = int(raw_idx)
        frame_db = float(db[idx])
        if not currently_silent:
            if frame_db <= silence_enter_db:
                currently_silent = True
                run_start = idx
                run_end = idx
            continue

        if frame_db >= silence_exit_db:
            if run_start is not None and run_end is not None:
                runs.append((run_start, run_end))
            currently_silent = False
            run_start = None
            run_end = None
            continue

        run_end = idx

    if currently_silent and run_start is not None and run_end is not None:
        runs.append((run_start, run_end))

    output: list[dict[str, float]] = []
    for run_start_idx, run_end_idx in runs:
        run_start_sec = float(centers_sec[run_start_idx]) - (frame_sec / 2.0)
        run_end_sec = float(centers_sec[run_end_idx]) + (frame_sec / 2.0)
        run_duration = run_end_sec - run_start_sec
        if run_duration < min_silence_sec:
            continue
        output.append(
            {
                "startSec": round(run_start_sec, 4),
                "endSec": round(run_end_sec, 4),
                "centerSec": round((run_start_sec + run_end_sec) / 2.0, 4),
                "durationSec": round(run_duration, 4),
            }
        )

    return output


def choose_boundary_near_anchor(
    runs: list[dict[str, float]],
    anchor_sec: float,
) -> tuple[float, dict[str, float] | None]:
    if not runs:
        return round(anchor_sec, 4), None

    best = min(
        runs,
        key=lambda run: (
            abs(float(run["centerSec"]) - anchor_sec),
            -float(run["durationSec"]),
        ),
    )
    return round(float(best["centerSec"]), 4), best


def refine_line_boundaries_with_silence(
    *,
    lines: list[dict[str, Any]],
    full_audio_path: Path,
    audio_duration_sec: float,
    frame_ms: int,
    window_ms: int,
    min_silence_ms: int,
    silence_enter_db: float,
    silence_exit_db: float,
    line_end_pad_ms: int,
    next_start_backpad_ms: int,
    min_line_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(lines) <= 1:
        return lines, {"enabled": True, "applied": False, "reason": "single-line"}

    refined = [dict(item) for item in lines]
    audio, sample_rate = load_audio_mono_float(full_audio_path)
    centers_sec, db = build_rms_envelope_db(
        audio, sample_rate, frame_ms=frame_ms)

    min_line_sec = max(0.001, float(min_line_ms) / 1000.0)
    window_sec = max(0.01, float(window_ms) / 1000.0)
    end_pad_sec = max(0.0, float(line_end_pad_ms) / 1000.0)
    start_backpad_sec = max(0.0, float(next_start_backpad_ms) / 1000.0)

    boundary_records: list[dict[str, Any]] = []

    for index in range(len(refined) - 1):
        left = refined[index]
        right = refined[index + 1]

        left_start = float(left["startSec"])
        left_end = float(left["endSec"])
        right_start = float(right["startSec"])
        right_end = float(right["endSec"])

        left_anchor = min(audio_duration_sec, left_end + end_pad_sec)
        right_anchor = max(0.0, right_start - start_backpad_sec)
        anchor_sec = (left_anchor + right_anchor) / 2.0

        search_start = max(left_start + min_line_sec, anchor_sec - window_sec)
        search_end = min(right_end - min_line_sec, anchor_sec + window_sec)

        if search_end <= search_start:
            search_start = max(left_start + min_line_sec,
                               min(left_end, right_start) - (window_sec * 0.5))
            search_end = min(right_end - min_line_sec,
                             max(left_end, right_start) + (window_sec * 0.5))

        if search_end <= search_start:
            clamped_boundary = max(
                left_start + min_line_sec, min(right_end - min_line_sec, anchor_sec))
            clamped_boundary = round(clamped_boundary, 4)
            left["endSec"] = clamped_boundary
            right["startSec"] = clamped_boundary
            left["durationSec"] = round(left["endSec"] - left["startSec"], 4)
            right["durationSec"] = round(
                right["endSec"] - right["startSec"], 4)
            boundary_records.append(
                {
                    "boundaryIndex": index + 1,
                    "anchorSec": round(anchor_sec, 4),
                    "snappedSec": clamped_boundary,
                    "search": {
                        "startSec": round(search_start, 4),
                        "endSec": round(search_end, 4),
                    },
                    "silenceRun": None,
                    "usedFallbackClamp": True,
                }
            )
            continue

        runs = collect_silence_runs(
            centers_sec=centers_sec,
            db=db,
            start_sec=search_start,
            end_sec=search_end,
            silence_enter_db=silence_enter_db,
            silence_exit_db=silence_exit_db,
            min_silence_ms=min_silence_ms,
            frame_ms=frame_ms,
        )
        snapped_boundary, picked_run = choose_boundary_near_anchor(
            runs, anchor_sec)

        min_boundary = left_start + min_line_sec
        max_boundary = right_end - min_line_sec
        if max_boundary < min_boundary:
            max_boundary = min_boundary
        snapped_boundary = max(min_boundary, min(
            max_boundary, snapped_boundary))
        snapped_boundary = round(snapped_boundary, 4)

        left["endSec"] = snapped_boundary
        right["startSec"] = snapped_boundary
        left["durationSec"] = round(left["endSec"] - left["startSec"], 4)
        right["durationSec"] = round(right["endSec"] - right["startSec"], 4)

        boundary_records.append(
            {
                "boundaryIndex": index + 1,
                "anchorSec": round(anchor_sec, 4),
                "snappedSec": snapped_boundary,
                "search": {
                    "startSec": round(search_start, 4),
                    "endSec": round(search_end, 4),
                },
                "silenceRun": picked_run,
                "candidateSilenceRuns": runs,
                "usedFallbackClamp": False,
            }
        )

    if refined:
        first_start = max(0.0, min(audio_duration_sec,
                          float(refined[0]["startSec"])))
        refined[0]["startSec"] = round(first_start, 4)
        refined[0]["durationSec"] = round(
            float(refined[0]["endSec"]) - float(refined[0]["startSec"]), 4)

        refined[-1]["endSec"] = round(audio_duration_sec, 4)
        refined[-1]["durationSec"] = round(
            float(refined[-1]["endSec"]) - float(refined[-1]["startSec"]), 4)

    for index in range(1, len(refined)):
        previous = refined[index - 1]
        current = refined[index]
        if float(current["startSec"]) < float(previous["endSec"]):
            current["startSec"] = previous["endSec"]
        if float(current["endSec"]) < float(current["startSec"]):
            current["endSec"] = current["startSec"]
        previous["durationSec"] = round(
            float(previous["endSec"]) - float(previous["startSec"]), 4)
        current["durationSec"] = round(
            float(current["endSec"]) - float(current["startSec"]), 4)

    report = {
        "enabled": True,
        "applied": True,
        "method": "pause-snap-near-line-boundary",
        "params": {
            "frameMs": frame_ms,
            "windowMs": window_ms,
            "minSilenceMs": min_silence_ms,
            "silenceEnterDb": silence_enter_db,
            "silenceExitDb": silence_exit_db,
            "lineEndPadMs": line_end_pad_ms,
            "nextStartBackpadMs": next_start_backpad_ms,
            "minLineMs": min_line_ms,
        },
        "boundaries": boundary_records,
    }

    return refined, report


def split_audio_by_lines(
    full_audio_path: Path,
    output_dir: Path,
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    audio, sample_rate = load_audio_float_2d(full_audio_path)
    total_samples = audio.shape[0]

    splits_root = output_dir / "splits"
    splits_root.mkdir(parents=True, exist_ok=True)

    written: list[dict[str, Any]] = []
    for line in lines:
        start_sec = float(line["startSec"])
        end_sec = float(line["endSec"])

        start_sample = int(round(start_sec * sample_rate))
        end_sample = int(round(end_sec * sample_rate))

        start_sample = max(0, min(total_samples, start_sample))
        end_sample = max(start_sample, min(total_samples, end_sample))

        chunk = audio[start_sample:end_sample]

        character_folder = safe_name(
            str(line.get("characterFolder", "unknown")))
        character_dir = splits_root / character_folder
        character_dir.mkdir(parents=True, exist_ok=True)

        line_id = int(line["id"])
        file_name = f"{line_id}-{character_folder}.wav"
        file_path = character_dir / file_name

        sf.write(str(file_path), chunk, sample_rate)

        written.append(
            {
                **line,
                "audioFile": file_name,
                "audioPath": str(file_path),
            }
        )

    return written


def write_audio_excerpt(
    *,
    audio: np.ndarray,
    sample_rate: int,
    start_sec: float,
    end_sec: float,
    output_path: Path,
) -> None:
    total_samples = audio.shape[0]
    start_sample = int(round(start_sec * sample_rate))
    end_sample = int(round(end_sec * sample_rate))

    start_sample = max(0, min(total_samples, start_sample))
    end_sample = max(start_sample, min(total_samples, end_sample))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), audio[start_sample:end_sample], sample_rate)
