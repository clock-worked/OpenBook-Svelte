"""Generate batch chapter-title WAV files, split them, and distribute clips.

Workflow:
1. Discover chapter directories and derive chapter titles
2. Split titles into batches
3. Generate batch TTS audio with generate_and_split_chapter_narrator.py
4. Split batch audio with ASR and copy resulting clips into destination chapters
"""

from __future__ import annotations

# pylint: disable=C0115,C0116
# ruff: noqa: D101, D103, E402, E501
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


CHAPTER_FOLDER_PATTERN = re.compile(r"^(?P<number>\d+)\s*-\s*(?P<slug>.+)$")
SPOKEN_CHAPTER_PATTERN = re.compile(
    r"^chapter\s+(?P<chapter_number>\d+)\s*-\s*(?P<title>.+)$",
    re.IGNORECASE,
)

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
    target_manifest_path: str | None = None
    target_manifest_line_id: int | None = None
    target_validation_report_path: str | None = None


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the batched chapter title audio workflow."""

    this_file = Path(__file__).resolve()
    default_runner = this_file.parent / "generate_and_split_chapter_narrator.py"
    
    # Try to use the repo's venv python if available, otherwise use system python
    default_python = this_file.parents[3] / ".venv" / "Scripts" / "python.exe"
    if not default_python.exists():
        default_python = Path(sys.executable)

    parser = argparse.ArgumentParser(
        description=(
            "Create chapter-title narration files for a book in batches, run the "
            "existing ASR-backed splitter on each batch, and optionally copy the "
            "resulting title clips into each chapter folder."
        )
    )
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument("--sample-path", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for batch artifacts and chapter clip destinations.",
    )
    parser.add_argument(
        "--runner-script",
        type=Path,
        default=default_runner,
        help="Path to generate_and_split_chapter_narrator.py.",
    )
    parser.add_argument(
        "--python-bin",
        type=Path,
        default=default_python,
        help="Python executable used to run the chapter audio test script.",
    )
    parser.add_argument(
        "--chapter-title-subdir",
        default="title_audio",
        help="Subdirectory created inside each chapter when copying split clips.",
    )
    parser.add_argument(
        "--chapter-title-filename",
        default="chapter-title.wav",
        help="File name used when copying split clips into chapter folders.",
    )
    parser.add_argument(
        "--copy-to-chapters",
        action="store_true",
        help="Copy each split title clip into its source chapter folder.",
    )
    parser.add_argument(
        "--refresh-audio-test-metadata",
        action="store_true",
        help=(
            "After copying split clips, update the destination manifest text so "
            "the replaced title line matches the spoken replacement title."
        ),
    )
    parser.add_argument(
        "--copy-to-existing-audio-test-title-lines",
        action="store_true",
        help=(
            "Resolve each chapter destination from its existing title line, "
            "preferring audio_lines manifests and falling back to "
            "audio_test_narrator/audio_test_manifest.json, instead of using the "
            "static subdir/filename destination."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--engine", choices=["openai", "whisperx"], default="whisperx")
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
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument(
        "--compute-type",
        choices=["auto", "float16", "int8", "int8_float16", "float32"],
        default="float16",
    )
    parser.add_argument("--batch-size-asr", type=int, default=0)
    parser.add_argument("--max-chapters", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reuse-existing-batches",
        action="store_true",
        help="Reuse existing batch_*/audio_test_manifest.json artifacts when present.",
    )
    parser.add_argument(
        "--skip-clip-copy",
        action="store_true",
        help=(
            "Only generate/split batch artifacts; do not copy clips into the "
            "chapter directories."
        ),
    )
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
        return f"{ONES[hundreds]} Hundred and {int_to_words(remainder)}"
    if value < 10000:
        # Prefer year-style chapter narration: 1256 -> "Twelve Fifty Six".
        lead = value // 100
        tail = value % 100
        lead_words = int_to_words(lead)
        if tail == 0:
            return f"{lead_words} Hundred"
        if tail < 10:
            return f"{lead_words} Oh {int_to_words(tail)}"
        return f"{lead_words} {int_to_words(tail)}"
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
    if not slug:
        raise ValueError(f"Unable to derive title from chapter folder: {chapter_dir_name}")

    lowered_slug = slug.lower()
    if lowered_slug == "prologue":
        return "Prologue."
    if lowered_slug == "epilogue":
        return "Epilogue."

    spoken_match = SPOKEN_CHAPTER_PATTERN.match(slug)
    if spoken_match is not None:
        chapter_number_words = int_to_words(int(spoken_match.group("chapter_number")))
        title_words = spoken_match.group("title").strip()
        if title_words:
            return f"Chapter {chapter_number_words} - {title_words}."
        return f"Chapter {chapter_number_words}."

    return f"{slug}."


def sanitize_voice_label(sample_path: Path) -> str:
    """Turn a sample file stem into a stable lowercase folder/file suffix."""

    label = re.sub(r"[^a-z0-9]+", "-", sample_path.stem.lower()).strip("-")
    return label or "voice-sample"


def resolve_existing_title_destination(chapter_path: Path) -> tuple[Path, int | None, Path | None, Path | None] | None:
    """Locate an existing chapter title target, preferring audio_lines manifests."""

    manifest_specs = [
        {
            "manifest": chapter_path / "audio_lines" / "manifest.json",
            "base_dir": chapter_path / "audio_lines",
            "output_key": "output",
            "validation": None,
        },
        {
            "manifest": chapter_path / "audio_test_narrator" / "audio_test_manifest.json",
            "base_dir": chapter_path / "audio_test_narrator" / "splits" / "Narrator",
            "output_key": "audioFile",
            "validation": chapter_path / "audio_test_narrator" / "split_validation_report.json",
        },
    ]

    for spec in manifest_specs:
        manifest_path = spec["manifest"]
        if not manifest_path.exists():
            continue

        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_lines = payload.get("lines")
        if not isinstance(raw_lines, list):
            continue

        for candidate in raw_lines:
            if not isinstance(candidate, dict):
                continue
            text_value = str(candidate.get("text", "")).strip()
            if not text_value.lower().startswith("chapter "):
                continue

            output_value = str(candidate.get(spec["output_key"], "")).strip()
            if not output_value and spec["output_key"] == "audioFile":
                audio_path = Path(str(candidate.get("audioPath", "")).strip())
                output_value = audio_path.name
            if not output_value:
                continue

            destination_path = spec["base_dir"] / Path(output_value)
            try:
                target_manifest_line_id = int(candidate.get("id"))
            except (TypeError, ValueError):
                target_manifest_line_id = None

            validation_path = spec["validation"]
            if validation_path is not None and not validation_path.exists():
                validation_path = None

            return destination_path, target_manifest_line_id, manifest_path, validation_path

    return None


def build_title_entries(
    *,
    chapters: list[Path],
    chapter_title_subdir: str,
    chapter_title_filename: str,
    copy_to_existing_audio_test_title_lines: bool,
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
        target_manifest_path: Path | None = None
        target_manifest_line_id: int | None = None
        target_validation_report_path: Path | None = None

        if copy_to_existing_audio_test_title_lines:
            resolved_destination = resolve_existing_title_destination(chapter_path)
            if resolved_destination is not None:
                destination_path, target_manifest_line_id, target_manifest_path, target_validation_report_path = resolved_destination
                source_output = Path(chapter_dir_name) / destination_path.relative_to(chapter_path)

        entries.append(
            TitleEntry(
                line_id=line_id,
                chapter_dir_name=chapter_dir_name,
                chapter_path=str(chapter_path.resolve()),
                spoken_text=derive_spoken_title(chapter_dir_name),
                source_output=source_output.as_posix(),
                target_manifest_path=(str(target_manifest_path.resolve()) if target_manifest_path is not None else None),
                target_manifest_line_id=target_manifest_line_id,
                target_validation_report_path=(
                    str(target_validation_report_path.resolve())
                    if target_validation_report_path is not None
                    else None
                ),
            )
        )

    return entries


def chunk_entries(entries: list[TitleEntry], batch_size: int) -> list[list[TitleEntry]]:
    if batch_size <= 0:
        raise ValueError("--batch-size must be > 0")
    return [entries[index : index + batch_size] for index in range(0, len(entries), batch_size)]


def build_runtime_env(workspace_root: Path) -> dict[str, str]:
    """Prepare PATH so WhisperX CUDA dependencies resolve on Windows."""

    env = os.environ.copy()
    cudnn_bin = workspace_root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin"
    if cudnn_bin.exists():
        env["PATH"] = str(cudnn_bin) + os.pathsep + env.get("PATH", "")
    return env


def write_batch_source_files(
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
                "formatVersion": "chapter-title-batch-index/v1",
                "bookDir": str(book_dir.resolve()),
                "lineCount": len(entries),
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


def build_runner_command(
    *,
    args: argparse.Namespace,
    source_dir: Path,
    source_paths: dict[str, Path],
    output_dir: Path,
    full_audio_path: Path,
) -> list[str]:
    """Build the chapter audio runner command for the title batch."""

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

    if args.batch_size_asr > 0:
        command.extend(["--batch-size", str(args.batch_size_asr)])

    return command


def clear_generated_outputs(output_dir: Path) -> None:
    """Remove previous generated artifacts in the batches folder."""

    output_dir.mkdir(parents=True, exist_ok=True)

    removable_files = [
        output_dir / "chapter_title_batches.json",
        output_dir / "chapter_title_clips.json",
        output_dir / "chapter_title_clips.txt",
    ]

    for path in removable_files:
        if path.exists():
            path.unlink()

    for batch_dir in output_dir.glob("batch_*"):
        if batch_dir.is_dir():
            shutil.rmtree(batch_dir)


def load_batch_index(index_path: Path) -> dict[int, dict[str, Any]]:
    """Load batch index mapping line IDs to title entries."""

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError(f"Invalid batch index format: {index_path}")

    by_line_id: dict[int, dict[str, Any]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        line_id = entry.get("line_id")
        if isinstance(line_id, int):
            by_line_id[line_id] = entry
    return by_line_id


def copy_batch_clips(
    *,
    batch_dir: Path,
    book_dir: Path,
) -> list[dict[str, Any]]:
    """Copy generated split title clips into their chapter destinations."""

    result_manifest_path = batch_dir / "audio_test_manifest.json"
    index_path = batch_dir / "source" / "titles_index.json"
    if not result_manifest_path.exists():
        raise FileNotFoundError(f"Split manifest not found: {result_manifest_path}")
    if not index_path.exists():
        raise FileNotFoundError(f"Batch index not found: {index_path}")

    manifest_payload = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    raw_lines = manifest_payload.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError(f"Invalid split manifest format: {result_manifest_path}")

    entries_by_line_id = load_batch_index(index_path)

    copied: list[dict[str, Any]] = []
    for line in raw_lines:
        if not isinstance(line, dict):
            continue
        try:
            line_id = int(line.get("id"))
        except (TypeError, ValueError):
            continue

        batch_entry = entries_by_line_id.get(line_id)
        if not isinstance(batch_entry, dict):
            continue

        audio_path = Path(str(line.get("audioPath", "")).strip())
        source_output = str(line.get("sourceOutput", "")).strip()
        if not audio_path.exists():
            raise FileNotFoundError(f"Split audio file not found: {audio_path}")
        if not source_output:
            continue

        destination_path = (book_dir / Path(source_output)).resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(audio_path, destination_path)

        chapter_dir_name = batch_entry.get("chapter_dir_name", "")
        copied.append(
            {
                "lineId": line_id,
                "chapterDir": chapter_dir_name,
                "text": str(batch_entry.get("spoken_text", "")),
                "sourceAudioPath": str(audio_path),
                "destinationPath": str(destination_path),
            }
        )

    return copied


def write_clip_reports(output_dir: Path, copied_clips: list[dict[str, Any]]) -> None:
    """Write aggregated clip index files."""

    (output_dir / "chapter_title_clips.json").write_text(
        json.dumps(
            {
                "formatVersion": "chapter-title-audio-clips/v1",
                "count": len(copied_clips),
                "clips": copied_clips,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    lines = [f"{clip['chapterDir']}: {clip['text']}" for clip in copied_clips]
    (output_dir / "chapter_title_clips.txt").write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )


def refresh_audio_test_metadata(*, entries: list[TitleEntry]) -> list[Path]:
    """Update destination metadata after replacing title clips."""

    updated_paths: list[Path] = []
    for entry in entries:
        if entry.target_manifest_path is not None:
            manifest_path = Path(entry.target_manifest_path)
        else:
            manifest_path = Path(entry.chapter_path) / "audio_test_narrator" / "audio_test_manifest.json"

        if manifest_path.exists():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_lines = payload.get("lines")
            changed = False
            if isinstance(raw_lines, list):
                for line in raw_lines:
                    if not isinstance(line, dict):
                        continue
                    try:
                        line_id = int(line.get("id", -1))
                    except (TypeError, ValueError):
                        continue
                    if entry.target_manifest_line_id is not None:
                        if line_id != entry.target_manifest_line_id:
                            continue
                    elif line_id != 0:
                        continue
                    if line.get("text") != entry.spoken_text:
                        line["text"] = entry.spoken_text
                        changed = True
            if changed:
                manifest_path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                updated_paths.append(manifest_path)

        split_validation_report_path = (
            Path(entry.target_validation_report_path)
            if entry.target_validation_report_path is not None
            else Path(entry.chapter_path) / "audio_test_narrator" / "split_validation_report.json"
        )
        if split_validation_report_path.exists():
            payload = json.loads(split_validation_report_path.read_text(encoding="utf-8"))
            raw_lines = payload.get("lines")
            changed = False
            if isinstance(raw_lines, list):
                for line in raw_lines:
                    if not isinstance(line, dict):
                        continue
                    try:
                        line_id = int(line.get("lineId", -1))
                    except (TypeError, ValueError):
                        continue
                    if entry.target_manifest_line_id is not None:
                        if line_id != entry.target_manifest_line_id:
                            continue
                    elif line_id != 0:
                        continue
                    if line.get("expectedText") != entry.spoken_text:
                        line["expectedText"] = entry.spoken_text
                        changed = True
            if changed:
                split_validation_report_path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                updated_paths.append(split_validation_report_path)

    return updated_paths


def main() -> int:
    """Prepare, generate, split, and optionally distribute chapter title audio in batches."""

    args = parse_args()

    book_dir = args.book_dir.resolve()
    sample_path = args.sample_path.resolve()
    runner_script = args.runner_script.resolve()
    python_bin = args.python_bin.resolve()

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else (book_dir / f"_chapter_title_audio_batches_{sanitize_voice_label(sample_path)}").resolve()
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

    if args.max_chapters > 0:
        chapters = chapters[: args.max_chapters]

    entries = build_title_entries(
        chapters=chapters,
        chapter_title_subdir=args.chapter_title_subdir,
        chapter_title_filename=args.chapter_title_filename,
        copy_to_existing_audio_test_title_lines=bool(args.copy_to_existing_audio_test_title_lines),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if not args.reuse_existing_batches and not args.dry_run:
        clear_generated_outputs(output_dir)

    batches = chunk_entries(entries, args.batch_size) if entries else []

    print(f"Book dir: {book_dir}")
    print(f"Sample: {sample_path}")
    print(f"Total chapters: {len(entries)}")
    print(f"Batch count: {len(batches)}")
    print(f"Batch size: {args.batch_size}")

    if args.dry_run or not entries:
        return 0

    workspace_root = PATHS["repoRoot"]
    env = build_runtime_env(workspace_root)
    all_clips: list[dict[str, Any]] = []
    batch_records: list[dict[str, Any]] = []

    for batch_index, batch_entries in enumerate(batches, start=1):
        batch_id = f"batch_{batch_index:04d}"
        batch_dir = output_dir / batch_id
        batch_manifest_path = batch_dir / "audio_test_manifest.json"
        source_dir = batch_dir / "source"
        voice_label = sanitize_voice_label(sample_path)
        full_audio_path = batch_dir / f"{book_dir.name}-chapter-titles-{batch_id}-{voice_label}-full.wav"

        should_reuse = args.reuse_existing_batches and batch_manifest_path.exists()

        if not should_reuse:
            if batch_dir.exists():
                shutil.rmtree(batch_dir)
            source_paths = write_batch_source_files(source_dir, book_dir, batch_entries)
            command = build_runner_command(
                args=args,
                source_dir=source_dir,
                source_paths=source_paths,
                output_dir=batch_dir,
                full_audio_path=full_audio_path,
            )

            print(f"Running {batch_id} ({len(batch_entries)} titles)")
            subprocess.run(command, cwd=str(workspace_root), env=env, check=True)
        else:
            print(f"Reusing existing generated artifacts for {batch_id}: {batch_manifest_path}")

        copied_clips: list[dict[str, Any]] = []
        if not args.skip_clip_copy:
            copied_clips = copy_batch_clips(batch_dir=batch_dir, book_dir=book_dir)
            all_clips.extend(copied_clips)
            print(f"Copied clips for {batch_id}: {len(copied_clips)}")

        batch_records.append(
            {
                "batchId": batch_id,
                "lineCount": len(batch_entries),
                "batchDir": str(batch_dir),
                "manifestPath": str(batch_manifest_path),
                "clipCount": len(copied_clips),
            }
        )

    (output_dir / "chapter_title_batches.json").write_text(
        json.dumps(
            {
                "formatVersion": "chapter-title-batches/v1",
                "bookDir": str(book_dir),
                "samplePath": str(sample_path),
                "chapterCount": len(entries),
                "batchCount": len(batches),
                "clipCount": len(all_clips),
                "batches": batch_records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote batch summary: {output_dir / 'chapter_title_batches.json'}")

    if not args.skip_clip_copy:
        write_clip_reports(output_dir, all_clips)
        print(f"Wrote clip index: {output_dir / 'chapter_title_clips.json'}")

        if args.refresh_audio_test_metadata:
            updated_paths = refresh_audio_test_metadata(entries=entries)
            print(f"Updated narrator title metadata files: {len(updated_paths)}")

    print(f"Total clips: {len(all_clips)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
