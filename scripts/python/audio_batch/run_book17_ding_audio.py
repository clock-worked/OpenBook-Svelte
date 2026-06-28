# pylint: disable=C0115,C0116
# ruff: noqa: D101, D103, E402

"""Generate normalized DING clips in batches and replace chapter WAVs.

Workflow:
1. Scan Primal-Hunter Book-17 dialogue.json files for DING stat lines.
2. Normalize those lines into TTS-friendly phrasing.
3. Generate batch full-audio files and split by ASR with run_chapter_audio_test.py.
4. Prefix each split clip with ding.wav + 1 second silence.
5. Replace destination chapter WAVs (with backups).
"""

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

import numpy as np
import soundfile as sf


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

REPO_ROOT = Path(__file__).resolve().parents[3]


CHAPTER_FOLDER_PATTERN = re.compile(r"^(?P<number>\d+)\s*-\s*.+$")
DING_PREFIX_PATTERN = re.compile(
    r"^[\s\"'`\u2018\u2019\u201c\u201d\u2026\.\-]*DING!",
    re.IGNORECASE,
)
DING_LINE_PATTERN = re.compile(
    r"^[\s\"'`\u2018\u2019\u201c\u201d]*DING![\s\"'`\u2018\u2019\u201c\u201d]*"
    r"(?P<kind>Class|Race|Profession):\s*\[(?P<name>[^\]]+)\]\s*"
    r"has\s+reached\s+level\s+(?P<level>\d+)\s*-\s*"
    r"Stat\s+points\s+allocated,\s*\+(?P<free_points>\d+)\s+Free\s+Points\s*$",
    re.IGNORECASE,
)

ONES = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}

TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}


@dataclass
class DingEntry:
    chapter_dir_name: str
    chapter_path: str
    line_id: int
    source_text: str
    normalized_text: str
    kind: str
    bracket_name: str
    level: int
    free_points: int
    spoken_text: str
    source_output: str
    target_wav_path: str


def parse_args() -> argparse.Namespace:
    this_file = Path(__file__).resolve()
    default_runner = this_file.parent / "run_chapter_audio_test.py"
    default_book_dir = (
        Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources")
        / "Primal-Hunter"
        / "Book-17"
    )
    default_sample_path = (
        Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources")
        / "Primal-Hunter"
        / "Audio Samples"
        / "narrator-b13-c1-20.wav"
    )
    default_ding_wav = (
        Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources")
        / "Primal-Hunter"
        / "Audio Snippets"
        / "ding.wav"
    )

    parser = argparse.ArgumentParser(
        description=(
            "Generate normalized DING lines in batches, ASR-split them, prepend "
            "ding.wav + 1s silence, and replace chapter audio lines."
        )
    )
    parser.add_argument("--book-dir", type=Path, default=default_book_dir)
    parser.add_argument("--sample-path", type=Path, default=default_sample_path)
    parser.add_argument("--ding-wav", type=Path, default=default_ding_wav)
    parser.add_argument("--runner-script", type=Path, default=default_runner)
    parser.add_argument("--python-bin", type=Path, default=Path(sys.executable))
    parser.add_argument("--batch-size", type=int, default=120)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for temporary batch artifacts and reports.",
    )
    parser.add_argument(
        "--engine",
        choices=["openai", "whisperx"],
        default="whisperx",
    )
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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--max-lines",
        type=int,
        default=0,
        help="Optional cap for quick tests (0 means all lines).",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Generate/split only; do not overwrite destination chapter WAV files.",
    )
    parser.add_argument(
        "--reuse-existing-batches",
        action="store_true",
        help=(
            "Reuse existing batch artifacts when audio_test_manifest.json already "
            "exists (skip rerunning generation/split for that batch)."
        ),
    )
    parser.add_argument(
        "--replace-from-existing",
        action="store_true",
        help=(
            "Strict replacement-only mode: never generate audio, only consume "
            "existing batch_*/audio_test_manifest.json artifacts."
        ),
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Backup location for replaced destination WAVs.",
    )
    return parser.parse_args()


