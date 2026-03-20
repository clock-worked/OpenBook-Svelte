"""Run heuristic audio split experiments without ASR dependencies."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def generate_vibevoice_audio(
    *,
    text: str,
    sample_path: Path,
    output_audio: Path,
    model_path: str | None,
    repo_path: str | None,
    device: str | None,
    ddpm_steps: int,
    cfg_scale: float,
    seed: int | None,
) -> None:
    from vibevoice_local_service import VibeVoiceLocalService  # noqa: WPS433

    service = VibeVoiceLocalService(
        model_path=model_path,
        repo_path=repo_path,
        device=device,
        ddpm_steps=ddpm_steps,
        cfg_scale=cfg_scale,
    )
    service.generate_audio(
        text=text,
        sample_path=str(sample_path),
        output_path=str(output_audio),
        seed=seed,
    )


def count_nonspace_characters(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def estimate_segment_units(
    text: str,
    *,
    punctuation_char_weight: float,
    terminal_punctuation_bonus: float,
) -> dict[str, Any]:
    nonspace_chars = count_nonspace_characters(text)
    punctuation_chars = len(re.findall(r"[,:;.!?]", text))
    stripped = text.strip()
    terminal_bonus = terminal_punctuation_bonus if stripped and stripped[-1] in ",:;.!?" else 0.0
    weighted_units = max(
        1.0,
        float(nonspace_chars) + (float(punctuation_chars) * punctuation_char_weight) + terminal_bonus,
    )
    return {
        "nonSpaceChars": nonspace_chars,
        "punctuationChars": punctuation_chars,
        "terminalPunctuationBonus": terminal_bonus,
        "weightedUnits": round(weighted_units, 4),
    }


def get_audio_duration_seconds(audio_path: Path) -> float:
    return float(sf.info(str(audio_path)).duration)


def load_audio_mono_float(audio_path: Path) -> tuple[np.ndarray, int] | tuple[None, None]:
    try:
        audio, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
    except Exception:
        return None, None

    if isinstance(audio, np.ndarray) and audio.ndim == 2:
        audio = audio.mean(axis=1)
    if not isinstance(audio, np.ndarray):
        return None, None
    if audio.size == 0 or sample_rate <= 0:
        return None, None
    return audio, int(sample_rate)


def build_rms_envelope_db(audio: np.ndarray, sample_rate: int, frame_ms: int) -> tuple[np.ndarray, np.ndarray]:
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
            frame_rms = float(np.sqrt(np.mean(np.square(frame), dtype=np.float64)))
        rms[frame_index] = frame_rms
        centers_sec[frame_index] = ((start_idx + end_idx) / 2.0) / float(sample_rate)

    db = 20.0 * np.log10(np.maximum(rms, 1e-8))
    return centers_sec, db


def build_zero_crossing_rate(audio: np.ndarray, sample_rate: int, frame_ms: int) -> np.ndarray:
    frame_samples = max(1, int(sample_rate * (frame_ms / 1000.0)))
    total_samples = audio.size
    frame_count = max(1, int(np.ceil(total_samples / frame_samples)))

    zcr = np.empty(frame_count, dtype=np.float32)
    for frame_index in range(frame_count):
        start_idx = frame_index * frame_samples
        end_idx = min(total_samples, start_idx + frame_samples)
        frame = audio[start_idx:end_idx]
        if frame.size < 2:
            zcr[frame_index] = 0.0
            continue
        signs = np.signbit(frame)
        crossings = np.count_nonzero(signs[:-1] != signs[1:])
        zcr[frame_index] = float(crossings) / float(frame.size - 1)
    return zcr


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
    in_window = np.where((centers_sec >= start_sec) & (centers_sec <= end_sec))[0]
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


def choose_pause_near_anchor(runs: list[dict[str, float]], anchor_sec: float) -> float:
    if not runs:
        return round(anchor_sec, 4)
    best = min(
        runs,
        key=lambda run: (
            abs(float(run["centerSec"]) - anchor_sec),
            -float(run["durationSec"]),
        ),
    )
    return round(float(best["centerSec"]), 4)


def guard_boundary_against_voiced_region(
    *,
    candidate_sec: float,
    anchor_sec: float,
    centers_sec: np.ndarray,
    db: np.ndarray,
    zcr: np.ndarray,
    window_ms: int,
    voiced_db_threshold: float,
    voiced_zcr_threshold: float,
) -> float:
    window_sec = max(0.0, window_ms / 1000.0)
    in_window = np.where(np.abs(centers_sec - anchor_sec) <= window_sec)[0]
    if in_window.size == 0:
        return round(candidate_sec, 4)

    candidate_idx = int(np.argmin(np.abs(centers_sec - candidate_sec)))
    candidate_voiced = float(db[candidate_idx]) > voiced_db_threshold and float(zcr[candidate_idx]) > voiced_zcr_threshold
    if not candidate_voiced:
        return round(candidate_sec, 4)

    safe_pool = []
    for raw_idx in in_window:
        idx = int(raw_idx)
        frame_voiced = float(db[idx]) > voiced_db_threshold and float(zcr[idx]) > voiced_zcr_threshold
        if frame_voiced:
            continue
        prev_idx = max(0, idx - 1)
        next_idx = min(len(db) - 1, idx + 1)
        slope = abs(float(db[next_idx]) - float(db[prev_idx]))
        distance = abs(float(centers_sec[idx]) - candidate_sec)
        safe_pool.append((slope, distance, idx))

    if not safe_pool:
        return round(candidate_sec, 4)

    safe_pool.sort(key=lambda item: (item[0], item[1]))
    best_idx = safe_pool[0][2]
    return round(float(centers_sec[best_idx]), 4)


def select_punctuation_ordered_pause(
    *,
    runs: list[dict[str, float]],
    anchor_sec: float,
    expected_pause_rank: int,
    punctuation_sequence: list[str] | None = None,
) -> tuple[float, dict[str, Any]]:
    if not runs:
        return round(anchor_sec, 4), {
            "usedPunctuationOrdering": True,
            "selectedRank": None,
            "expectedRank": expected_pause_rank,
            "runCount": 0,
            "reason": "No qualifying silence runs in search range.",
        }

    desired_rank = max(1, expected_pause_rank)
    selected_index = min(desired_rank - 1, len(runs) - 1)
    selected_run = runs[selected_index]
    selected_center = float(selected_run["centerSec"])
    strategy = "rank"

    sequence = punctuation_sequence or []
    if sequence and sequence[-1] in ".!?":
        tail_runs = runs[selected_index:]
        best_terminal = max(
            tail_runs,
            key=lambda run: (
                float(run["centerSec"]),
                float(run["durationSec"]),
                -abs(float(run["centerSec"]) - anchor_sec),
            ),
        )
        selected_run = best_terminal
        selected_center = float(best_terminal["centerSec"])
        selected_index = runs.index(best_terminal)
        strategy = "terminal-late-pause"

    return round(selected_center, 4), {
        "usedPunctuationOrdering": True,
        "selectedRank": selected_index + 1,
        "expectedRank": desired_rank,
        "runCount": len(runs),
        "strategy": strategy,
        "punctuationSequence": sequence,
        "selectedRun": selected_run,
    }


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_segments_input(segments_json: str) -> list[dict[str, Any]]:
    candidate_path = Path(segments_json)
    if candidate_path.exists():
        raw_text = candidate_path.read_text(encoding="utf-8")
    else:
        raw_text = segments_json

    payload = json.loads(raw_text)
    if not isinstance(payload, list) or len(payload) < 1:
        raise ValueError("segments-json must be a JSON array with at least 1 segment.")

    segments: list[dict[str, Any]] = []
    for idx, item in enumerate(payload):
        character = None
        text = None
        line_id = None

        if isinstance(item, dict):
            character = item.get("character") or item.get("characterName")
            text = item.get("text")
            line_id = item.get("lineId") if isinstance(item.get("lineId"), int) else item.get("id")
        elif isinstance(item, list) and len(item) >= 2:
            character = item[0]
            text = item[1]

        if not isinstance(character, str) or not character.strip():
            raise ValueError(f"Segment {idx + 1} is missing a valid character name.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Segment {idx + 1} is missing valid text.")

        segments.append(
            {
                "index": idx,
                "character": character.strip(),
                "text": text.strip(),
                "lineId": line_id if isinstance(line_id, int) else None,
            }
        )
    return segments


def normalize_character_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def candidate_character_keys(value: str) -> list[str]:
    base = value.strip().lower()
    keys = {
        normalize_character_key(base),
        normalize_character_key(base.replace("the ", "")),
        normalize_character_key(base.replace("the-", "")),
        normalize_character_key(base.replace("the_", "")),
    }
    return [item for item in keys if item]


def find_characters_json(chapter_dir: Path) -> Path | None:
    direct = chapter_dir.parent / "characters.json"
    if direct.exists() and direct.is_file():
        return direct.resolve()

    for parent in chapter_dir.parents:
        candidate = parent / "characters.json"
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def resolve_sample_from_characters_json(chapter_dir: Path, main_character_name: str) -> Path | None:
    characters_json = find_characters_json(chapter_dir)
    if characters_json is None:
        return None

    try:
        payload = json.loads(characters_json.read_text(encoding="utf-8"))
    except Exception:
        return None

    characters = payload.get("characters")
    if not isinstance(characters, list):
        return None

    target_keys = set(candidate_character_keys(main_character_name))
    if not target_keys:
        return None

    chosen_voice_id = None
    for character in characters:
        if not isinstance(character, dict):
            continue

        match_keys: set[str] = set()

        for field in [character.get("id"), character.get("name")]:
            if isinstance(field, str) and field.strip():
                match_keys.update(candidate_character_keys(field))

        aliases = character.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    match_keys.update(candidate_character_keys(alias))

        if not (target_keys & match_keys):
            continue

        voice_id = character.get("voiceId")
        provider = character.get("provider")
        if isinstance(voice_id, str) and voice_id.strip() and provider == "vibevoice_local":
            chosen_voice_id = voice_id.strip()
            break

    if not chosen_voice_id:
        return None

    for parent in [chapter_dir.parent, *chapter_dir.parents]:
        samples_dir = parent / "Audio Samples"
        if not samples_dir.exists() or not samples_dir.is_dir():
            continue
        candidate = (samples_dir / chosen_voice_id).resolve()
        if candidate.exists() and candidate.is_file():
            return candidate

    return None


def find_audio_sample_file(chapter_dir: Path, main_character_name: str) -> Path | None:
    target_keys = candidate_character_keys(main_character_name)
    for parent in [chapter_dir, *chapter_dir.parents]:
        sample_dir = parent / "Audio Samples"
        if not sample_dir.exists() or not sample_dir.is_dir():
            continue

        candidates = sorted(sample_dir.glob("*.wav"))
        if not candidates:
            continue

        exact_matches = []
        partial_matches = []
        for candidate in candidates:
            stem_key = normalize_character_key(candidate.stem)
            if any(stem_key == key for key in target_keys):
                exact_matches.append(candidate)
                continue
            if any(key in stem_key or stem_key in key for key in target_keys):
                partial_matches.append(candidate)

        if exact_matches:
            return exact_matches[0].resolve()
        if partial_matches:
            return partial_matches[0].resolve()

    return None


def resolve_sample_path(
    *,
    sample_path: Path | None,
    chapter_dir: Path | None,
    main_character_name: str,
) -> Path:
    if sample_path is not None:
        resolved = sample_path.resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Sample path not found: {resolved}")
        return resolved

    if chapter_dir is None:
        raise ValueError("Provide --sample-path or --chapter-dir to resolve a sample by character name.")

    configured_sample = resolve_sample_from_characters_json(chapter_dir, main_character_name)
    if configured_sample is not None:
        return configured_sample

    preferred_sample = find_audio_sample_file(chapter_dir, main_character_name)
    if preferred_sample is not None:
        return preferred_sample

    audio_lines_dir = (chapter_dir / "audio_lines").resolve()
    if not audio_lines_dir.exists():
        raise FileNotFoundError(f"audio_lines folder not found under chapter dir: {audio_lines_dir}")

    target_keys = candidate_character_keys(main_character_name)
    character_dirs = [child for child in audio_lines_dir.iterdir() if child.is_dir()]
    matched_dir = None
    for directory in character_dirs:
        directory_key = normalize_character_key(directory.name)
        if any(directory_key == key for key in target_keys):
            matched_dir = directory
            break
    if matched_dir is None:
        raise FileNotFoundError(f"Could not find character folder for main character: {main_character_name}")

    wav_files = sorted(matched_dir.glob("*.wav"))
    if not wav_files:
        raise FileNotFoundError(f"No wav files found in character folder: {matched_dir}")

    return wav_files[0].resolve()


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned or "segment"


def write_segment_splits(
    *,
    merged_audio_path: Path,
    output_dir: Path,
    segments: list[dict[str, Any]],
    boundaries: list[float],
    main_character_name: str,
) -> dict[str, Any]:
    audio, sample_rate = sf.read(str(merged_audio_path), dtype="float32", always_2d=True)
    total_samples = audio.shape[0]

    splits_dir = ensure_output_dir(output_dir / "splits")
    segment_files: list[dict[str, Any]] = []
    main_chunks: list[np.ndarray] = []

    starts = [0.0] + boundaries
    ends = boundaries + [float(total_samples) / float(sample_rate)]

    for index, segment in enumerate(segments):
        start_sec = starts[index]
        end_sec = ends[index]

        start_sample = int(round(start_sec * sample_rate))
        end_sample = int(round(end_sec * sample_rate))
        start_sample = max(0, min(total_samples, start_sample))
        end_sample = max(start_sample, min(total_samples, end_sample))

        chunk = audio[start_sample:end_sample]
        file_prefix = segment.get("lineId") if isinstance(segment.get("lineId"), int) else index + 1
        file_name = f"{file_prefix}-{safe_name(str(segment['character']))}.wav"
        file_path = splits_dir / file_name
        sf.write(str(file_path), chunk, sample_rate)

        if normalize_character_key(str(segment["character"])) == normalize_character_key(main_character_name):
            main_chunks.append(chunk)

        segment_files.append(
            {
                "index": index + 1,
                "lineId": segment.get("lineId") if isinstance(segment.get("lineId"), int) else None,
                "character": segment["character"],
                "text": segment["text"],
                "audioPath": str(file_path),
                "startSec": round(start_sec, 4),
                "endSec": round(end_sec, 4),
                "durationSec": round(end_sec - start_sec, 4),
            }
        )

    main_character_audio = None
    if main_chunks:
        combined = np.concatenate(main_chunks, axis=0)
        main_path = output_dir / f"main_character_{safe_name(main_character_name)}.wav"
        sf.write(str(main_path), combined, sample_rate)
        main_character_audio = str(main_path)

    return {
        "splitsDir": str(splits_dir),
        "segmentFiles": segment_files,
        "mainCharacterAudio": main_character_audio,
    }


def compute_refined_boundaries(
    *,
    segments: list[dict[str, Any]],
    duration_sec: float,
    args: argparse.Namespace,
    audio_path: Path,
) -> tuple[list[float], list[float], list[dict[str, Any]], dict[str, Any]]:
    segment_units = [
        estimate_segment_units(
            str(segment["text"]),
            punctuation_char_weight=args.punctuation_char_weight,
            terminal_punctuation_bonus=args.terminal_punctuation_bonus,
        )
        for segment in segments
    ]

    total_units = sum(float(item["weightedUnits"]) for item in segment_units)
    if total_units <= 0.0:
        raise ValueError("Heuristic units are invalid; cannot estimate boundaries.")

    anchor_boundaries: list[float] = []
    running_units = 0.0
    for unit in segment_units[:-1]:
        running_units += float(unit["weightedUnits"])
        anchor_boundaries.append(round(duration_sec * (running_units / total_units), 4))

    refined_boundaries = anchor_boundaries[:]
    refinement_records: list[dict[str, Any]] = []
    boundary_refinement: dict[str, Any] = {
        "enabled": bool(args.pause_snap),
        "applied": False,
        "method": "none",
    }

    if args.pause_snap and anchor_boundaries:
        audio, sample_rate = load_audio_mono_float(audio_path)
        if audio is not None and sample_rate is not None:
            centers_sec, db = build_rms_envelope_db(audio, sample_rate, frame_ms=args.pause_snap_frame_ms)
            zcr = build_zero_crossing_rate(audio, sample_rate, frame_ms=args.pause_snap_frame_ms)
            window_sec = args.pause_snap_window_ms / 1000.0
            min_gap_sec = args.pause_snap_min_gap_ms / 1000.0

            for idx, anchor in enumerate(anchor_boundaries):
                prev_boundary = 0.0 if idx == 0 else refined_boundaries[idx - 1]
                left_text = str(segments[idx]["text"])
                punctuation_sequence = re.findall(r"[,:;.!?]", left_text)
                expected_rank = max(1, len(punctuation_sequence))

                runs = collect_silence_runs(
                    centers_sec=centers_sec,
                    db=db,
                    start_sec=max(0.0, prev_boundary + min_gap_sec, anchor - window_sec),
                    end_sec=min(duration_sec, anchor + window_sec),
                    silence_enter_db=args.pause_snap_silence_enter_db,
                    silence_exit_db=args.pause_snap_silence_exit_db,
                    min_silence_ms=args.pause_snap_min_silence_ms,
                    frame_ms=args.pause_snap_frame_ms,
                )

                selected_pause, punctuation_meta = select_punctuation_ordered_pause(
                    runs=runs,
                    anchor_sec=anchor,
                    expected_pause_rank=expected_rank,
                    punctuation_sequence=punctuation_sequence,
                )
                safe_pause = guard_boundary_against_voiced_region(
                    candidate_sec=selected_pause,
                    anchor_sec=anchor,
                    centers_sec=centers_sec,
                    db=db,
                    zcr=zcr,
                    window_ms=args.pause_snap_window_ms,
                    voiced_db_threshold=args.pause_snap_voiced_db,
                    voiced_zcr_threshold=args.pause_snap_voiced_zcr,
                )

                if idx == 0 and not runs:
                    short_text = len(left_text.split()) <= 4
                    comma_ended = left_text.strip().endswith(",")
                    if short_text and comma_ended:
                        safe_pause = round(safe_pause + (args.short_comma_no_pause_bias_ms / 1000.0), 4)

                if left_text.strip().endswith((".", "!", "?")):
                    safe_pause = round(safe_pause + (args.narration_end_pad_ms / 1000.0), 4)

                upper_bound = duration_sec if idx == len(anchor_boundaries) - 1 else anchor_boundaries[idx + 1]
                safe_pause = clamp(safe_pause, prev_boundary + min_gap_sec, max(prev_boundary + min_gap_sec, upper_bound))
                refined_boundaries[idx] = round(safe_pause, 4)

                refinement_records.append(
                    {
                        "boundaryIndex": idx + 1,
                        "afterSegmentCharacter": segments[idx]["character"],
                        "anchorSec": anchor,
                        "snappedSec": refined_boundaries[idx],
                        "candidateRuns": runs,
                        "punctuation": {
                            "sequence": punctuation_sequence,
                            "selected": punctuation_meta,
                        },
                    }
                )

            boundary_refinement = {
                "enabled": True,
                "applied": True,
                "method": "pause-energy-snap",
                "params": {
                    "windowMs": args.pause_snap_window_ms,
                    "frameMs": args.pause_snap_frame_ms,
                    "silenceEnterDb": args.pause_snap_silence_enter_db,
                    "silenceExitDb": args.pause_snap_silence_exit_db,
                    "minSilenceMs": args.pause_snap_min_silence_ms,
                    "minGapMs": args.pause_snap_min_gap_ms,
                    "voicedDb": args.pause_snap_voiced_db,
                    "voicedZcr": args.pause_snap_voiced_zcr,
                    "sentencePadMs": args.narration_end_pad_ms,
                    "shortCommaNoPauseBiasMs": args.short_comma_no_pause_bias_ms,
                },
                "boundaries": refinement_records,
            }

    return anchor_boundaries, refined_boundaries, segment_units, boundary_refinement


def cmd_heuristic_split(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    merged_text = " ".join(
        [
            args.segment_a_text.strip(),
            args.narration_text.strip(),
            args.segment_b_text.strip(),
        ]
    ).strip()

    if args.merged_audio:
        merged_audio = args.merged_audio.resolve()
        if not merged_audio.exists():
            raise FileNotFoundError(f"Merged audio not found: {merged_audio}")
    else:
        if not args.sample_path:
            raise ValueError("Provide --merged-audio, or provide --sample-path to generate merged audio.")
        merged_audio = (output_dir / "merged_character_full.wav").resolve()
        print(f"Generating merged clip: {merged_audio}")
        generate_vibevoice_audio(
            text=merged_text,
            sample_path=args.sample_path.resolve(),
            output_audio=merged_audio,
            model_path=args.vibevoice_model_path,
            repo_path=args.vibevoice_repo_path,
            device=args.vibevoice_device,
            ddpm_steps=args.vibevoice_ddpm_steps,
            cfg_scale=args.vibevoice_cfg_scale,
            seed=args.vibevoice_seed,
        )

    duration_sec = round(get_audio_duration_seconds(merged_audio), 4)

    units_a = estimate_segment_units(
        args.segment_a_text,
        punctuation_char_weight=args.punctuation_char_weight,
        terminal_punctuation_bonus=args.terminal_punctuation_bonus,
    )
    units_n = estimate_segment_units(
        args.narration_text,
        punctuation_char_weight=args.punctuation_char_weight,
        terminal_punctuation_bonus=args.terminal_punctuation_bonus,
    )
    units_b = estimate_segment_units(
        args.segment_b_text,
        punctuation_char_weight=args.punctuation_char_weight,
        terminal_punctuation_bonus=args.terminal_punctuation_bonus,
    )

    total_units = (
        float(units_a["weightedUnits"])
        + float(units_n["weightedUnits"])
        + float(units_b["weightedUnits"])
    )
    if total_units <= 0.0:
        raise ValueError("Heuristic units are invalid; cannot estimate boundaries.")

    seg_a_end_anchor = round(duration_sec * (float(units_a["weightedUnits"]) / total_units), 4)
    narr_end_anchor = round(
        seg_a_end_anchor + duration_sec * (float(units_n["weightedUnits"]) / total_units),
        4,
    )
    narr_end_anchor = min(narr_end_anchor, duration_sec)

    refined_seg_a_end = seg_a_end_anchor
    refined_narr_end = narr_end_anchor
    boundary_refinement: dict[str, Any] = {
        "enabled": bool(args.pause_snap),
        "applied": False,
        "method": "none",
    }

    if args.pause_snap:
        audio, sample_rate = load_audio_mono_float(merged_audio)
        if audio is not None and sample_rate is not None:
            centers_sec, db = build_rms_envelope_db(audio, sample_rate, frame_ms=args.pause_snap_frame_ms)
            zcr = build_zero_crossing_rate(audio, sample_rate, frame_ms=args.pause_snap_frame_ms)
            window_sec = args.pause_snap_window_ms / 1000.0
            min_gap_sec = args.pause_snap_min_gap_ms / 1000.0

            first_runs = collect_silence_runs(
                centers_sec=centers_sec,
                db=db,
                start_sec=max(0.0, seg_a_end_anchor - window_sec),
                end_sec=min(duration_sec, seg_a_end_anchor + window_sec),
                silence_enter_db=args.pause_snap_silence_enter_db,
                silence_exit_db=args.pause_snap_silence_exit_db,
                min_silence_ms=args.pause_snap_min_silence_ms,
                frame_ms=args.pause_snap_frame_ms,
            )
            snapped_first = choose_pause_near_anchor(first_runs, seg_a_end_anchor)
            snapped_first = guard_boundary_against_voiced_region(
                candidate_sec=snapped_first,
                anchor_sec=seg_a_end_anchor,
                centers_sec=centers_sec,
                db=db,
                zcr=zcr,
                window_ms=args.pause_snap_window_ms,
                voiced_db_threshold=args.pause_snap_voiced_db,
                voiced_zcr_threshold=args.pause_snap_voiced_zcr,
            )

            punctuation_sequence = re.findall(r"[,:;.!?]", args.narration_text)
            expected_rank = max(1, len(punctuation_sequence))
            narration_runs = collect_silence_runs(
                centers_sec=centers_sec,
                db=db,
                start_sec=max(0.0, snapped_first + min_gap_sec),
                end_sec=min(duration_sec, narr_end_anchor + window_sec),
                silence_enter_db=args.pause_snap_silence_enter_db,
                silence_exit_db=args.pause_snap_silence_exit_db,
                min_silence_ms=args.pause_snap_min_silence_ms,
                frame_ms=args.pause_snap_frame_ms,
            )
            selected_pause, punctuation_meta = select_punctuation_ordered_pause(
                runs=narration_runs,
                anchor_sec=narr_end_anchor,
                expected_pause_rank=expected_rank,
                punctuation_sequence=punctuation_sequence,
            )

            snapped_second = guard_boundary_against_voiced_region(
                candidate_sec=selected_pause,
                anchor_sec=narr_end_anchor,
                centers_sec=centers_sec,
                db=db,
                zcr=zcr,
                window_ms=args.pause_snap_window_ms,
                voiced_db_threshold=args.pause_snap_voiced_db,
                voiced_zcr_threshold=args.pause_snap_voiced_zcr,
            )
            snapped_second = round(snapped_second + (args.narration_end_pad_ms / 1000.0), 4)

            snapped_first = clamp(snapped_first, 0.0, max(0.0, duration_sec - min_gap_sec))
            snapped_second = clamp(snapped_second, min_gap_sec, duration_sec)
            if snapped_second < snapped_first + min_gap_sec:
                snapped_second = min(duration_sec, snapped_first + min_gap_sec)

            refined_seg_a_end = round(snapped_first, 4)
            refined_narr_end = round(snapped_second, 4)

            boundary_refinement = {
                "enabled": True,
                "applied": True,
                "method": "pause-energy-snap",
                "params": {
                    "windowMs": args.pause_snap_window_ms,
                    "frameMs": args.pause_snap_frame_ms,
                    "silenceEnterDb": args.pause_snap_silence_enter_db,
                    "silenceExitDb": args.pause_snap_silence_exit_db,
                    "minSilenceMs": args.pause_snap_min_silence_ms,
                    "minGapMs": args.pause_snap_min_gap_ms,
                    "voicedDb": args.pause_snap_voiced_db,
                    "voicedZcr": args.pause_snap_voiced_zcr,
                    "narrationEndPadMs": args.narration_end_pad_ms,
                    "usePunctuationOrder": True,
                },
                "anchors": {
                    "afterSegmentA": seg_a_end_anchor,
                    "afterNarration": narr_end_anchor,
                },
                "snappedBoundaries": {
                    "afterSegmentA": refined_seg_a_end,
                    "afterNarration": refined_narr_end,
                },
                "punctuationGuidance": {
                    "enabled": True,
                    "expectedNarrationPauseCount": len(punctuation_sequence),
                    "searchRange": {
                        "startSec": round(max(0.0, snapped_first + min_gap_sec), 4),
                        "endSec": round(min(duration_sec, narr_end_anchor + window_sec), 4),
                        "candidateRuns": narration_runs,
                    },
                    "selected": punctuation_meta,
                },
            }

    segment_a_range = {
        "label": "segment_a",
        "startSec": 0.0,
        "endSec": refined_seg_a_end,
        "durationSec": round(refined_seg_a_end, 4),
    }
    narration_range = {
        "startSec": refined_seg_a_end,
        "endSec": refined_narr_end,
        "durationSec": round(max(0.0, refined_narr_end - refined_seg_a_end), 4),
    }
    segment_b_range = {
        "label": "segment_b",
        "startSec": refined_narr_end,
        "endSec": duration_sec,
        "durationSec": round(max(0.0, duration_sec - refined_narr_end), 4),
    }

    report = {
        "formatVersion": "asr-experiment/heuristic-split/v1",
        "createdAt": utc_now_iso(),
        "inputs": {
            "mergedAudio": str(merged_audio),
            "segmentAText": args.segment_a_text,
            "narrationText": args.narration_text,
            "segmentBText": args.segment_b_text,
            "audioDurationSec": duration_sec,
        },
        "heuristics": {
            "method": "punctuation-plus-char-count",
            "params": {
                "punctuationCharWeight": args.punctuation_char_weight,
                "terminalPunctuationBonus": args.terminal_punctuation_bonus,
            },
            "segmentUnits": {
                "segmentA": units_a,
                "narration": units_n,
                "segmentB": units_b,
                "totalWeightedUnits": round(total_units, 4),
            },
        },
        "splitSuggestion": {
            "characterRanges": [segment_a_range, segment_b_range],
            "removedNarrationRange": narration_range,
            "boundaryRefinement": boundary_refinement,
        },
        "viability": {
            "isViable": narration_range["durationSec"] > 0.0,
            "reason": (
                "Heuristic estimate generated without ASR."
                if narration_range["durationSec"] > 0.0
                else "Heuristic estimate failed to allocate narration duration."
            ),
        },
    }

    report_path = output_dir / "heuristic_split_experiment.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote: {report_path}")


def cmd_multi_heuristic_split(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    segments = parse_segments_input(args.segments_json)

    chapter_dir = args.chapter_dir.resolve() if args.chapter_dir else None
    sample = resolve_sample_path(
        sample_path=(args.sample_path.resolve() if args.sample_path else None),
        chapter_dir=chapter_dir,
        main_character_name=args.main_character_name,
    )

    merged_text = " ".join(str(segment["text"]).strip() for segment in segments).strip()
    if args.merged_audio:
        merged_audio = args.merged_audio.resolve()
        if not merged_audio.exists():
            raise FileNotFoundError(f"Merged audio not found: {merged_audio}")
    else:
        merged_audio = (output_dir / "merged_character_full.wav").resolve()
        print(f"Generating merged clip: {merged_audio}")
        generate_vibevoice_audio(
            text=merged_text,
            sample_path=sample,
            output_audio=merged_audio,
            model_path=args.vibevoice_model_path,
            repo_path=args.vibevoice_repo_path,
            device=args.vibevoice_device,
            ddpm_steps=args.vibevoice_ddpm_steps,
            cfg_scale=args.vibevoice_cfg_scale,
            seed=args.vibevoice_seed,
        )

    duration_sec = round(get_audio_duration_seconds(merged_audio), 4)
    anchors, refined_boundaries, segment_units, boundary_refinement = compute_refined_boundaries(
        segments=segments,
        duration_sec=duration_sec,
        args=args,
        audio_path=merged_audio,
    )

    split_artifacts = write_segment_splits(
        merged_audio_path=merged_audio,
        output_dir=output_dir,
        segments=segments,
        boundaries=refined_boundaries,
        main_character_name=args.main_character_name,
    )

    starts = [0.0] + refined_boundaries
    ends = refined_boundaries + [duration_sec]
    split_segments = []
    for idx, segment in enumerate(segments):
        split_segments.append(
            {
                "index": idx + 1,
                "lineId": segment.get("lineId") if isinstance(segment.get("lineId"), int) else None,
                "character": segment["character"],
                "text": segment["text"],
                "startSec": round(starts[idx], 4),
                "endSec": round(ends[idx], 4),
                "durationSec": round(ends[idx] - starts[idx], 4),
            }
        )

    report = {
        "formatVersion": "asr-experiment/heuristic-multi-split/v1",
        "createdAt": utc_now_iso(),
        "inputs": {
            "mergedAudio": str(merged_audio),
            "mainCharacterName": args.main_character_name,
            "samplePath": str(sample),
            "segmentCount": len(segments),
            "audioDurationSec": duration_sec,
        },
        "segments": split_segments,
        "heuristics": {
            "method": "punctuation-plus-char-count",
            "params": {
                "punctuationCharWeight": args.punctuation_char_weight,
                "terminalPunctuationBonus": args.terminal_punctuation_bonus,
            },
            "segmentUnits": segment_units,
            "anchorBoundariesSec": anchors,
            "refinedBoundariesSec": refined_boundaries,
            "boundaryRefinement": boundary_refinement,
        },
        "artifacts": split_artifacts,
    }

    report_path = output_dir / "heuristic_multi_split_experiment.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote: {report_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Heuristic audio split experiment runner (no ASR).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    heuristic = subparsers.add_parser(
        "heuristic-split",
        help="Estimate split boundaries without ASR using punctuation, character counts, and pause analysis.",
    )
    heuristic.add_argument("--output-dir", type=Path, required=True)
    heuristic.add_argument("--segment-a-text", required=True)
    heuristic.add_argument("--narration-text", required=True)
    heuristic.add_argument("--segment-b-text", required=True)
    heuristic.add_argument("--merged-audio", type=Path, default=None)
    heuristic.add_argument("--sample-path", type=Path, default=None)
    heuristic.add_argument("--punctuation-char-weight", type=float, default=1.0)
    heuristic.add_argument("--terminal-punctuation-bonus", type=float, default=2.0)

    heuristic.add_argument("--pause-snap", action="store_true")
    heuristic.add_argument("--pause-snap-window-ms", type=int, default=350)
    heuristic.add_argument("--pause-snap-frame-ms", type=int, default=10)
    heuristic.add_argument("--pause-snap-silence-enter-db", type=float, default=-40.0)
    heuristic.add_argument("--pause-snap-silence-exit-db", type=float, default=-34.0)
    heuristic.add_argument("--pause-snap-min-silence-ms", type=int, default=180)
    heuristic.add_argument("--pause-snap-min-gap-ms", type=int, default=60)
    heuristic.add_argument("--pause-snap-voiced-db", type=float, default=-28.0)
    heuristic.add_argument("--pause-snap-voiced-zcr", type=float, default=0.06)
    heuristic.add_argument("--narration-end-pad-ms", type=int, default=140)
    heuristic.add_argument("--short-comma-no-pause-bias-ms", type=int, default=220)

    heuristic.add_argument("--vibevoice-model-path", default=None)
    heuristic.add_argument("--vibevoice-repo-path", default=None)
    heuristic.add_argument("--vibevoice-device", default=None)
    heuristic.add_argument("--vibevoice-ddpm-steps", type=int, default=20)
    heuristic.add_argument("--vibevoice-cfg-scale", type=float, default=1.3)
    heuristic.add_argument("--vibevoice-seed", type=int, default=None)

    multi = subparsers.add_parser(
        "multi-heuristic-split",
        help="Generate one merged clip from many [character,text] segments and split it back to per-segment audio.",
    )
    multi.add_argument("--output-dir", type=Path, required=True)
    multi.add_argument("--segments-json", required=True)
    multi.add_argument("--main-character-name", required=True)
    multi.add_argument("--chapter-dir", type=Path, default=None)
    multi.add_argument("--sample-path", type=Path, default=None)
    multi.add_argument("--merged-audio", type=Path, default=None)
    multi.add_argument("--punctuation-char-weight", type=float, default=1.0)
    multi.add_argument("--terminal-punctuation-bonus", type=float, default=2.0)

    multi.add_argument("--pause-snap", action="store_true")
    multi.add_argument("--pause-snap-window-ms", type=int, default=350)
    multi.add_argument("--pause-snap-frame-ms", type=int, default=10)
    multi.add_argument("--pause-snap-silence-enter-db", type=float, default=-40.0)
    multi.add_argument("--pause-snap-silence-exit-db", type=float, default=-34.0)
    multi.add_argument("--pause-snap-min-silence-ms", type=int, default=180)
    multi.add_argument("--pause-snap-min-gap-ms", type=int, default=60)
    multi.add_argument("--pause-snap-voiced-db", type=float, default=-28.0)
    multi.add_argument("--pause-snap-voiced-zcr", type=float, default=0.06)
    multi.add_argument("--narration-end-pad-ms", type=int, default=140)
    multi.add_argument("--short-comma-no-pause-bias-ms", type=int, default=220)

    multi.add_argument("--vibevoice-model-path", default=None)
    multi.add_argument("--vibevoice-repo-path", default=None)
    multi.add_argument("--vibevoice-device", default=None)
    multi.add_argument("--vibevoice-ddpm-steps", type=int, default=20)
    multi.add_argument("--vibevoice-cfg-scale", type=float, default=1.3)
    multi.add_argument("--vibevoice-seed", type=int, default=None)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "heuristic-split":
        cmd_heuristic_split(args)
        return
    if args.command == "multi-heuristic-split":
        cmd_multi_heuristic_split(args)
        return
    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
