"""Batch chapter audio generation with overall and per-chunk progress."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


CHAPTER_FOLDER_PATTERN = re.compile(r"^(?P<number>\d+)-")


@dataclass
class ChapterResult:
    chapter: str
    status: int
    runtime_sec: float
    flagged_count: int | None
    flagged_line_ids: list[int] | None
    report_path: str | None
    output_dir: str


class ProgressRenderer:
    """Render two live progress lines in-place when terminal supports ANSI."""

    def __init__(self) -> None:
        term_name = os.environ.get("TERM", "").strip().lower()
        self._enabled = bool(sys.stdout.isatty()) and term_name not in {"", "dumb"}
        self._rendered = False

    def _fit_line(self, value: str) -> str:
        if not self._enabled:
            return value

        width = max(40, shutil.get_terminal_size((120, 20)).columns - 1)
        if len(value) > width:
            return value[: max(0, width - 3)] + "..."
        return value.ljust(width)

    def update(self, overall_line: str, chunk_line: str) -> None:
        if not self._enabled:
            print(overall_line)
            print(chunk_line)
            return

        if self._rendered:
            sys.stdout.write("\x1b[2F")

        sys.stdout.write("\x1b[2K\r" + self._fit_line(overall_line) + "\n")
        sys.stdout.write("\x1b[2K\r" + self._fit_line(chunk_line) + "\n")
        sys.stdout.flush()
        self._rendered = True

    def end_block(self) -> None:
        if self._enabled and self._rendered:
            self._rendered = False
            # Keep the final rendered bars visible before regular print output resumes.
            sys.stdout.write("\n")
            sys.stdout.flush()

    def info(self, line: str) -> None:
        self.end_block()
        print(line)


def _format_hms(total_seconds: float | None) -> str:
    if total_seconds is None:
        return "--:--:--"

    safe_seconds = max(0, int(round(float(total_seconds))))
    hours, remainder = divmod(safe_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _render_progress_bar(fraction: float, *, width: int = 28) -> str:
    clamped = max(0.0, min(1.0, float(fraction)))
    filled = int(round(clamped * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _chapter_number(chapter_name: str) -> int | None:
    match = CHAPTER_FOLDER_PATTERN.match(chapter_name)
    if match is None:
        return None
    return int(match.group("number"))


def parse_args() -> argparse.Namespace:
    this_file = Path(__file__).resolve()
    default_runner = this_file.parent / "run_chapter_audio_test.py"

    parser = argparse.ArgumentParser(
        description=(
            "Batch run chapter audio generation, dialogue splitting, and split "
            "validation with overall ETA and per-chunk progress."
        )
    )
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument("--sample-path", type=Path, required=True)
    parser.add_argument("--start-chapter", type=int, default=1)
    parser.add_argument("--end-chapter", type=int, default=None)
    parser.add_argument(
        "--output-dir-name",
        default="audio_test_stephen_fry",
        help="Per-chapter output folder name created inside each chapter dir.",
    )
    parser.add_argument(
        "--runner-script",
        type=Path,
        default=default_runner,
        help="Path to run_chapter_audio_test.py.",
    )
    parser.add_argument(
        "--python-bin",
        type=Path,
        default=Path(sys.executable),
        help="Python executable used to run each chapter.",
    )

    parser.add_argument(
        "--engine",
        choices=["openai", "whisperx"],
        default="whisperx",
    )
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument(
        "--compute-type",
        choices=["auto", "float16", "int8", "int8_float16", "float32"],
        default="float16",
    )
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument(
        "--split-validation-engine",
        choices=["same", "openai", "whisperx"],
        default="whisperx",
    )
    parser.add_argument("--line-chunk-min-chars", type=int, default=800)
    parser.add_argument("--line-chunk-join-ms", type=int, default=0)
    parser.add_argument("--no-line-chunk-generate", action="store_true")
    parser.add_argument("--character-filter", default=None)

    parser.add_argument(
        "--skip-chapters-with-report",
        action="store_true",
        help="Skip chapter if split_validation_report.json already exists.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue remaining chapters if one chapter fails.",
    )
    parser.add_argument(
        "--overall-update-sec",
        type=float,
        default=20.0,
        help="Seconds between periodic overall ETA updates while a chapter runs.",
    )
    parser.add_argument("--summary-tsv", type=Path, default=None)
    parser.add_argument("--summary-json", type=Path, default=None)
    parser.add_argument("--log-path", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")

    return parser.parse_args()


def discover_chapters(
    *,
    book_dir: Path,
    start_chapter: int,
    end_chapter: int | None,
) -> list[Path]:
    chapter_dirs: list[tuple[int, Path]] = []
    for child in sorted(book_dir.iterdir()):
        if not child.is_dir():
            continue
        chapter_num = _chapter_number(child.name)
        if chapter_num is None:
            continue
        if chapter_num < start_chapter:
            continue
        if end_chapter is not None and chapter_num > end_chapter:
            continue
        chapter_dirs.append((chapter_num, child))

    chapter_dirs.sort(key=lambda item: (item[0], item[1].name.lower()))
    return [item[1] for item in chapter_dirs]


def load_flagged_line_ids(report_path: Path) -> list[int] | None:
    if not report_path.exists():
        return None

    with report_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    summary = payload.get("summary") if isinstance(payload, dict) else None
    final_summary = summary.get("final") if isinstance(summary, dict) else None
    ids = final_summary.get("flaggedLineIds") if isinstance(final_summary, dict) else None
    if not isinstance(ids, list):
        return []

    parsed: list[int] = []
    for value in ids:
        try:
            parsed.append(int(value))
        except (TypeError, ValueError):
            continue
    return parsed


def write_summary_header(summary_path: Path) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                "chapter",
                "status",
                "runtime_sec",
                "flagged_count",
                "flagged_line_ids",
                "report_path",
                "output_dir",
            ]
        )


def append_summary_row(summary_path: Path, result: ChapterResult) -> None:
    flagged_ids_csv = ""
    if result.flagged_line_ids is not None:
        flagged_ids_csv = ",".join(str(value) for value in result.flagged_line_ids)

    with summary_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                result.chapter,
                result.status,
                f"{result.runtime_sec:.3f}",
                "" if result.flagged_count is None else result.flagged_count,
                flagged_ids_csv,
                "" if result.report_path is None else result.report_path,
                result.output_dir,
            ]
        )


def write_summary_json(summary_path: Path, results: list[ChapterResult]) -> None:
    payload = {
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": [
            {
                "chapter": result.chapter,
                "status": result.status,
                "runtimeSec": round(result.runtime_sec, 4),
                "flaggedCount": result.flagged_count,
                "flaggedLineIds": result.flagged_line_ids,
                "reportPath": result.report_path,
                "outputDir": result.output_dir,
            }
            for result in results
        ],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def print_overall_progress(
    *,
    completed: int,
    total: int,
    run_started_at: float,
    average_chapter_sec: float | None,
    current_chapter: str | None,
    current_chapter_elapsed_sec: float | None,
) -> None:
    print(
        build_overall_progress_line(
            completed=completed,
            total=total,
            run_started_at=run_started_at,
            average_chapter_sec=average_chapter_sec,
            current_chapter=current_chapter,
            current_chapter_elapsed_sec=current_chapter_elapsed_sec,
        )
    )


def build_overall_progress_line(
    *,
    completed: int,
    total: int,
    run_started_at: float,
    average_chapter_sec: float | None,
    current_chapter: str | None,
    current_chapter_elapsed_sec: float | None,
) -> str:
    if total <= 0:
        return "Overall [----------------------------] 0/0 (  0.0%) | elapsed 00:00:00 | ETA --:--:--"

    running_fraction = 0.0
    if (
        current_chapter is not None
        and average_chapter_sec is not None
        and average_chapter_sec > 0.0
        and current_chapter_elapsed_sec is not None
    ):
        running_fraction = min(0.99, current_chapter_elapsed_sec / average_chapter_sec)

    completed_units = float(completed) + running_fraction
    progress_fraction = min(1.0, completed_units / float(total))

    elapsed_sec = time.perf_counter() - run_started_at
    eta_sec: float | None = None
    if average_chapter_sec is not None and average_chapter_sec > 0.0:
        remaining_units = max(0.0, float(total) - completed_units)
        eta_sec = remaining_units * average_chapter_sec

    suffix = ""
    if current_chapter is not None:
        suffix = f" | current {current_chapter}"

    return (
        "Overall "
        f"{_render_progress_bar(progress_fraction)} "
        f"{completed}/{total} "
        f"({progress_fraction * 100.0:5.1f}%) "
        f"| elapsed {_format_hms(elapsed_sec)} "
        f"| ETA {_format_hms(eta_sec)}"
        f"{suffix}"
    )


def build_chapter_command(
    *,
    args: argparse.Namespace,
    chapter_dir: Path,
    output_dir: Path,
    full_audio_path: Path,
) -> list[str]:
    command = [
        str(args.python_bin),
        str(args.runner_script),
        "--chapter-dir",
        str(chapter_dir),
        "--sample-path",
        str(args.sample_path),
        "--output-dir",
        str(output_dir),
        "--full-audio",
        str(full_audio_path),
        "--engine",
        str(args.engine),
        "--split-validation-engine",
        str(args.split_validation_engine),
        "--device",
        str(args.device),
        "--compute-type",
        str(args.compute_type),
        "--line-chunk-min-chars",
        str(args.line_chunk_min_chars),
        "--line-chunk-join-ms",
        str(args.line_chunk_join_ms),
    ]

    if args.batch_size > 0:
        command.extend(["--batch-size", str(args.batch_size)])
    if args.no_line_chunk_generate:
        command.append("--no-line-chunk-generate")
    if args.character_filter:
        command.extend(["--character-filter", str(args.character_filter)])

    return command


def _open_log(log_path: Path | None) -> TextIO | None:
    if log_path is None:
        return None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path.open("a", encoding="utf-8")


def main() -> None:
    args = parse_args()

    book_dir = args.book_dir.resolve()
    sample_path = args.sample_path.resolve()
    runner_script = args.runner_script.resolve()
    python_bin = args.python_bin.resolve()

    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")
    if not sample_path.exists():
        raise FileNotFoundError(f"Voice sample not found: {sample_path}")
    if not runner_script.exists():
        raise FileNotFoundError(f"Runner script not found: {runner_script}")
    if not python_bin.exists():
        raise FileNotFoundError(f"Python executable not found: {python_bin}")

    chapters = discover_chapters(
        book_dir=book_dir,
        start_chapter=int(args.start_chapter),
        end_chapter=(int(args.end_chapter) if args.end_chapter is not None else None),
    )
    if not chapters:
        raise RuntimeError("No matching chapter directories found for the selected range.")

    run_tag = time.strftime("%Y%m%d_%H%M%S")
    summary_tsv = (
        args.summary_tsv.resolve()
        if args.summary_tsv is not None
        else (book_dir / f"batch_{run_tag}_malformed_summary.tsv")
    )
    summary_json = (
        args.summary_json.resolve()
        if args.summary_json is not None
        else summary_tsv.with_suffix(".json")
    )
    log_path = (
        args.log_path.resolve()
        if args.log_path is not None
        else (book_dir / f"batch_{run_tag}.log")
    )

    write_summary_header(summary_tsv)

    workspace_root = Path(__file__).resolve().parents[1]
    cudnn_bin = workspace_root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin"

    env = os.environ.copy()
    if cudnn_bin.exists():
        current_path = env.get("PATH", "")
        env["PATH"] = str(cudnn_bin) + os.pathsep + current_path

    total_chapters = len(chapters)
    run_started_at = time.perf_counter()
    completed_durations: list[float] = []
    results: list[ChapterResult] = []
    progress_renderer = ProgressRenderer()

    print(f"Found {total_chapters} chapters.")
    print(f"Summary TSV: {summary_tsv}")
    print(f"Summary JSON: {summary_json}")
    print(f"Log path: {log_path}")

    current_chunk_line = "Chunk   [----------------------------] waiting to start"

    log_handle = _open_log(log_path)
    try:
        for index, chapter_dir in enumerate(chapters, start=1):
            chapter_name = chapter_dir.name
            output_dir = chapter_dir / str(args.output_dir_name)
            full_audio_path = output_dir / f"{chapter_name}-stephen-fry-full.wav"
            report_path = output_dir / "split_validation_report.json"

            if args.skip_chapters_with_report and report_path.exists():
                skipped_ids = load_flagged_line_ids(report_path)
                skipped_count = None if skipped_ids is None else len(skipped_ids)
                skipped_result = ChapterResult(
                    chapter=chapter_name,
                    status=0,
                    runtime_sec=0.0,
                    flagged_count=skipped_count,
                    flagged_line_ids=skipped_ids,
                    report_path=str(report_path) if report_path.exists() else None,
                    output_dir=str(output_dir),
                )
                append_summary_row(summary_tsv, skipped_result)
                results.append(skipped_result)
                if log_handle is not None:
                    log_handle.write(
                        f"Skipping {chapter_name} because report already exists: {report_path}\n"
                    )
                    log_handle.flush()
                current_chunk_line = (
                    "Chunk   [############################] "
                    f"skipped {chapter_name} (report exists)"
                )
                progress_renderer.update(
                    build_overall_progress_line(
                        completed=len(results),
                        total=total_chapters,
                        run_started_at=run_started_at,
                        average_chapter_sec=(
                            sum(completed_durations) / len(completed_durations)
                            if completed_durations
                            else None
                        ),
                        current_chapter=None,
                        current_chapter_elapsed_sec=None,
                    ),
                    current_chunk_line,
                )
                continue

            average_chapter_sec = (
                sum(completed_durations) / len(completed_durations)
                if completed_durations
                else None
            )
            current_chunk_line = (
                "Chunk   [----------------------------] "
                f"starting {chapter_name}"
            )
            progress_renderer.update(
                build_overall_progress_line(
                    completed=len(results),
                    total=total_chapters,
                    run_started_at=run_started_at,
                    average_chapter_sec=average_chapter_sec,
                    current_chapter=chapter_name,
                    current_chapter_elapsed_sec=0.0,
                ),
                current_chunk_line,
            )

            command = build_chapter_command(
                args=args,
                chapter_dir=chapter_dir,
                output_dir=output_dir,
                full_audio_path=full_audio_path,
            )
            if args.dry_run:
                dry_command_line = "DRY RUN: " + " ".join(command)
                if log_handle is not None:
                    log_handle.write(dry_command_line + "\n")
                    log_handle.flush()
                dry_result = ChapterResult(
                    chapter=chapter_name,
                    status=0,
                    runtime_sec=0.0,
                    flagged_count=None,
                    flagged_line_ids=None,
                    report_path=str(report_path),
                    output_dir=str(output_dir),
                )
                append_summary_row(summary_tsv, dry_result)
                results.append(dry_result)
                current_chunk_line = (
                    "Chunk   [############################] "
                    f"dry-run only {chapter_name}"
                )
                progress_renderer.update(
                    build_overall_progress_line(
                        completed=len(results),
                        total=total_chapters,
                        run_started_at=run_started_at,
                        average_chapter_sec=average_chapter_sec,
                        current_chapter=None,
                        current_chapter_elapsed_sec=None,
                    ),
                    current_chunk_line,
                )
                continue

            chapter_started_at = time.perf_counter()
            process = subprocess.Popen(
                command,
                cwd=str(workspace_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            last_overall_tick = time.perf_counter()
            if process.stdout is not None:
                for raw_line in process.stdout:
                    clean_line = raw_line.rstrip("\n")
                    if log_handle is not None:
                        log_handle.write(f"[{chapter_name}] {clean_line}\n")
                        log_handle.flush()

                    if clean_line.startswith("Chunk progress "):
                        current_chunk_line = clean_line
                        now = time.perf_counter()
                        progress_renderer.update(
                            build_overall_progress_line(
                                completed=len(results),
                                total=total_chapters,
                                run_started_at=run_started_at,
                                average_chapter_sec=(
                                    sum(completed_durations) / len(completed_durations)
                                    if completed_durations
                                    else None
                                ),
                                current_chapter=chapter_name,
                                current_chapter_elapsed_sec=(now - chapter_started_at),
                            ),
                            current_chunk_line,
                        )
                        continue

                    if clean_line.startswith("Generating chunk "):
                        current_chunk_line = clean_line
                    elif clean_line.startswith("Transcribing full chapter audio"):
                        current_chunk_line = (
                            "Chunk   [############################] "
                            "generation complete | transcribing full chapter"
                        )
                    elif clean_line.startswith("Loading split validation ASR"):
                        current_chunk_line = (
                            "Chunk   [############################] "
                            "running split validation"
                        )
                    elif clean_line.startswith("Initial split validation flagged"):
                        current_chunk_line = clean_line
                    elif clean_line.startswith("Final split validation flagged"):
                        current_chunk_line = clean_line

                    now = time.perf_counter()
                    if now - last_overall_tick >= max(1.0, float(args.overall_update_sec)):
                        progress_renderer.update(
                            build_overall_progress_line(
                                completed=len(results),
                                total=total_chapters,
                                run_started_at=run_started_at,
                                average_chapter_sec=(
                                    sum(completed_durations) / len(completed_durations)
                                    if completed_durations
                                    else None
                                ),
                                current_chapter=chapter_name,
                                current_chapter_elapsed_sec=(now - chapter_started_at),
                            ),
                            current_chunk_line,
                        )
                        last_overall_tick = now

            process.wait()
            runtime_sec = time.perf_counter() - chapter_started_at
            completed_durations.append(runtime_sec)

            flagged_line_ids = load_flagged_line_ids(report_path)
            flagged_count = None
            if flagged_line_ids is not None:
                flagged_count = len(flagged_line_ids)

            result = ChapterResult(
                chapter=chapter_name,
                status=int(process.returncode),
                runtime_sec=runtime_sec,
                flagged_count=flagged_count,
                flagged_line_ids=flagged_line_ids,
                report_path=str(report_path) if report_path.exists() else None,
                output_dir=str(output_dir),
            )
            append_summary_row(summary_tsv, result)
            results.append(result)

            average_chapter_sec = (
                sum(completed_durations) / len(completed_durations)
                if completed_durations
                else None
            )
            current_chunk_line = (
                "Chunk   [############################] "
                f"finished {chapter_name} "
                f"| status={result.status} "
                f"| flagged={result.flagged_count if result.flagged_count is not None else 'NA'}"
            )
            progress_renderer.update(
                build_overall_progress_line(
                    completed=len(results),
                    total=total_chapters,
                    run_started_at=run_started_at,
                    average_chapter_sec=average_chapter_sec,
                    current_chapter=None,
                    current_chapter_elapsed_sec=None,
                ),
                current_chunk_line,
            )

            if result.status != 0 and not args.continue_on_error:
                progress_renderer.info(
                    "Stopping batch because chapter failed. "
                    "Use --continue-on-error to keep going."
                )
                break
    finally:
        if log_handle is not None:
            log_handle.close()

    write_summary_json(summary_json, results)

    progress_renderer.end_block()

    completed_count = len(results)
    failed_count = sum(1 for item in results if item.status != 0)
    malformed_total = sum(
        int(item.flagged_count)
        for item in results
        if item.flagged_count is not None
    )
    print(
        "Batch complete: "
        f"chapters={completed_count}/{total_chapters}, "
        f"failed={failed_count}, "
        f"total_flagged_lines={malformed_total}"
    )
    print(f"Wrote summary TSV: {summary_tsv}")
    print(f"Wrote summary JSON: {summary_json}")


if __name__ == "__main__":
    main()
