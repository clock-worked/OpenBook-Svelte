"""Generate batch audio clips from the Book-17 lvl segment report.

Workflow:
1. Read the reusable segment library written by run_book17_lvl_audio.py.
2. Generate batch TTS audio for the segment library with run_chapter_audio_test.py.
3. Split the batch audio with ASR and copy the resulting clips into a stable clip
    directory for reuse.
"""

from __future__ import annotations

# pylint: disable=C0115,C0116
# ruff: noqa: D101, D103, E402, E501
import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BOOK_DIR = (
    Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources")
    / "Primal-Hunter"
    / "Book-17"
)
DEFAULT_REPORT_PATH = DEFAULT_BOOK_DIR / "_lvl_audio_segments" / "lvl_dialogue_report.json"
DEFAULT_SAMPLE_PATH = (
    Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources")
    / "Primal-Hunter"
    / "Audio Samples"
    / "narrator-b13-c1-20.wav"
)


@dataclass
class SegmentEntry:
    segment_index: int
    kind: str
    text: str
    count: int
    examples: list[dict[str, Any]]
    source_output: str


def parse_args() -> argparse.Namespace:
    this_file = Path(__file__).resolve()
    default_runner = this_file.parent / "run_chapter_audio_test.py"

    parser = argparse.ArgumentParser(
        description=(
            "Generate batch audio clips from the Book-17 lvl segment report, split "
            "them with ASR, and copy the clips into a reusable output directory."
        )
    )
    parser.add_argument("--book-dir", type=Path, default=DEFAULT_BOOK_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--sample-path", type=Path, default=DEFAULT_SAMPLE_PATH)
    parser.add_argument("--runner-script", type=Path, default=default_runner)
    parser.add_argument("--python-bin", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for batch artifacts and copied clip outputs.",
    )
    parser.add_argument("--batch-size", type=int, default=120)
    parser.add_argument("--engine", choices=["openai", "whisperx"], default="whisperx")
    parser.add_argument(
        "--split-validation-engine",
        choices=["same", "openai", "whisperx"],
        default="whisperx",
    )
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument(
        "--compute-type",
        choices=["auto", "float16", "int8", "int8_float16", "float32"],
        default="float16",
    )
    parser.add_argument("--batch-size-asr", type=int, default=0)
    parser.add_argument("--max-segments", type=int, default=0)
    parser.add_argument(
        "--text-separator",
        default=" ...",
        help=(
            "Separator appended to each generated segment text to encourage "
            "a stronger pause in TTS output. Set empty string to disable."
        ),
    )
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
            "stable clip directory."
        ),
    )
    return parser.parse_args()


def safe_name(value: str) -> str:
    cleaned = []
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
        else:
            cleaned.append("-")
    return "".join(cleaned).strip("-") or "segment"


def load_report(report_path: Path) -> dict[str, Any]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    segment_library = payload.get("segmentLibrary")
    if not isinstance(segment_library, list):
        raise ValueError(f"Invalid lvl report format: missing segmentLibrary[]: {report_path}")
    return payload


def build_segment_entries(report_payload: dict[str, Any]) -> list[SegmentEntry]:
    entries: list[SegmentEntry] = []
    segment_library = report_payload.get("segmentLibrary", [])

    for index, segment in enumerate(segment_library, start=1):
        if not isinstance(segment, dict):
            continue
        kind = str(segment.get("kind", "plain")).strip() or "plain"
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        count = int(segment.get("count", 0) or 0)
        examples_value = segment.get("examples")
        examples = examples_value if isinstance(examples_value, list) else []
        entries.append(
            SegmentEntry(
                segment_index=index,
                kind=kind,
                text=text,
                count=count,
                examples=[item for item in examples if isinstance(item, dict)],
                source_output=f"Narrator/{index:05d}-Narrator.wav",
            )
        )

    return entries


def chunk_entries(entries: list[SegmentEntry], batch_size: int) -> list[list[SegmentEntry]]:
    if batch_size <= 0:
        raise ValueError("--batch-size must be > 0")
    return [entries[index : index + batch_size] for index in range(0, len(entries), batch_size)]


