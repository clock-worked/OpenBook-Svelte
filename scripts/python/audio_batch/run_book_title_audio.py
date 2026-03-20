"""Generate one full-book chapter-title WAV, split it, and optionally distribute clips."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

from _path_setup import bootstrap_python_script_paths

PATHS = bootstrap_python_script_paths(__file__)


CHAPTER_FOLDER_PATTERN = re.compile(r"^(?P<number>\d+)-(?P<slug>.+)$")

ONES = {
    0: "Zero",
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
    11: "Eleven",
    12: "Twelve",
    13: "Thirteen",
    14: "Fourteen",
    15: "Fifteen",
    16: "Sixteen",
    17: "Seventeen",
    18: "Eighteen",
    19: "Nineteen",
}

TENS = {
    20: "Twenty",
    30: "Thirty",
    40: "Forty",
    50: "Fifty",
    60: "Sixty",
    70: "Seventy",
    80: "Eighty",
    90: "Ninety",
}


@dataclass
class TitleEntry:
    """Describe one chapter title line and its destination mapping."""

    line_id: int
    chapter_dir_name: str
    chapter_path: str
    spoken_text: str
    source_output: str


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the book title audio workflow."""

    this_file = Path(__file__).resolve()
    default_runner = this_file.parent / "run_chapter_audio_test.py"

    parser = argparse.ArgumentParser(
        description=(
            "Create one combined chapter-title narration file for a book, run the "
            "existing ASR-backed splitter, and optionally copy the resulting title "
            "clips into each chapter folder."
        )
    )
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument("--sample-path", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the combined full WAV, manifests, and split clips.",
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
        help="Python executable used to run the chapter audio test script.",
    )
    parser.add_argument(
        "--chapter-title-subdir",
        default="title_audio",
        help="Subdirectory created inside each chapter when copying split clips.",
    )
    parser.add_argument(
        "--chapter-title-filename",
        default="chapter-title-stephen-fry.wav",
        help="File name used when copying split clips into chapter folders.",
    )
    parser.add_argument(
        "--copy-to-chapters",
        action="store_true",
        help="Copy each split title clip into its source chapter folder.",
    )
    parser.add_argument(
        "--engine",
        choices=["openai", "whisperx"],
        default="whisperx",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="cuda",
    )
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
    parser.add_argument(
        "--split-validation-threshold",
        type=float,
        default=0.82,
    )
    parser.add_argument(
        "--split-validation-neighbor-delta",
        type=float,
        default=0.08,
    )
    parser.add_argument(
        "--split-validation-min-improvement",
        type=float,
        default=0.04,
    )
    parser.add_argument(
        "--split-validation-alternates-per-side",
        type=int,
        default=1,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_ascii_punctuation(text: str) -> str:
    """Replace smart punctuation with ASCII variants for synthesis and ASR."""

    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return " ".join(text.split()).strip()


def int_to_words(value: int) -> str:
    """Convert a small positive integer into title-friendly English words."""

    if value < 0:
        raise ValueError("Negative chapter numbers are not supported.")
    if value < 20:
        return ONES[value]
    if value < 100:
        tens_value = (value // 10) * 10
        remainder = value % 10
        if remainder == 0:
            return TENS[tens_value]
        return f"{TENS[tens_value]} {ONES[remainder]}"
    if value < 1000:
        hundreds = value // 100
        remainder = value % 100
        if remainder == 0:
            return f"{ONES[hundreds]} Hundred"
        return f"{ONES[hundreds]} Hundred {int_to_words(remainder)}"
    raise ValueError(f"Unsupported chapter number: {value}")


def discover_chapter_dirs(book_dir: Path) -> list[Path]:
    """Find numeric chapter directories that contain chapter text."""

    chapters: list[tuple[int, Path]] = []
    for child in sorted(book_dir.iterdir()):
        if not child.is_dir():
            continue
        match = CHAPTER_FOLDER_PATTERN.match(child.name)
        if match is None:
            continue
        if not (child / "chapter.txt").exists():
            continue
        chapters.append((int(match.group("number")), child))

    chapters.sort(key=lambda item: (item[0], item[1].name.lower()))
    return [item[1] for item in chapters]


def derive_spoken_title(chapter_dir_name: str) -> str:
    """Turn a chapter folder name into the spoken chapter title text."""

    match = CHAPTER_FOLDER_PATTERN.match(chapter_dir_name)
    if match is None:
        raise ValueError(f"Chapter folder does not match expected pattern: {chapter_dir_name}")

    slug = normalize_ascii_punctuation(match.group("slug"))
    parts = [part for part in slug.split("-") if part]
    if not parts:
        raise ValueError(f"Unable to derive title from chapter folder: {chapter_dir_name}")

    lowered = [part.lower() for part in parts]
    if lowered[0] == "prologue":
        return "Prologue."
    if lowered[0] == "epilogue":
        return "Epilogue."

    if len(parts) >= 2 and lowered[0] == "chapter" and parts[1].isdigit():
        chapter_number_words = int_to_words(int(parts[1]))
        title_words = " ".join(parts[2:]).strip()
        if title_words:
            return f"Chapter {chapter_number_words}: {title_words}."
        return f"Chapter {chapter_number_words}."

    return f"{' '.join(parts)}."


def build_title_entries(
    *,
    chapters: list[Path],
    chapter_title_subdir: str,
    chapter_title_filename: str,
) -> list[TitleEntry]:
    """Create title entries for the synthetic combined title manifest."""

    entries: list[TitleEntry] = []
    clean_subdir = chapter_title_subdir.strip().strip("/\\")
    if not clean_subdir:
        raise ValueError("--chapter-title-subdir cannot be empty")

    clean_filename = chapter_title_filename.strip()
    if not clean_filename:
        raise ValueError("--chapter-title-filename cannot be empty")

    for line_id, chapter_path in enumerate(chapters):
        chapter_dir_name = chapter_path.name
        source_output = Path(chapter_dir_name) / clean_subdir / clean_filename
        entries.append(
            TitleEntry(
                line_id=line_id,
                chapter_dir_name=chapter_dir_name,
                chapter_path=str(chapter_path.resolve()),
                spoken_text=derive_spoken_title(chapter_dir_name),
                source_output=source_output.as_posix(),
            )
        )

    return entries


def write_source_files(
    source_dir: Path,
    book_dir: Path,
    entries: list[TitleEntry],
) -> dict[str, Path]:
    """Write the synthetic chapter text and manifest consumed by the runner."""

    if source_dir.exists():
        shutil.rmtree(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)

    chapter_text_path = source_dir / "chapter.txt"
    manifest_path = source_dir / "manifest.json"
    titles_index_path = source_dir / "titles_index.json"

    chapter_text_path.write_text(
        "\n\n".join(entry.spoken_text for entry in entries) + "\n",
        encoding="utf-8",
    )

    manifest_payload = {
        "formatVersion": "chapter-title-source/v1",
        "bookDir": str(book_dir.resolve()),
        "lines": [
            {
                "id": entry.line_id,
                "characterId": "chapter-title",
                "text": entry.spoken_text,
                "output": entry.source_output,
                "skipped": False,
            }
            for entry in entries
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    titles_index_path.write_text(
        json.dumps(
            {
                "formatVersion": "chapter-title-index/v1",
                "bookDir": str(book_dir.resolve()),
                "entries": [asdict(entry) for entry in entries],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "chapterText": chapter_text_path,
        "manifest": manifest_path,
        "titlesIndex": titles_index_path,
    }


def clean_generated_outputs(output_dir: Path, full_audio_path: Path) -> None:
    """Remove previous generated artifacts in the dedicated title output folder."""

    output_dir.mkdir(parents=True, exist_ok=True)

    removable_dirs = [
        output_dir / "splits",
        output_dir / "generation_chunks",
    ]
    removable_files = [
        full_audio_path,
        output_dir / "audio_test_manifest.json",
        output_dir / "transcription_result.json",
        output_dir / "split_validation_report.json",
        output_dir / "distribution_manifest.json",
    ]

    for path in removable_dirs:
        if path.exists():
            shutil.rmtree(path)

    for path in removable_files:
        if path.exists():
            path.unlink()


def build_runner_command(
    *,
    args: argparse.Namespace,
    source_dir: Path,
    source_paths: dict[str, Path],
    output_dir: Path,
    full_audio_path: Path,
) -> list[str]:
    """Build the existing chapter audio runner command for the title source."""

    command = [
        str(args.python_bin.resolve()),
        str(args.runner_script.resolve()),
        "--chapter-dir",
        str(source_dir.resolve()),
        "--chapter-text",
        str(source_paths["chapterText"].resolve()),
        "--manifest",
        str(source_paths["manifest"].resolve()),
        "--sample-path",
        str(args.sample_path.resolve()),
        "--output-dir",
        str(output_dir.resolve()),
        "--full-audio",
        str(full_audio_path.resolve()),
        "--engine",
        str(args.engine),
        "--split-validation-engine",
        str(args.split_validation_engine),
        "--device",
        str(args.device),
        "--compute-type",
        str(args.compute_type),
        "--split-validation-threshold",
        str(args.split_validation_threshold),
        "--split-validation-neighbor-delta",
        str(args.split_validation_neighbor_delta),
        "--split-validation-min-improvement",
        str(args.split_validation_min_improvement),
        "--split-validation-alternates-per-side",
        str(args.split_validation_alternates_per_side),
        "--no-line-chunk-generate",
    ]

    if args.batch_size > 0:
        command.extend(["--batch-size", str(args.batch_size)])

    return command


def build_runtime_env(workspace_root: Path) -> dict[str, str]:
    """Prepare PATH so WhisperX CUDA dependencies resolve on Windows."""

    env = os.environ.copy()
    cudnn_bin = workspace_root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin"
    if cudnn_bin.exists():
        current_path = env.get("PATH", "")
        env["PATH"] = str(cudnn_bin) + os.pathsep + current_path
    return env


def distribute_split_clips(*, book_dir: Path, output_dir: Path) -> Path:
    """Copy generated split clips into their intended chapter destinations."""

    result_manifest_path = output_dir / "audio_test_manifest.json"
    if not result_manifest_path.exists():
        raise FileNotFoundError(f"Split manifest not found: {result_manifest_path}")

    payload = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError(f"Invalid split manifest format: {result_manifest_path}")

    distribution_records: list[dict[str, Any]] = []
    for line in raw_lines:
        if not isinstance(line, dict):
            continue
        audio_path = Path(str(line.get("audioPath", "")).strip())
        source_output = str(line.get("sourceOutput", "")).strip()
        if not source_output:
            continue
        if not audio_path.exists():
            raise FileNotFoundError(f"Split audio file not found: {audio_path}")

        destination_path = (book_dir / Path(source_output)).resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(audio_path, destination_path)

        distribution_records.append(
            {
                "id": line.get("id"),
                "text": line.get("text"),
                "chapterDir": Path(source_output).parts[0] if Path(source_output).parts else None,
                "sourceAudioPath": str(audio_path),
                "destinationPath": str(destination_path),
            }
        )

    distribution_manifest_path = output_dir / "distribution_manifest.json"
    distribution_manifest_path.write_text(
        json.dumps(
            {
                "formatVersion": "chapter-title-distribution/v1",
                "bookDir": str(book_dir.resolve()),
                "clipCount": len(distribution_records),
                "clips": distribution_records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return distribution_manifest_path


def main() -> None:
    """Prepare, generate, split, and optionally distribute chapter title audio."""

    args = parse_args()

    book_dir = args.book_dir.resolve()
    sample_path = args.sample_path.resolve()
    runner_script = args.runner_script.resolve()
    python_bin = args.python_bin.resolve()
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else (book_dir / "_chapter_title_audio_stephen_fry").resolve()
    )

    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")
    if not sample_path.exists():
        raise FileNotFoundError(f"Voice sample not found: {sample_path}")
    if not runner_script.exists():
        raise FileNotFoundError(f"Runner script not found: {runner_script}")
    if not python_bin.exists():
        raise FileNotFoundError(f"Python executable not found: {python_bin}")

    chapters = discover_chapter_dirs(book_dir)
    if not chapters:
        raise RuntimeError(f"No chapter folders found in: {book_dir}")

    entries = build_title_entries(
        chapters=chapters,
        chapter_title_subdir=args.chapter_title_subdir,
        chapter_title_filename=args.chapter_title_filename,
    )

    source_dir = output_dir / "source"
    source_paths = write_source_files(source_dir, book_dir, entries)
    full_audio_path = output_dir / f"{book_dir.name}-chapter-titles-stephen-fry-full.wav"
    clean_generated_outputs(output_dir, full_audio_path)

    command = build_runner_command(
        args=args,
        source_dir=source_dir,
        source_paths=source_paths,
        output_dir=output_dir,
        full_audio_path=full_audio_path,
    )

    print(f"Prepared {len(entries)} chapter titles.")
    print(f"Source chapter text: {source_paths['chapterText']}")
    print(f"Source manifest: {source_paths['manifest']}")
    print(f"Output directory: {output_dir}")
    print(f"Full title audio: {full_audio_path}")
    print("Command:")
    print(" ".join(command))

    if args.dry_run:
        return

    workspace_root = PATHS["repoRoot"]
    subprocess.run(
        command,
        cwd=str(workspace_root),
        env=build_runtime_env(workspace_root),
        check=True,
    )

    if args.copy_to_chapters:
        distribution_manifest_path = distribute_split_clips(
            book_dir=book_dir,
            output_dir=output_dir,
        )
        print(f"Copied split title clips into chapter folders: {distribution_manifest_path}")


if __name__ == "__main__":
    main()
