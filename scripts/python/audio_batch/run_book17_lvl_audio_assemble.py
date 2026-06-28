"""Assemble Book-17 lvl segment clips back into chapter audio line WAVs.

Workflow:
1. Read the lvl report and reusable clip index written by the lvl batch scripts.
2. Reconstruct each source line by concatenating the matching segment clips.
3. Write the assembled WAV back to the chapter's actual audio_lines target path,
   keeping backups of any existing files.
"""

from __future__ import annotations

# pylint: disable=C0115,C0116
# ruff: noqa: D101, D103, E402, E501
import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))


DEFAULT_BOOK_DIR = (
    Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources")
    / "Primal-Hunter"
    / "Book-17"
)
DEFAULT_BATCH_DIR = DEFAULT_BOOK_DIR / "_lvl_audio_batches"
DEFAULT_REPORT_PATH = DEFAULT_BOOK_DIR / "_lvl_audio_segments" / "lvl_dialogue_report.json"
DEFAULT_CLIP_INDEX_PATH = DEFAULT_BATCH_DIR / "lvl_audio_clips.json"


@dataclass
class AssemblyRecord:
    chapter_dir_name: str
    chapter_path: str
    line_id: int
    target_path: str
    source_text: str
    segment_count: int
    source_segments: list[dict[str, Any]]
    assembled: bool
    reason: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild lvl-bearing lines from reusable segment clips and write the "
            "assembled audio back into the source chapter audio_lines targets."
        )
    )
    parser.add_argument("--book-dir", type=Path, default=DEFAULT_BOOK_DIR)
    parser.add_argument("--batch-dir", type=Path, default=DEFAULT_BATCH_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--clip-index-path", type=Path, default=DEFAULT_CLIP_INDEX_PATH)
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Where to store backups of any replaced chapter audio files.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-lines", type=int, default=0)
    parser.add_argument(
        "--separator-ms",
        type=int,
        default=500,
        help="Silence duration to insert between assembled segment clips.",
    )
    return parser.parse_args()


def normalize_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def safe_name(value: str) -> str:
    cleaned = []
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
        else:
            cleaned.append("-")
    return "".join(cleaned).strip("-") or "segment"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid JSON object: {path}")
    return payload


def load_report(report_path: Path) -> dict[str, Any]:
    payload = load_json(report_path)
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"Invalid lvl report: missing entries[]: {report_path}")
    return payload


def load_clip_index(clip_index_path: Path) -> dict[tuple[str, str], Path]:
    payload = load_json(clip_index_path)
    clips = payload.get("clips")
    if not isinstance(clips, list):
        raise ValueError(f"Invalid clip index: missing clips[]: {clip_index_path}")

    clip_dir = clip_index_path.parent / "clips"
    lookup: dict[tuple[str, str], Path] = {}
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        kind = str(clip.get("kind", "")).strip()
        text = str(clip.get("text", "")).strip()
        clip_path = Path(str(clip.get("clipPath", "")).strip())
        if not kind or not text or not clip_path.exists():
            continue
        lookup[(normalize_key(kind), text.casefold())] = clip_path

    if not lookup and clip_dir.exists():
        for clip_path in sorted(clip_dir.glob("*.wav")):
            stem = clip_path.stem
            if "-" not in stem:
                continue
    return lookup


def discover_chapter_dirs(book_dir: Path) -> list[Path]:
    chapter_dirs: list[Path] = []
    for child in sorted(book_dir.iterdir()):
        if not child.is_dir():
            continue
        if not child.name[:1].isdigit():
            continue
        if not (child / "dialogue.json").exists():
            continue
        if not (child / "audio_lines" / "manifest.json").exists():
            continue
        chapter_dirs.append(child.resolve())
    return chapter_dirs


def read_audio_clip(audio_path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    if not isinstance(audio, np.ndarray) or audio.size == 0:
        raise ValueError(f"Empty audio clip: {audio_path}")
    return audio, int(sample_rate)


def write_audio_clip(audio_path: Path, audio: np.ndarray, sample_rate: int) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(audio_path), audio, int(sample_rate), subtype="PCM_16")


def to_character_folder(character_id: str) -> str:
    clean = str(character_id or "").strip()
    if not clean or clean.lower() == "narrator":
        return "Narrator"
    token = clean.replace("_", " ").replace("-", " ").strip()
    return "".join(piece.title() for piece in token.split()) or "Narrator"


def assemble_line_audio(
    *,
    segments: list[dict[str, Any]],
    clip_lookup: dict[tuple[str, str], Path],
    separator_ms: int,
) -> tuple[np.ndarray, int, list[Path]]:
    audio_parts: list[np.ndarray] = []
    sample_rate: int | None = None
    resolved_paths: list[Path] = []

    for segment in segments:
        kind = str(segment.get("kind", "")).strip()
        text = str(segment.get("text", "")).strip()
        if not kind or not text:
            raise ValueError("Segment entry is missing kind/text")

        clip_path = clip_lookup.get((normalize_key(kind), text.casefold()))
        if clip_path is None:
            raise FileNotFoundError(f"Missing reusable clip for segment: {kind} | {text}")

        clip_audio, clip_sample_rate = read_audio_clip(clip_path)
        if sample_rate is None:
            sample_rate = clip_sample_rate
        elif clip_sample_rate != sample_rate:
            raise ValueError(
                "Sample-rate mismatch while assembling line audio: "
                f"{clip_path} has {clip_sample_rate}, expected {sample_rate}"
            )
        audio_parts.append(clip_audio)
        resolved_paths.append(clip_path)

    if not audio_parts or sample_rate is None:
        raise ValueError("No audio parts available for assembly")

    channels = int(audio_parts[0].shape[1])
    for clip_audio in audio_parts[1:]:
        if int(clip_audio.shape[1]) != channels:
            raise ValueError("Channel-count mismatch while assembling line audio")

    if len(audio_parts) == 1 or separator_ms <= 0:
        combined = np.concatenate(audio_parts, axis=0)
        return combined, sample_rate, resolved_paths

    separator_samples = int(round((separator_ms / 1000.0) * float(sample_rate)))
    channels = int(audio_parts[0].shape[1])
    separator = np.zeros((max(0, separator_samples), channels), dtype=np.float32)

    stitched: list[np.ndarray] = []
    for index, clip_audio in enumerate(audio_parts):
        stitched.append(clip_audio)
        if index < len(audio_parts) - 1 and separator.size > 0:
            stitched.append(separator)

    combined = np.concatenate(stitched, axis=0)
    return combined, sample_rate, resolved_paths


