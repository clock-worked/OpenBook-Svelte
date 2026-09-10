"""Batch full-chapter audio generation directly from chapter.txt files."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

from _path_setup import bootstrap_python_script_paths

bootstrap_python_script_paths(__file__)

from py_services.chapter_audio_test.generation import (
    build_line_chunk_generation_plan,
    generate_line_chunked_chapter_audio,
)
from py_services.vibevoice_local_service import VibeVoiceLocalService


CHAPTER_FOLDER_PATTERN = re.compile(r"^(?P<number>\d+)\s*-")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for full chapter batch generation."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate full chapter audio in numeric order from raw chapter.txt "
            "files, skipping chapters whose final WAV already exists."
        )
    )
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument("--sample-path", type=Path, required=True)
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument("--start-chapter", type=int, default=0)
    parser.add_argument("--end-chapter", type=int, default=None)
    parser.add_argument("--line-chunk-min-chars", type=int, default=800)
    parser.add_argument(
        "--line-chunk-max-chars",
        type=int,
        default=None,
        help="Split oversized lines at sentence or word boundaries to enforce this ceiling.",
    )
    parser.add_argument("--line-chunk-join-ms", type=int, default=0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--output-dir-name",
        default="audio_test_stephen_fry",
        help="Per-chapter output directory created under each chapter folder.",
    )
    parser.add_argument(
        "--output-filename-suffix",
        default="-stephen-fry-full.wav",
        help="Suffix appended to each chapter directory name for the final WAV.",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=None,
        help="Append-only progress log path. Defaults inside the book dir.",
    )
    parser.add_argument(
        "--summary-jsonl",
        type=Path,
        default=None,
        help="Append-only summary JSONL path. Defaults inside the book dir.",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Regenerate chapters even if the final WAV already exists.",
    )
    parser.add_argument(
        "--resume-existing-chunks",
        action="store_true",
        help="Reuse valid chunk WAVs whose filenames match the current chunk plan.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue later chapters after a failure instead of stopping the batch.",
    )
    parser.add_argument(
        "--attn-impl",
        choices=["auto", "sdpa", "flash_attention_2", "eager"],
        default="auto",
        help="Optional VibeVoice attention override via VIBEVOICE_ATTN_IMPL.",
    )
    return parser.parse_args()


def timestamp() -> str:
    """Return a log-friendly local timestamp."""

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def chapter_number(chapter_name: str) -> int | None:
    """Extract the numeric prefix from a chapter directory name."""

    match = CHAPTER_FOLDER_PATTERN.match(chapter_name)
    if match is None:
        return None
    return int(match.group("number"))


def discover_chapters(
    *,
    book_dir: Path,
    start_chapter: int,
    end_chapter: int | None,
) -> list[Path]:
    """Discover numeric chapter directories that contain chapter.txt."""

    chapters: list[tuple[int, Path]] = []
    for child in sorted(book_dir.iterdir()):
        if not child.is_dir():
            continue
        chapter_num = chapter_number(child.name)
        if chapter_num is None:
            continue
        if chapter_num < start_chapter:
            continue
        if end_chapter is not None and chapter_num > end_chapter:
            continue
        if not (child / "chapter.txt").exists():
            continue
        chapters.append((chapter_num, child))

    chapters.sort(key=lambda item: (item[0], item[1].name.lower()))
    return [item[1] for item in chapters]


def append_log(log_path: Path, message: str) -> None:
    """Print and append one timestamped progress line."""

    line = f"[{timestamp()}] {message}"
    print(line, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def append_summary(summary_path: Path, payload: dict[str, object]) -> None:
    """Append one JSONL summary record."""

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> int:
    """Run ordered full-chapter generation for the requested book range."""

    args = parse_args()
    book_dir = args.book_dir.resolve()
    sample_path = args.sample_path.resolve()

    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")
    if not sample_path.exists():
        raise FileNotFoundError(f"Sample audio not found: {sample_path}")

    if args.attn_impl != "auto":
        os.environ["VIBEVOICE_ATTN_IMPL"] = args.attn_impl

    log_path = args.log_path or (book_dir / "_full_chapter_audio_batch.log")
    summary_path = args.summary_jsonl or (book_dir / "_full_chapter_audio_batch.jsonl")

    chapter_dirs = discover_chapters(
        book_dir=book_dir,
        start_chapter=args.start_chapter,
        end_chapter=args.end_chapter,
    )
    if not chapter_dirs:
        append_log(log_path, "No chapter directories found for the requested range.")
        return 0

    total = len(chapter_dirs)
    pending: list[tuple[int, Path, Path, Path]] = []
    skipped = 0
    for index, chapter_dir in enumerate(chapter_dirs, start=1):
        output_dir = chapter_dir / args.output_dir_name
        full_audio_path = output_dir / f"{chapter_dir.name}{args.output_filename_suffix}"
        if (
            not args.overwrite_existing
            and full_audio_path.exists()
            and full_audio_path.stat().st_size > 0
        ):
            skipped += 1
            continue
        pending.append((index, chapter_dir, output_dir, full_audio_path))

    if not pending:
        append_log(
            log_path,
            f"No unfinished chapters found in requested range. Skipped {skipped} existing outputs.",
        )
        return 0

    first_index, first_chapter_dir, _, _ = pending[0]
    append_log(
        log_path,
        f"Starting full-chapter batch from {first_chapter_dir.name} ({first_index}/{total}); "
        f"{len(pending)} chapters pending, {skipped} skipped.",
    )

    service = VibeVoiceLocalService(device=args.device)
    failed = False

    for index, chapter_dir, output_dir, full_audio_path in pending:
        chunks_dir = output_dir / "generation_chunks"
        output_dir.mkdir(parents=True, exist_ok=True)
        started_at = time.monotonic()

        try:
            chapter_text = (chapter_dir / "chapter.txt").read_text(encoding="utf-8")
            chunk_plan = build_line_chunk_generation_plan(
                chapter_text=chapter_text,
                min_chunk_chars=args.line_chunk_min_chars,
                max_chunk_chars=args.line_chunk_max_chars,
            )
            start_message = (
                f"Starting {chapter_dir.name} ({index}/{total}): "
                f"{len(chunk_plan)} chunks -> {full_audio_path.name}"
            )
            append_log(
                log_path,
                start_message,
            )
            report = generate_line_chunked_chapter_audio(
                service=service,
                chunk_plan=chunk_plan,
                sample_path=sample_path,
                output_path=full_audio_path,
                chunks_dir=chunks_dir,
                min_chunk_chars=args.line_chunk_min_chars,
                seed=args.seed,
                join_pause_ms=args.line_chunk_join_ms,
                resume_existing_chunks=args.resume_existing_chunks,
            )
            runtime_sec = round(time.monotonic() - started_at, 1)
            audio_duration_sec = round(float(report["stitch"]["durationSec"]), 4)
            append_summary(
                summary_path,
                {
                    "chapter": chapter_dir.name,
                    "index": index,
                    "total": total,
                    "status": "completed",
                    "runtimeSec": runtime_sec,
                    "audioDurationSec": audio_duration_sec,
                    "chunkCount": len(chunk_plan),
                    "fullAudioPath": str(full_audio_path),
                    "chunksDir": str(chunks_dir),
                },
            )
            completed_message = (
                f"Completed {chapter_dir.name} in {runtime_sec}s "
                f"(audio {audio_duration_sec}s, {len(chunk_plan)} chunks)."
            )
            append_log(
                log_path,
                completed_message,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            failed = True
            runtime_sec = round(time.monotonic() - started_at, 1)
            append_summary(
                summary_path,
                {
                    "chapter": chapter_dir.name,
                    "index": index,
                    "total": total,
                    "status": "failed",
                    "runtimeSec": runtime_sec,
                    "error": str(exc),
                    "fullAudioPath": str(full_audio_path),
                    "chunksDir": str(chunks_dir),
                },
            )
            append_log(log_path, f"FAILED {chapter_dir.name} after {runtime_sec}s: {exc}")
            traceback.print_exc()
            if not args.continue_on_error:
                return 1

    if failed:
        append_log(log_path, "Full-chapter batch finished with one or more failures.")
        return 1

    append_log(log_path, "Full-chapter batch finished for all requested chapters.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