def build_runtime_env(workspace_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    cudnn_bin = workspace_root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin"
    if cudnn_bin.exists():
        env["PATH"] = str(cudnn_bin) + os.pathsep + env.get("PATH", "")
    return env


def _render_generation_text(text: str, separator: str) -> str:
    normalized_text = str(text).strip()
    normalized_separator = str(separator)
    if not normalized_separator:
        return normalized_text

    if normalized_text.endswith(normalized_separator.strip()):
        return normalized_text

    if normalized_separator.startswith(" "):
        return f"{normalized_text}{normalized_separator}"
    return f"{normalized_text} {normalized_separator}".strip()


def write_batch_source_files(
    source_dir: Path,
    entries: list[SegmentEntry],
    *,
    text_separator: str,
) -> dict[str, Path]:
    if source_dir.exists():
        shutil.rmtree(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)

    chapter_text_path = source_dir / "chapter.txt"
    manifest_path = source_dir / "manifest.json"
    index_path = source_dir / "batch_index.json"

    chapter_text_path.write_text(
        "\n\n".join(_render_generation_text(entry.text, text_separator) for entry in entries)
        + "\n",
        encoding="utf-8",
    )

    manifest_path.write_text(
        json.dumps(
            {
                "formatVersion": "book17-lvl-source/v1",
                "lines": [
                    {
                        "id": index + 1,
                        "characterId": "narrator",
                        "text": _render_generation_text(entry.text, text_separator),
                        "output": entry.source_output,
                        "sourceSegment": {
                            "segmentIndex": entry.segment_index,
                            "kind": entry.kind,
                        },
                    }
                    for index, entry in enumerate(entries)
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    index_path.write_text(
        json.dumps(
            {
                "formatVersion": "book17-lvl-batch-index/v1",
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
        "index": index_path,
    }


def build_runner_command(
    *,
    args: argparse.Namespace,
    source_dir: Path,
    source_paths: dict[str, Path],
    output_dir: Path,
    full_audio_path: Path,
) -> list[str]:
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
        "--no-line-chunk-generate",
    ]

    if args.batch_size_asr > 0:
        command.extend(["--batch-size", str(args.batch_size_asr)])

    return command


def clear_generated_outputs(output_dir: Path) -> None:
    for path in [
        output_dir / "clips",
        output_dir / "lvl_audio_batches.json",
        output_dir / "lvl_audio_clips.json",
        output_dir / "lvl_audio_clips.txt",
    ]:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    for batch_dir in output_dir.glob("batch_*"):
        if batch_dir.is_dir():
            shutil.rmtree(batch_dir)


def load_batch_index(index_path: Path) -> dict[int, dict[str, Any]]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError(f"Invalid batch index format: {index_path}")

    by_line_id: dict[int, dict[str, Any]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        segment_index = entry.get("segment_index")
        if isinstance(segment_index, int):
            by_line_id[segment_index] = entry
    return by_line_id


def copy_batch_clips(
    *,
    batch_dir: Path,
    clip_dir: Path,
) -> list[dict[str, Any]]:
    result_manifest_path = batch_dir / "audio_test_manifest.json"
    index_path = batch_dir / "source" / "batch_index.json"
    if not result_manifest_path.exists():
        raise FileNotFoundError(f"Split manifest not found: {result_manifest_path}")
    if not index_path.exists():
        raise FileNotFoundError(f"Batch index not found: {index_path}")

    manifest_payload = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    raw_lines = manifest_payload.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError(f"Invalid split manifest format: {result_manifest_path}")

    entries_by_line_id = load_batch_index(index_path)
    clip_dir.mkdir(parents=True, exist_ok=True)

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
        if not audio_path.exists():
            raise FileNotFoundError(f"Split audio file not found: {audio_path}")

        destination_path = clip_dir / (
            f"seg-{int(batch_entry['segment_index']):05d}-"
            f"{safe_name(str(batch_entry['kind']))}.wav"
        )
        shutil.copyfile(audio_path, destination_path)

        copied.append(
            {
                "segmentIndex": int(batch_entry["segment_index"]),
                "kind": str(batch_entry["kind"]),
                "text": str(batch_entry["text"]),
                "count": int(batch_entry.get("count", 0) or 0),
                "examples": batch_entry.get("examples", []),
                "sourceAudioPath": str(audio_path),
                "clipPath": str(destination_path),
                "batchLineId": line_id,
            }
        )

    return copied


def write_clip_reports(output_dir: Path, copied_clips: list[dict[str, Any]]) -> None:
    (output_dir / "lvl_audio_clips.json").write_text(
        json.dumps(
            {
                "formatVersion": "book17-lvl-audio-clips/v1",
                "count": len(copied_clips),
                "clips": copied_clips,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    lines = [
        f"seg-{clip['segmentIndex']:05d}-{clip['kind']}: {clip['text']}"
        for clip in copied_clips
    ]
    (output_dir / "lvl_audio_clips.txt").write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()

    book_dir = args.book_dir.resolve()
    report_path = args.report_path.resolve()
    sample_path = args.sample_path.resolve()
    runner_script = args.runner_script.resolve()
    python_bin = args.python_bin.resolve()

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else (book_dir / "_lvl_audio_batches").resolve()
    )
    clip_dir = output_dir / "clips"

    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")
    if not report_path.exists():
        raise FileNotFoundError(f"Lvl report not found: {report_path}")
    if not sample_path.exists():
        raise FileNotFoundError(f"Voice sample not found: {sample_path}")
    if not runner_script.exists():
        raise FileNotFoundError(f"Runner script not found: {runner_script}")
    if not python_bin.exists():
        raise FileNotFoundError(f"Python executable not found: {python_bin}")

    report_payload = load_report(report_path)
    segment_entries = build_segment_entries(report_payload)
    if args.max_segments > 0:
        segment_entries = segment_entries[: args.max_segments]

    output_dir.mkdir(parents=True, exist_ok=True)
    if not args.reuse_existing_batches and not args.dry_run:
        clear_generated_outputs(output_dir)

    batches = chunk_entries(segment_entries, args.batch_size) if segment_entries else []

    print(f"Book dir: {book_dir}")
    print(f"Report: {report_path}")
    print(f"Segments: {len(segment_entries)}")
    print(f"Batch count: {len(batches)}")

    if args.dry_run or not segment_entries:
        return 0

    workspace_root = REPO_ROOT
    env = build_runtime_env(workspace_root)
    all_clips: list[dict[str, Any]] = []
    batch_records: list[dict[str, Any]] = []

    for batch_index, batch_entries in enumerate(batches, start=1):
        batch_id = f"batch_{batch_index:04d}"
        batch_dir = output_dir / batch_id
        batch_manifest_path = batch_dir / "audio_test_manifest.json"
        source_dir = batch_dir / "source"
        full_audio_path = batch_dir / f"{book_dir.name}-{batch_id}-full.wav"

        source_paths = write_batch_source_files(
            source_dir,
            batch_entries,
            text_separator=args.text_separator,
        )
        command = build_runner_command(
            args=args,
            source_dir=source_dir,
            source_paths=source_paths,
            output_dir=batch_dir,
            full_audio_path=full_audio_path,
        )

        should_reuse = args.reuse_existing_batches and batch_manifest_path.exists()

        if not should_reuse and batch_dir.exists():
            shutil.rmtree(batch_dir)
            source_paths = write_batch_source_files(
                source_dir,
                batch_entries,
                text_separator=args.text_separator,
            )
            command = build_runner_command(
                args=args,
                source_dir=source_dir,
                source_paths=source_paths,
                output_dir=batch_dir,
                full_audio_path=full_audio_path,
            )

        if should_reuse:
            print(f"Reusing existing generated artifacts for {batch_id}: {batch_manifest_path}")
        else:
            print(f"Running {batch_id} ({len(batch_entries)} segments)")
            subprocess.run(command, cwd=str(workspace_root), env=env, check=True)

        copied_clips: list[dict[str, Any]] = []
        if not args.skip_clip_copy:
            copied_clips = copy_batch_clips(batch_dir=batch_dir, clip_dir=clip_dir)
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

    (output_dir / "lvl_audio_batches.json").write_text(
        json.dumps(
            {
                "formatVersion": "book17-lvl-audio-batches/v1",
                "bookDir": str(book_dir),
                "reportPath": str(report_path),
                "segmentCount": len(segment_entries),
                "batchCount": len(batches),
                "clipCount": len(all_clips),
                "batches": batch_records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if not args.skip_clip_copy:
        write_clip_reports(output_dir, all_clips)

    print(f"Wrote batch summary: {output_dir / 'lvl_audio_batches.json'}")
    if not args.skip_clip_copy:
        print(f"Wrote clip index: {output_dir / 'lvl_audio_clips.json'}")
        print(f"Reusable clips: {len(all_clips)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