def main() -> int:
    args = parse_args()

    book_dir = args.book_dir.resolve()
    report_path = args.report_path.resolve()
    clip_index_path = args.clip_index_path.resolve()
    backup_dir = (
        args.backup_dir.resolve()
        if args.backup_dir is not None
        else (args.batch_dir.resolve() / "assembled_backups")
    )

    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")
    if not report_path.exists():
        raise FileNotFoundError(f"Lvl report not found: {report_path}")
    if not clip_index_path.exists():
        raise FileNotFoundError(f"Clip index not found: {clip_index_path}")

    report_payload = load_report(report_path)
    clip_lookup = load_clip_index(clip_index_path)
    chapter_dirs = discover_chapter_dirs(book_dir)
    chapter_map = {chapter_dir.name: chapter_dir for chapter_dir in chapter_dirs}

    entries = report_payload.get("entries", [])
    if args.max_lines > 0:
        entries = entries[: args.max_lines]

    print(f"Book dir: {book_dir}")
    print(f"Report: {report_path}")
    print(f"Clip index: {clip_index_path}")
    print(f"Chapter count: {len(chapter_dirs)}")
    print(f"Line count: {len(entries)}")

    if args.dry_run:
        return 0

    records: list[AssemblyRecord] = []
    assembled_count = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        chapter_dir_name = str(entry.get("chapter_dir_name", "")).strip()
        chapter_dir = chapter_map.get(chapter_dir_name)
        if chapter_dir is None:
            records.append(
                AssemblyRecord(
                    chapter_dir_name=chapter_dir_name,
                    chapter_path="",
                    line_id=int(entry.get("line_id", -1)),
                    target_path="",
                    source_text=str(entry.get("source_text", "")),
                    segment_count=0,
                    source_segments=[],
                    assembled=False,
                    reason="missing-chapter-dir",
                )
            )
            continue

        line_id = int(entry.get("line_id", -1))
        character_id = str(entry.get("character_id", "narrator"))
        character_folder = to_character_folder(character_id)
        target_path = (
            chapter_dir
            / "audio_lines"
            / character_folder
            / f"{line_id}-{character_folder}.wav"
        ).resolve()

        source_segments = entry.get("segments")
        if not isinstance(source_segments, list) or not source_segments:
            records.append(
                AssemblyRecord(
                    chapter_dir_name=chapter_dir_name,
                    chapter_path=str(chapter_dir),
                    line_id=line_id,
                    target_path=str(target_path),
                    source_text=str(entry.get("source_text", "")),
                    segment_count=0,
                    source_segments=[],
                    assembled=False,
                    reason="missing-segments",
                )
            )
            continue

        try:
            assembled_audio, sample_rate, _resolved_paths = assemble_line_audio(
                segments=source_segments,
                clip_lookup=clip_lookup,
                separator_ms=max(0, int(args.separator_ms)),
            )
        except (FileNotFoundError, ValueError) as exc:  # pragma: no cover - surfaced in summary
            records.append(
                AssemblyRecord(
                    chapter_dir_name=chapter_dir_name,
                    chapter_path=str(chapter_dir),
                    line_id=line_id,
                    target_path=str(target_path),
                    source_text=str(entry.get("source_text", "")),
                    segment_count=len(source_segments),
                    source_segments=source_segments,
                    assembled=False,
                    reason=str(exc),
                )
            )
            continue

        if target_path.exists():
            backup_rel = target_path.relative_to(book_dir)
            backup_path = backup_dir / backup_rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target_path, backup_path)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        write_audio_clip(target_path, assembled_audio, sample_rate)
        assembled_count += 1

        records.append(
            AssemblyRecord(
                chapter_dir_name=chapter_dir_name,
                chapter_path=str(chapter_dir),
                line_id=line_id,
                target_path=str(target_path),
                source_text=str(entry.get("source_text", "")),
                segment_count=len(source_segments),
                source_segments=source_segments,
                assembled=True,
            )
        )

    summary_path = args.batch_dir.resolve() / "lvl_audio_assembled_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "formatVersion": "book17-lvl-audio-assembled/v1",
                "bookDir": str(book_dir),
                "reportPath": str(report_path),
                "clipIndexPath": str(clip_index_path),
                "assembledCount": assembled_count,
                "lineCount": len(entries),
                "records": [
                    {
                        **{
                            "chapterDirName": record.chapter_dir_name,
                            "chapterPath": record.chapter_path,
                            "lineId": record.line_id,
                            "targetPath": record.target_path,
                            "sourceText": record.source_text,
                            "segmentCount": record.segment_count,
                            "sourceSegments": record.source_segments,
                            "assembled": record.assembled,
                        },
                        **({"reason": record.reason} if record.reason else {}),
                    }
                    for record in records
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"Assembled lines: {assembled_count}")
    print(f"Wrote summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