def normalize_ascii_punctuation(text: str) -> str:
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


def two_digit_words(value: int) -> str:
    if value < 20:
        return ONES[value]
    tens_value = (value // 10) * 10
    remainder = value % 10
    if remainder == 0:
        return TENS[tens_value]
    return f"{TENS[tens_value]} {ONES[remainder]}"


def cardinal_number_words(value: int) -> str:
    """Convert integers into standard cardinal English words."""

    if value < 0:
        raise ValueError("Negative values are not supported.")
    if value < 100:
        return two_digit_words(value)
    if value < 1000:
        hundreds = value // 100
        tail = value % 100
        if tail == 0:
            return f"{ONES[hundreds]} hundred"
        return f"{ONES[hundreds]} hundred {two_digit_words(tail)}"

    thousands = value // 1000
    remainder = value % 1000
    if remainder == 0:
        return f"{cardinal_number_words(thousands)} thousand"
    return (
        f"{cardinal_number_words(thousands)} thousand "
        f"{cardinal_number_words(remainder)}"
    )


def level_number_words(value: int) -> str:
    """Convert level values to short spoken style (333 -> three thirty three)."""

    if value < 0:
        raise ValueError("Negative values are not supported.")
    if value < 100:
        return two_digit_words(value)
    if value < 1000:
        hundreds = value // 100
        tail = value % 100
        if tail == 0:
            return f"{ONES[hundreds]} hundred"
        if tail < 10:
            return f"{ONES[hundreds]} oh {ONES[tail]}"
        return f"{ONES[hundreds]} {two_digit_words(tail)}"
    return cardinal_number_words(value)


def race_name_to_spoken(name: str) -> str:
    spoken = re.sub(r"\(([^)]+)\)", r", \1,", name)
    spoken = spoken.replace("-", " ")
    spoken = re.sub(r"\s+,", ",", spoken)
    spoken = re.sub(r",\s*,+", ",", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip(" ,")
    return spoken


def looks_like_ding_line(text: str) -> bool:
    normalized = normalize_ascii_punctuation(text)
    return bool(DING_PREFIX_PATTERN.match(normalized))


def build_spoken_ding(kind: str, name: str, level: int, free_points: int) -> str:
    canonical_kind = kind.title()
    clean_name = normalize_ascii_punctuation(name)
    clean_name = clean_name.replace("[", "").replace("]", "").strip()
    clean_name = clean_name.replace("-", "-")

    if canonical_kind == "Race":
        spoken_name = race_name_to_spoken(clean_name)
        lead = f"Race, {spoken_name},"
    else:
        lead = f"{canonical_kind} {clean_name}"

    level_words = level_number_words(level)
    points_words = cardinal_number_words(free_points)
    return (
        f"{lead} has reached level {level_words}... "
        f"Stat points, allocated. Plus {points_words} free points."
    )


def discover_chapter_dirs(book_dir: Path) -> list[Path]:
    chapters: list[tuple[int, Path]] = []
    for child in sorted(book_dir.iterdir()):
        if not child.is_dir():
            continue
        match = CHAPTER_FOLDER_PATTERN.match(child.name)
        if match is None:
            continue
        if not (child / "dialogue.json").exists():
            continue
        if not (child / "audio_lines" / "manifest.json").exists():
            continue
        chapters.append((int(match.group("number")), child))

    chapters.sort(key=lambda item: (item[0], item[1].name.lower()))
    return [item[1] for item in chapters]


def collect_ding_entries(book_dir: Path) -> tuple[list[DingEntry], list[dict[str, Any]]]:
    entries: list[DingEntry] = []
    skipped: list[dict[str, Any]] = []

    for chapter_path in discover_chapter_dirs(book_dir):
        dialogue_path = chapter_path / "dialogue.json"
        manifest_path = chapter_path / "audio_lines" / "manifest.json"

        dialogue_payload = json.loads(dialogue_path.read_text(encoding="utf-8"))
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))

        dialogue_lines = dialogue_payload.get("lines")
        manifest_lines = manifest_payload.get("lines")
        if not isinstance(dialogue_lines, list) or not isinstance(manifest_lines, list):
            continue

        manifest_by_id: dict[int, dict[str, Any]] = {}
        for line in manifest_lines:
            if not isinstance(line, dict):
                continue
            line_id = line.get("id")
            if isinstance(line_id, int):
                manifest_by_id[line_id] = line

        for line in dialogue_lines:
            if not isinstance(line, dict):
                continue
            line_id = line.get("id")
            text = str(line.get("text", ""))
            if not isinstance(line_id, int) or not text.strip():
                continue
            if not looks_like_ding_line(text):
                continue

            normalized_text = normalize_ascii_punctuation(text)
            match = DING_LINE_PATTERN.match(normalized_text)
            if match is None:
                skipped.append(
                    {
                        "chapter": chapter_path.name,
                        "lineId": line_id,
                        "text": text,
                        "reason": "unmatched_pattern",
                    }
                )
                continue

            manifest_line = manifest_by_id.get(line_id)
            if not isinstance(manifest_line, dict):
                skipped.append(
                    {
                        "chapter": chapter_path.name,
                        "lineId": line_id,
                        "text": text,
                        "reason": "missing_audio_lines_manifest_entry",
                    }
                )
                continue

            output_value = str(manifest_line.get("output", "")).strip()
            if not output_value:
                skipped.append(
                    {
                        "chapter": chapter_path.name,
                        "lineId": line_id,
                        "text": text,
                        "reason": "manifest_output_missing",
                    }
                )
                continue

            kind = match.group("kind").title()
            name = match.group("name").strip()
            level = int(match.group("level"))
            free_points = int(match.group("free_points"))
            spoken_text = build_spoken_ding(kind, name, level, free_points)

            source_output = Path(chapter_path.name) / "audio_lines" / output_value
            target_wav_path = chapter_path / "audio_lines" / output_value

            entries.append(
                DingEntry(
                    chapter_dir_name=chapter_path.name,
                    chapter_path=str(chapter_path.resolve()),
                    line_id=line_id,
                    source_text=text,
                    normalized_text=normalized_text,
                    kind=kind,
                    bracket_name=name,
                    level=level,
                    free_points=free_points,
                    spoken_text=spoken_text,
                    source_output=source_output.as_posix(),
                    target_wav_path=str(target_wav_path.resolve()),
                )
            )

    entries.sort(key=lambda item: (item.chapter_dir_name.lower(), item.line_id))
    return entries, skipped


