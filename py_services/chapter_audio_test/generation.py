from __future__ import annotations

from pathlib import Path
import re
import time
from typing import Any

import numpy as np
import soundfile as sf

from .text_utils import normalize_chapter_text


def _split_text_to_max_chars(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue

        words = sentence.split()
        current: list[str] = []
        for word in words:
            candidate = " ".join([*current, word])
            if current and len(candidate) > max_chars:
                pieces.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            pieces.append(" ".join(current))

    packed: list[str] = []
    for piece in pieces:
        candidate = " ".join([*packed[-1:], piece]) if packed else piece
        if packed and len(candidate) <= max_chars:
            packed[-1] = candidate
        else:
            packed.append(piece)
    return packed


def _format_hms(total_seconds: float | None) -> str:
    if total_seconds is None:
        return "--:--:--"

    safe_seconds = max(0, int(round(float(total_seconds))))
    hours, remainder = divmod(safe_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _render_progress_bar(fraction: float, *, width: int = 24) -> str:
    clamped = max(0.0, min(1.0, float(fraction)))
    filled = int(round(clamped * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def build_line_chunk_generation_plan(
    *,
    chapter_text: str,
    min_chunk_chars: int,
    max_chunk_chars: int | None = None,
) -> list[dict[str, Any]]:
    if min_chunk_chars < 1:
        raise ValueError("--line-chunk-min-chars must be >= 1")
    if max_chunk_chars is not None and max_chunk_chars < min_chunk_chars:
        raise ValueError(
            "--line-chunk-max-chars must be greater than or equal to "
            "--line-chunk-min-chars"
        )

    normalized_lines: list[tuple[int, str]] = []
    for line_number, raw_line in enumerate(chapter_text.splitlines(), start=1):
        normalized_line = normalize_chapter_text(raw_line)
        if normalized_line:
            pieces = (
                _split_text_to_max_chars(normalized_line, max_chunk_chars)
                if max_chunk_chars is not None
                else [normalized_line]
            )
            normalized_lines.extend((line_number, piece) for piece in pieces)

    if not normalized_lines:
        raise ValueError(
            "No non-empty lines found in chapter text for chunk generation.")

    plan: list[dict[str, Any]] = []
    pending_lines: list[str] = []
    pending_start_line: int | None = None
    pending_end_line: int | None = None
    pending_char_count = 0

    def flush_pending_lines() -> None:
        nonlocal pending_lines
        nonlocal pending_start_line
        nonlocal pending_end_line
        nonlocal pending_char_count

        if not pending_lines or pending_start_line is None or pending_end_line is None:
            return

        chunk_text = " ".join(pending_lines)
        plan.append(
            {
                "chunkIndex": len(plan) + 1,
                "startLine": pending_start_line,
                "endLine": pending_end_line,
                "lineCount": len(pending_lines),
                "charCount": len(chunk_text),
                "text": chunk_text,
            }
        )

        pending_lines = []
        pending_start_line = None
        pending_end_line = None
        pending_char_count = 0

    for line_number, line_text in normalized_lines:
        next_char_count = pending_char_count + len(line_text) + (1 if pending_lines else 0)
        if (
            max_chunk_chars is not None
            and pending_lines
            and next_char_count > max_chunk_chars
        ):
            flush_pending_lines()

        if pending_start_line is None:
            pending_start_line = line_number
        pending_end_line = line_number
        pending_char_count += len(line_text) + (1 if pending_lines else 0)
        pending_lines.append(line_text)

        # Keep appending lines until we hit the minimum target size.
        if pending_char_count >= min_chunk_chars:
            flush_pending_lines()

    if pending_lines:
        tail_text = " ".join(pending_lines)
        tail_char_count = len(tail_text)

        merged_char_count = (
            int(plan[-1]["charCount"]) + 1 + tail_char_count if plan else 0
        )
        if (
            plan
            and tail_char_count < min_chunk_chars
            and (max_chunk_chars is None or merged_char_count <= max_chunk_chars)
        ):
            previous_chunk = plan[-1]
            merged_text = f"{previous_chunk['text']} {tail_text}".strip()
            previous_chunk["text"] = merged_text
            previous_chunk["endLine"] = pending_end_line
            previous_chunk["lineCount"] = int(
                previous_chunk["lineCount"]) + len(pending_lines)
            previous_chunk["charCount"] = len(merged_text)
        else:
            flush_pending_lines()

    for index, chunk in enumerate(plan, start=1):
        chunk["chunkIndex"] = index

    return plan


def stitch_generated_chunk_audio(
    *,
    chunk_audio_paths: list[Path],
    output_path: Path,
    join_pause_ms: int,
) -> dict[str, Any]:
    if not chunk_audio_paths:
        raise ValueError("No chunk audio paths provided for stitching.")

    first_chunk_path = chunk_audio_paths[0]
    first_audio, sample_rate = sf.read(
        str(first_chunk_path), dtype="float32", always_2d=True)
    if not isinstance(first_audio, np.ndarray) or first_audio.size == 0:
        raise ValueError(f"Chunk audio is empty: {first_chunk_path}")

    first_info = sf.info(str(first_chunk_path))
    channels = int(first_audio.shape[1])
    pause_samples = max(
        0, int(round((max(0, join_pause_ms) / 1000.0) * float(sample_rate))))
    pause_audio = np.zeros((pause_samples, channels),
                           dtype=np.float32) if pause_samples > 0 else None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = 0

    with sf.SoundFile(
        str(output_path),
        mode="w",
        samplerate=int(sample_rate),
        channels=channels,
        format=first_info.format,
        subtype=first_info.subtype,
    ) as sink:
        sink.write(first_audio)
        total_frames += int(first_audio.shape[0])

        for index, chunk_path in enumerate(chunk_audio_paths[1:], start=2):
            chunk_audio, chunk_rate = sf.read(
                str(chunk_path), dtype="float32", always_2d=True)
            if not isinstance(chunk_audio, np.ndarray) or chunk_audio.size == 0:
                raise ValueError(f"Chunk audio is empty: {chunk_path}")
            if int(chunk_rate) != int(sample_rate):
                raise ValueError(
                    "Chunk sample rate mismatch while stitching: "
                    f"chunk {index} has {chunk_rate}, expected {sample_rate}."
                )
            if int(chunk_audio.shape[1]) != channels:
                raise ValueError(
                    "Chunk channel count mismatch while stitching: "
                    f"chunk {index} has {chunk_audio.shape[1]}, expected {channels}."
                )

            if pause_audio is not None:
                sink.write(pause_audio)
                total_frames += pause_samples

            sink.write(chunk_audio)
            total_frames += int(chunk_audio.shape[0])

    return {
        "sampleRate": int(sample_rate),
        "channels": channels,
        "totalFrames": total_frames,
        "durationSec": round(total_frames / float(sample_rate), 4),
        "joinPauseMs": max(0, int(join_pause_ms)),
    }


def generate_line_chunked_chapter_audio(
    *,
    service: Any,
    chunk_plan: list[dict[str, Any]],
    sample_path: Path,
    output_path: Path,
    chunks_dir: Path,
    min_chunk_chars: int,
    seed: int | None,
    join_pause_ms: int,
    resume_existing_chunks: bool = False,
) -> dict[str, Any]:
    if not chunk_plan:
        raise ValueError("Chunk generation plan is empty.")

    chunks_dir.mkdir(parents=True, exist_ok=True)
    if not resume_existing_chunks:
        for stale_file in chunks_dir.glob("*.wav"):
            if stale_file.is_file():
                stale_file.unlink()

    chunk_audio_paths: list[Path] = []
    chunk_reports: list[dict[str, Any]] = []
    total_chunks = len(chunk_plan)
    reused_chunk_count = 0
    generation_started_at = time.perf_counter()

    for chunk in chunk_plan:
        chunk_index = int(chunk["chunkIndex"])
        start_line = int(chunk["startLine"])
        end_line = int(chunk["endLine"])
        chunk_output_path = chunks_dir / \
            f"{chunk_index:04d}-line-{start_line}-to-{end_line}.wav"
        chunk_seed = seed
        chunk_started_at = time.perf_counter()

        if resume_existing_chunks and chunk_output_path.exists():
            chunk_info = sf.info(str(chunk_output_path))
            if chunk_info.frames <= 0 or chunk_info.samplerate <= 0:
                raise ValueError(
                    f"Existing chunk audio is invalid and cannot be resumed: {chunk_output_path}"
                )
            reused_chunk_count += 1
            chunk_duration_sec = float(chunk_info.duration)
            chunk_audio_paths.append(chunk_output_path)
            chunk_reports.append(
                {
                    "chunkIndex": chunk_index,
                    "startLine": start_line,
                    "endLine": end_line,
                    "lineCount": int(chunk["lineCount"]),
                    "charCount": int(chunk["charCount"]),
                    "durationSec": round(chunk_duration_sec, 4),
                    "audioPath": str(chunk_output_path),
                    "seed": chunk_seed,
                    "reused": True,
                }
            )
            print(
                "Reusing chunk "
                f"{chunk_index}/{total_chunks} "
                f"(lines {start_line}-{end_line}, {int(chunk['charCount'])} chars)"
            )
            continue

        print(
            "Generating chunk "
            f"{chunk_index}/{total_chunks} "
            f"(lines {start_line}-{end_line}, {int(chunk['charCount'])} chars)"
        )
        service.generate_audio(
            text=str(chunk["text"]),
            sample_path=str(sample_path),
            output_path=str(chunk_output_path),
            seed=chunk_seed,
        )

        chunk_elapsed_sec = time.perf_counter() - chunk_started_at
        total_elapsed_sec = time.perf_counter() - generation_started_at
        remaining_chunks = total_chunks - chunk_index
        average_chunk_sec = total_elapsed_sec / float(chunk_index)
        eta_sec = average_chunk_sec * float(remaining_chunks)
        chunk_bar = _render_progress_bar(
            chunk_index / float(total_chunks),
            width=24,
        )
        print(
            "Chunk progress "
            f"{chunk_bar} {chunk_index}/{total_chunks} "
            f"| chunk {chunk_elapsed_sec:.1f}s "
            f"| elapsed {_format_hms(total_elapsed_sec)} "
            f"| ETA {_format_hms(eta_sec)}"
        )

        chunk_duration_sec = float(sf.info(str(chunk_output_path)).duration)
        chunk_audio_paths.append(chunk_output_path)
        chunk_reports.append(
            {
                "chunkIndex": chunk_index,
                "startLine": start_line,
                "endLine": end_line,
                "lineCount": int(chunk["lineCount"]),
                "charCount": int(chunk["charCount"]),
                "durationSec": round(chunk_duration_sec, 4),
                "audioPath": str(chunk_output_path),
                "seed": chunk_seed,
            }
        )

    stitch_report = stitch_generated_chunk_audio(
        chunk_audio_paths=chunk_audio_paths,
        output_path=output_path,
        join_pause_ms=join_pause_ms,
    )

    return {
        "mode": "line-chunked",
        "minChunkChars": int(min_chunk_chars),
        "chunkCount": total_chunks,
        "reusedChunkCount": reused_chunk_count,
        "chunksDir": str(chunks_dir.resolve()),
        "chunks": chunk_reports,
        "stitch": stitch_report,
    }