def write_gather_reports(
    output_dir: Path,
    entries: list[DingEntry],
    skipped: list[dict[str, Any]],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gathered_json = output_dir / "all_ding_lines.json"
    gathered_txt = output_dir / "all_ding_lines.txt"
    skipped_json = output_dir / "all_ding_lines_skipped.json"

    gathered_json.write_text(
        json.dumps(
            {
                "formatVersion": "book17-ding-lines/v1",
                "count": len(entries),
                "lines": [asdict(entry) for entry in entries],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    gathered_txt.write_text(
        "\n".join(
            f"{entry.chapter_dir_name} | line {entry.line_id} | {entry.spoken_text}"
            for entry in entries
        )
        + ("\n" if entries else ""),
        encoding="utf-8",
    )

    skipped_json.write_text(
        json.dumps(
            {
                "formatVersion": "book17-ding-lines-skipped/v1",
                "count": len(skipped),
                "lines": skipped,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "gatheredJson": gathered_json,
        "gatheredText": gathered_txt,
        "skippedJson": skipped_json,
    }


def chunk_entries(entries: list[DingEntry], chunk_size: int) -> list[list[DingEntry]]:
    if chunk_size <= 0:
        raise ValueError("--batch-size must be > 0")
    return [entries[index:index + chunk_size] for index in range(0, len(entries), chunk_size)]


def write_batch_source_files(source_dir: Path, entries: list[DingEntry]) -> dict[str, Path]:
    if source_dir.exists():
        shutil.rmtree(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)

    chapter_text_path = source_dir / "chapter.txt"
    manifest_path = source_dir / "manifest.json"
    index_path = source_dir / "batch_index.json"

    chapter_text_path.write_text(
        "\n\n".join(entry.spoken_text for entry in entries) + "\n",
        encoding="utf-8",
    )

    manifest_payload = {
        "formatVersion": "book17-ding-source/v1",
        "lines": [
            {
                "id": index + 1,
                "characterId": "narrator",
                "text": entry.spoken_text,
                "output": entry.source_output,
                "sourceLine": {
                    "chapter": entry.chapter_dir_name,
                    "lineId": entry.line_id,
                },
            }
            for index, entry in enumerate(entries)
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    index_path.write_text(
        json.dumps(
            {
                "formatVersion": "book17-ding-batch-index/v1",
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


def build_runtime_env(workspace_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    cudnn_bin = workspace_root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin"
    if cudnn_bin.exists():
        env["PATH"] = str(cudnn_bin) + os.pathsep + env.get("PATH", "")
    return env


def add_ding_prefix(
    *,
    split_audio_path: Path,
    ding_wav_path: Path,
    destination_path: Path,
) -> None:
    info = sf.info(str(split_audio_path))
    sample_rate = int(info.samplerate)
    channels = int(info.channels)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = destination_path.with_name(destination_path.name + ".dingtmp.wav")
    temp_ding = destination_path.with_name(destination_path.name + ".dingclip.wav")

    if temp_output.exists():
        temp_output.unlink()
    if temp_ding.exists():
        temp_ding.unlink()

    normalize_ding_command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(ding_wav_path),
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-c:a",
        "pcm_s16le",
        str(temp_ding),
    ]

    subprocess.run(normalize_ding_command, check=True)

    ding_audio, ding_sr = sf.read(str(temp_ding), dtype="float32", always_2d=True)
    split_audio, split_sr = sf.read(str(split_audio_path), dtype="float32", always_2d=True)
    if ding_sr != split_sr:
        raise RuntimeError(
            "Sample-rate mismatch after ding normalization: "
            f"{ding_sr} vs {split_sr}"
        )

    silence = np.zeros((int(sample_rate * 1.0), channels), dtype=np.float32)
    combined = np.concatenate([ding_audio, silence, split_audio], axis=0)
    sf.write(str(temp_output), combined, sample_rate, subtype="PCM_16")

    if temp_ding.exists():
        temp_ding.unlink()
    shutil.move(str(temp_output), str(destination_path))


def _resolve_destination_targets(
    *,
    book_dir: Path,
    source_output: str,
    line: dict[str, Any],
) -> list[Path]:
    """Resolve destination targets, preferring per-character lane files when present."""

    canonical_target = (book_dir / Path(source_output)).resolve()
    targets: list[Path] = [canonical_target]

    relative_output = Path(source_output)
    try:
        chapter_rel = relative_output.parents[1]
    except IndexError:
        return targets

    chapter_dir = (book_dir / chapter_rel).resolve()
    line_id = None
    file_stem = relative_output.stem
    file_match = re.match(r"^(\d+)_", file_stem)
    if file_match is not None:
        line_id = int(file_match.group(1))

    if line_id is None:
        candidate = line.get("lineSourceId") or line.get("sourceLineId")
        if isinstance(candidate, int):
            line_id = candidate
    character_id = str(line.get("characterId", "")).strip() or "narrator"

    if line_id is not None:
        character_folder = character_id.replace("_", " ").replace("-", " ").title().replace(" ", "")
        lane_target = (
            chapter_dir
            / "audio_lines"
            / character_folder
            / f"{line_id}-{character_folder}.wav"
        )
        if lane_target.exists():
            targets = [lane_target, canonical_target]

    deduped: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        key = str(target).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


def _is_split_clip_usable(line: dict[str, Any], split_audio_path: Path) -> tuple[bool, str | None]:
    """Reject clearly bad split clips so they do not overwrite destination files."""

    min_duration_sec = 1.0
    duration = line.get("durationSec")
    if isinstance(duration, (int, float)) and float(duration) < min_duration_sec:
        return False, f"duration-too-short:{float(duration):.3f}s"

    matched_tokens = line.get("matchedTokenCount")
    if isinstance(matched_tokens, int) and matched_tokens <= 0:
        return False, "no-matched-tokens"

    validation = line.get("validation")
    if isinstance(validation, dict):
        status = str(validation.get("status", "")).strip().lower()
        if status in {"empty-transcript"}:
            return False, f"validation-status:{status}"

    try:
        info = sf.info(str(split_audio_path))
    except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
        return False, f"split-info-error:{exc}"

    if float(info.duration) < min_duration_sec:
        return False, f"duration-too-short-file:{float(info.duration):.3f}s"

    return True, None


def apply_batch_replacements(
    *,
    book_dir: Path,
    batch_output_dir: Path,
    ding_wav_path: Path,
    backup_dir: Path,
    no_replace: bool,
) -> list[dict[str, Any]]:
    result_manifest_path = batch_output_dir / "audio_test_manifest.json"
    if not result_manifest_path.exists():
        raise FileNotFoundError(f"Split manifest not found: {result_manifest_path}")

    payload = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError(f"Invalid split manifest format: {result_manifest_path}")

    replacement_records: list[dict[str, Any]] = []

    for line in raw_lines:
        if not isinstance(line, dict):
            continue
        split_audio_path = Path(str(line.get("audioPath", "")).strip())
        source_output = str(line.get("sourceOutput", "")).strip()
        if not source_output:
            continue
        if not split_audio_path.exists():
            raise FileNotFoundError(f"Split audio file not found: {split_audio_path}")

        destination_targets = _resolve_destination_targets(
            book_dir=book_dir,
            source_output=source_output,
            line=line,
        )
        destination_path = destination_targets[0]

        is_usable, unusable_reason = _is_split_clip_usable(line, split_audio_path)
        if not is_usable:
            replacement_records.append(
                {
                    "id": line.get("id"),
                    "sourceAudioPath": str(split_audio_path),
                    "destinationPath": str(destination_path),
                    "destinationTargets": [str(path) for path in destination_targets],
                    "replaced": False,
                    "reason": unusable_reason,
                }
            )
            continue

        if no_replace:
            replacement_records.append(
                {
                    "id": line.get("id"),
                    "sourceAudioPath": str(split_audio_path),
                    "destinationPath": str(destination_path),
                    "destinationTargets": [str(path) for path in destination_targets],
                    "replaced": False,
                    "reason": "no-replace-flag",
                }
            )
            continue

        for target in destination_targets:
            if target.exists():
                backup_rel = target.relative_to(book_dir)
                backup_path = backup_dir / backup_rel
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, backup_path)

            add_ding_prefix(
                split_audio_path=split_audio_path,
                ding_wav_path=ding_wav_path,
                destination_path=target,
            )

        replacement_records.append(
            {
                "id": line.get("id"),
                "sourceAudioPath": str(split_audio_path),
                "destinationPath": str(destination_path),
                "destinationTargets": [str(path) for path in destination_targets],
                "replaced": True,
            }
        )

    return replacement_records


def main() -> None:
    args = parse_args()

    book_dir = args.book_dir.resolve()
    sample_path = args.sample_path.resolve()
    ding_wav_path = args.ding_wav.resolve()
    runner_script = args.runner_script.resolve()
    python_bin = args.python_bin.resolve()

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else (book_dir / "_ding_audio_batches").resolve()
    )
    backup_dir = (
        args.backup_dir.resolve()
        if args.backup_dir is not None
        else (output_dir / "backups").resolve()
    )

    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")
    if not sample_path.exists():
        raise FileNotFoundError(f"Voice sample not found: {sample_path}")
    if not ding_wav_path.exists():
        raise FileNotFoundError(f"Ding WAV not found: {ding_wav_path}")
    if not runner_script.exists():
        raise FileNotFoundError(f"Runner script not found: {runner_script}")
    if not python_bin.exists():
        raise FileNotFoundError(f"Python executable not found: {python_bin}")

    entries, skipped = collect_ding_entries(book_dir)
    if args.max_lines > 0:
        entries = entries[: args.max_lines]

    output_dir.mkdir(parents=True, exist_ok=True)
    report_paths = write_gather_reports(output_dir, entries, skipped)

    print(f"Book dir: {book_dir}")
    print(f"Collected DING lines: {len(entries)}")
    print(f"Skipped DING-like lines: {len(skipped)}")
    print(f"Gathered JSON: {report_paths['gatheredJson']}")
    print(f"Gathered text: {report_paths['gatheredText']}")
    print(f"Skipped JSON: {report_paths['skippedJson']}")

    if not entries:
        return

    batches = chunk_entries(entries, args.batch_size)
    print(f"Batch count: {len(batches)}")

    if args.dry_run:
        return

    workspace_root = REPO_ROOT
    env = build_runtime_env(workspace_root)

    all_replacements: list[dict[str, Any]] = []

    for batch_index, batch_entries in enumerate(batches, start=1):
        batch_id = f"batch_{batch_index:04d}"
        batch_dir = output_dir / batch_id
        source_dir = batch_dir / "source"
        source_paths = write_batch_source_files(source_dir, batch_entries)
        full_audio_path = batch_dir / f"{book_dir.name}-{batch_id}-full.wav"

        command = build_runner_command(
            args=args,
            source_dir=source_dir,
            source_paths=source_paths,
            output_dir=batch_dir,
            full_audio_path=full_audio_path,
        )

        batch_manifest_path = batch_dir / "audio_test_manifest.json"
        should_reuse = args.reuse_existing_batches and batch_manifest_path.exists()

        if args.replace_from_existing:
            if not batch_manifest_path.exists():
                raise FileNotFoundError(
                    "--replace-from-existing was set but required batch manifest "
                    f"is missing: {batch_manifest_path}"
                )
            should_reuse = True

        if not should_reuse and batch_dir.exists():
            for artifact_name in (
                "audio_test_manifest.json",
                "transcription_result.json",
                "split_validation_report.json",
            ):
                artifact_path = batch_dir / artifact_name
                if artifact_path.exists():
                    artifact_path.unlink()
            generated_splits = batch_dir / "splits"
            if generated_splits.exists():
                shutil.rmtree(generated_splits)

        if should_reuse:
            print(
                f"Reusing existing generated artifacts for {batch_id}: "
                f"{batch_manifest_path}"
            )
        else:
            print(f"Running {batch_id} ({len(batch_entries)} lines)")
            subprocess.run(command, cwd=str(workspace_root), env=env, check=True)

        batch_replacements = apply_batch_replacements(
            book_dir=book_dir,
            batch_output_dir=batch_dir,
            ding_wav_path=ding_wav_path,
            backup_dir=backup_dir / batch_id,
            no_replace=bool(args.no_replace),
        )
        all_replacements.extend(batch_replacements)
        print(f"Applied replacements for {batch_id}: {len(batch_replacements)}")

    summary_path = output_dir / "ding_replacement_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "formatVersion": "book17-ding-replacement-summary/v1",
                "bookDir": str(book_dir),
                "lineCount": len(entries),
                "batchCount": len(batches),
                "replacementCount": len(all_replacements),
                "dryRun": False,
                "noReplace": bool(args.no_replace),
                "replacements": all_replacements,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote replacement summary: {summary_path}")


if __name__ == "__main__":
    main()
