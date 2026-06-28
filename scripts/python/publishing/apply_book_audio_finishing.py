"""Apply final silence adjustments to chapter and title audio clips."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


BOOK1_PROVERB_END_OVERRIDES: dict[str, int] = {
    "01-Chapter-1-Knife": 2,
    "02-Chapter-2-Invitation": 1,
    "03-Chapter-3-Party": 1,
    "04-Chapter-4-Name": 1,
    "05-Chapter-5-Role": 1,
    "06-Chapter-6-Aspect": 2,
    "07-Chapter-7-Sword": 1,
    "08-Chapter-8-Introduction": 1,
    "09-Chapter-9-Claimant": 1,
    "10-Chapter-10-Menace": 1,
    "11-Chapter-11-Sucker-Punch": 1,
    "12-Chapter-12-Squire": 1,
    "13-Chapter-13-Order": 1,
    "14-Chapter-14-Villain": 1,
    "15-Chapter-15-Company": 1,
    "16-Chapter-16-Game": 1,
    "17-Chapter-17-Set": 1,
    "18-Chapter-18-Match": 1,
    "19-Chapter-19-Pivot": 1,
    "20-Chapter-20-Rise": 6,
    "21-Chapter-21-Fall": 1,
    "22-Chapter-22-All-According-To": 1,
    "23-Chapter-23-Morok’s-Plan": 1,
    "24-Chapter-24-Aisha’s-Plan": 1,
    "25-Chapter-25-Snatcher’s-Plan": 1,
    "26-Chapter-26-Juniper’s-Plan": 1,
    "27-Chapter-27-Callow’s-Plan": 3,
    "28-Chapter-28-Win-Condition": 1,
    "29-Epilogue": 6,
}

ATTRIBUTION_KEYWORDS = (
    "overheard",
    "extract",
    "dread emperor",
    "dread empress",
    "proverb",
    "saying",
    "last words",
    "last lines",
    "journal",
    "memoirs",
    "commentary",
    "marshal",
    "tyrant",
    "hierarch",
    "king ",
    "queen ",
    "seer",
)


@dataclass
class CharacterManifestState:
    """Track a loaded per-character manifest and whether it changed."""

    path: Path
    payload: dict[str, Any]
    dirty: bool = False


@dataclass
class ClipRef:
    """Describe a generated chapter clip and its owning manifest entry."""

    chapter_name: str
    line_id: int
    text: str
    audio_path: Path
    manifest_state: CharacterManifestState
    clip: dict[str, Any]


@dataclass
class AudioOperation:
    """Accumulate silence changes that should be applied to one audio file."""

    append_silence_sec: float = 0.0
    replace_with_silence_sec: float | None = None
    reasons: list[str] = field(default_factory=list)
    chapter_name: str | None = None
    line_id: int | None = None
    text: str | None = None
    clip_ref: ClipRef | None = None


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the finishing pass."""

    parser = argparse.ArgumentParser(
        description=(
            "Append or replace clip silence for final audiobook prep, including "
            "chapter titles, dash-only clips, proverb endings, and chapter endings."
        )
    )
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument(
        "--title-silence-sec",
        type=float,
        default=3.0,
        help="Silence appended after each chapter title clip.",
    )
    parser.add_argument(
        "--dash-silence-sec",
        type=float,
        default=3.0,
        help="Silence length used to replace dash-only clips.",
    )
    parser.add_argument(
        "--chapter-end-silence-sec",
        type=float,
        default=5.0,
        help="Silence appended to the last clip in each chapter.",
    )
    parser.add_argument(
        "--proverb-end-silence-sec",
        type=float,
        default=3.0,
        help="Silence appended after each chapter's opening proverb attribution.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Directory for backups and the finishing-pass report.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow re-running even if a previous finishing-pass report exists.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_text(value: str) -> str:
    """Normalize whitespace and common punctuation variants."""

    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\ufffd": '"',
    }
    text = value
    for source, target in replacements.items():
        text = text.replace(source, target)
    return " ".join(text.split()).strip()


def is_dash_only_text(value: str) -> bool:
    """Return whether a line contains only a dash-like glyph."""

    text = normalize_text(value)
    return text in {"-", "--", "---"}


def discover_chapters(book_dir: Path) -> list[Path]:
    """Find chapter directories that have chapter-level audio manifests."""

    chapters: list[Path] = []
    for child in sorted(book_dir.iterdir()):
        if not child.is_dir():
            continue
        if (child / "audio_lines" / "manifest.json").exists():
            chapters.append(child)
    return chapters


def load_json(path: Path) -> dict[str, Any]:
    """Load JSON from disk using UTF-8 with replacement for malformed bytes."""

    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def safe_name(value: str) -> str:
    """Convert a value into a filesystem-safe slug."""

    cleaned = "".join(char if char.isalnum() or char in {"_", "-"} else "-" for char in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-")
    return cleaned or "unknown"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to disk with stable formatting."""

    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_character_clip_index(
    chapter_dir: Path,
) -> tuple[dict[int, ClipRef], dict[Path, CharacterManifestState]]:
    """Index chapter clips by line ID from the per-character manifests."""

    audio_root = chapter_dir / "audio_lines"
    clip_index: dict[int, ClipRef] = {}
    manifest_states: dict[Path, CharacterManifestState] = {}

    for manifest_path in sorted(audio_root.glob("*/manifest.json")):
        if manifest_path.parent.name.startswith("_"):
            continue

        payload = load_json(manifest_path)
        state = CharacterManifestState(path=manifest_path, payload=payload)
        manifest_states[manifest_path] = state

        raw_clips = payload.get("clips")
        if not isinstance(raw_clips, list):
            continue

        for clip in raw_clips:
            if not isinstance(clip, dict):
                continue
            line_id = clip.get("id")
            if not isinstance(line_id, int):
                continue
            audio_file = str(clip.get("audioFile", "")).strip()
            if not audio_file:
                continue
            audio_path = (manifest_path.parent / audio_file).resolve()
            clip_index[line_id] = ClipRef(
                chapter_name=chapter_dir.name,
                line_id=line_id,
                text=str(clip.get("text", "")),
                audio_path=audio_path,
                manifest_state=state,
                clip=clip,
            )

    return clip_index, manifest_states


def detect_proverb_end_id(chapter_name: str, lines: list[dict[str, Any]]) -> int | None:
    """Return the line ID where the opening proverb attribution ends."""

    if chapter_name == "00-Prologue":
        return None

    override = BOOK1_PROVERB_END_OVERRIDES.get(chapter_name)
    if override is not None:
        return override

    for line in lines[:12]:
        line_id = line.get("id")
        if not isinstance(line_id, int):
            continue
        text = normalize_text(str(line.get("text", ""))).lower()
        if not text:
            continue
        if text.startswith("-") or any(keyword in text for keyword in ATTRIBUTION_KEYWORDS):
            return line_id

    return None


def resolve_title_path(chapter_dir: Path) -> Path:
    """Resolve a title clip from local or centralized title-audio layouts."""

    local_path = (chapter_dir / "title_audio" / "chapter-title-stephen-fry.wav").resolve()
    if local_path.exists():
        return local_path

    split_dir = (
        chapter_dir.parent
        / "_chapter_title_audio_stephen_fry"
        / "splits"
        / safe_name(chapter_dir.name)
    ).resolve()
    if split_dir.exists():
        wav_paths = sorted(path.resolve() for path in split_dir.glob("*.wav") if path.is_file())
        if len(wav_paths) == 1:
            return wav_paths[0]
        if len(wav_paths) > 1:
            raise RuntimeError(f"Multiple title clips found for {chapter_dir.name}: {split_dir}")

    return local_path


def ensure_report_paths(book_dir: Path, report_dir: Path | None) -> tuple[Path, Path]:
    """Resolve the report and backup directories for this finishing pass."""

    base_dir = (
        report_dir.resolve()
        if report_dir is not None
        else (book_dir / "_audiobook_finish_pass").resolve()
    )
    backups_dir = base_dir / "backups"
    return base_dir, backups_dir


def ensure_backup(file_path: Path, book_dir: Path, backups_dir: Path) -> Path:
    """Copy an original file into the backup tree once."""

    relative_path = file_path.resolve().relative_to(book_dir.resolve())
    backup_path = backups_dir / relative_path
    if backup_path.exists():
        return backup_path

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, backup_path)
    return backup_path


def create_silence(num_samples: int, channels: int) -> np.ndarray:
    """Create a float32 silence buffer with the given shape."""

    safe_samples = max(0, int(num_samples))
    if channels <= 1:
        return np.zeros((safe_samples,), dtype=np.float32)
    return np.zeros((safe_samples, channels), dtype=np.float32)


def append_silence_to_audio(
    audio: np.ndarray,
    sample_rate: int,
    silence_sec: float,
) -> np.ndarray:
    """Return audio with appended trailing silence."""

    silence_samples = int(round(max(0.0, silence_sec) * float(sample_rate)))
    if silence_samples <= 0:
        return audio

    channels = 1 if audio.ndim == 1 else int(audio.shape[1])
    silence = create_silence(silence_samples, channels)
    return np.concatenate([audio, silence], axis=0)


def replace_audio_with_silence(
    audio: np.ndarray,
    sample_rate: int,
    silence_sec: float,
) -> np.ndarray:
    """Replace audio content with pure silence of the requested duration."""

    silence_samples = int(round(max(0.0, silence_sec) * float(sample_rate)))
    channels = 1 if audio.ndim == 1 else int(audio.shape[1])
    return create_silence(silence_samples, channels)


def write_audio_preserving_format(audio_path: Path, audio: np.ndarray, info: sf.SoundFile) -> None:
    """Write an updated audio buffer while preserving container settings."""

    with tempfile.NamedTemporaryFile(
        suffix=audio_path.suffix,
        dir=str(audio_path.parent),
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)

    try:
        sf.write(
            str(temp_path),
            audio,
            int(info.samplerate),
            format=info.format,
            subtype=info.subtype,
        )
        temp_path.replace(audio_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def schedule_append(
    operations: dict[Path, AudioOperation],
    *,
    audio_path: Path,
    amount_sec: float,
    reason: str,
    chapter_name: str,
    line_id: int | None,
    text: str | None,
    clip_ref: ClipRef | None,
) -> None:
    """Add appended silence to an operation entry."""

    operation = operations.setdefault(audio_path, AudioOperation())
    operation.append_silence_sec = round(operation.append_silence_sec + amount_sec, 4)
    operation.reasons.append(reason)
    operation.chapter_name = chapter_name
    operation.line_id = line_id
    operation.text = text
    operation.clip_ref = clip_ref or operation.clip_ref


def schedule_replace(
    operations: dict[Path, AudioOperation],
    *,
    audio_path: Path,
    silence_sec: float,
    reason: str,
    chapter_name: str,
    line_id: int,
    text: str,
    clip_ref: ClipRef,
) -> None:
    """Replace a clip with silence in the operation map."""

    operation = operations.setdefault(audio_path, AudioOperation())
    operation.replace_with_silence_sec = silence_sec
    operation.reasons.append(reason)
    operation.chapter_name = chapter_name
    operation.line_id = line_id
    operation.text = text
    operation.clip_ref = clip_ref


def gather_operations(
    *,
    book_dir: Path,
    title_silence_sec: float,
    dash_silence_sec: float,
    chapter_end_silence_sec: float,
    proverb_end_silence_sec: float,
) -> tuple[dict[Path, AudioOperation], list[str], dict[Path, CharacterManifestState]]:
    """Collect all audio file modifications requested for the book."""

    operations: dict[Path, AudioOperation] = {}
    warnings: list[str] = []
    manifest_states: dict[Path, CharacterManifestState] = {}

    for chapter_dir in discover_chapters(book_dir):
        chapter_manifest_path = chapter_dir / "audio_lines" / "manifest.json"
        chapter_manifest = load_json(chapter_manifest_path)
        lines = chapter_manifest.get("lines")
        if not isinstance(lines, list):
            warnings.append(f"Missing lines[] in {chapter_manifest_path}")
            continue

        clip_index, chapter_manifest_states = load_character_clip_index(chapter_dir)
        manifest_states.update(chapter_manifest_states)

        title_path = resolve_title_path(chapter_dir)
        if title_path.exists():
            schedule_append(
                operations,
                audio_path=title_path,
                amount_sec=title_silence_sec,
                reason="chapter-title",
                chapter_name=chapter_dir.name,
                line_id=None,
                text=f"Title clip for {chapter_dir.name}",
                clip_ref=None,
            )
        else:
            warnings.append(f"Missing title clip: {title_path}")

        if clip_index:
            last_line_id = max(clip_index)
            last_clip = clip_index[last_line_id]
            if last_clip.audio_path.exists():
                schedule_append(
                    operations,
                    audio_path=last_clip.audio_path,
                    amount_sec=chapter_end_silence_sec,
                    reason="chapter-ending",
                    chapter_name=chapter_dir.name,
                    line_id=last_clip.line_id,
                    text=last_clip.text,
                    clip_ref=last_clip,
                )
            else:
                warnings.append(
                    f"Missing last clip for {chapter_dir.name}: {last_clip.audio_path}"
                )
        else:
            warnings.append(f"No indexed character clips found for {chapter_dir.name}")

        proverb_end_id = detect_proverb_end_id(chapter_dir.name, lines)
        if proverb_end_id is not None:
            proverb_clip = clip_index.get(proverb_end_id)
            if proverb_clip is None:
                warnings.append(
                    f"Missing proverb clip for {chapter_dir.name} line {proverb_end_id}"
                )
            elif not proverb_clip.audio_path.exists():
                warnings.append(
                    f"Missing proverb audio file for {chapter_dir.name} line {proverb_end_id}: "
                    f"{proverb_clip.audio_path}"
                )
            else:
                schedule_append(
                    operations,
                    audio_path=proverb_clip.audio_path,
                    amount_sec=proverb_end_silence_sec,
                    reason="proverb-ending",
                    chapter_name=chapter_dir.name,
                    line_id=proverb_clip.line_id,
                    text=proverb_clip.text,
                    clip_ref=proverb_clip,
                )

        for line in lines:
            line_id = line.get("id")
            if not isinstance(line_id, int):
                continue
            text = str(line.get("text", ""))
            if not is_dash_only_text(text):
                continue
            dash_clip = clip_index.get(line_id)
            if dash_clip is None:
                warnings.append(f"Missing dash-only clip for {chapter_dir.name} line {line_id}")
                continue
            if not dash_clip.audio_path.exists():
                warnings.append(
                    f"Missing dash-only audio file for {chapter_dir.name} line {line_id}: "
                    f"{dash_clip.audio_path}"
                )
                continue
            schedule_replace(
                operations,
                audio_path=dash_clip.audio_path,
                silence_sec=dash_silence_sec,
                reason="dash-line-silence",
                chapter_name=chapter_dir.name,
                line_id=dash_clip.line_id,
                text=dash_clip.text,
                clip_ref=dash_clip,
            )

    return operations, warnings, manifest_states


def apply_operations(
    *,
    operations: dict[Path, AudioOperation],
    book_dir: Path,
    backups_dir: Path,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Apply all pending audio operations and return a report payload."""

    results: list[dict[str, Any]] = []

    for audio_path in sorted(operations):
        operation = operations[audio_path]
        audio_info = sf.info(str(audio_path))
        audio, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
        before_duration_sec = float(audio_info.duration)

        updated_audio = audio
        if operation.replace_with_silence_sec is not None:
            updated_audio = replace_audio_with_silence(
                updated_audio,
                sample_rate,
                operation.replace_with_silence_sec,
            )
        if operation.append_silence_sec > 0.0:
            updated_audio = append_silence_to_audio(
                updated_audio,
                sample_rate,
                operation.append_silence_sec,
            )

        after_duration_sec = round(float(updated_audio.shape[0]) / float(sample_rate), 4)

        backup_path: str | None = None
        if not dry_run:
            backup = ensure_backup(audio_path, book_dir, backups_dir)
            backup_path = str(backup)
            write_audio_preserving_format(audio_path, updated_audio, audio_info)

            if operation.clip_ref is not None:
                metadata = operation.clip_ref.clip.setdefault("metadata", {})
                if isinstance(metadata, dict):
                    metadata["duration"] = after_duration_sec
                    metadata["manualSilenceAdjustedAt"] = utc_now_iso()
                    metadata["manualSilenceAdjustments"] = {
                        "appendedSec": round(operation.append_silence_sec, 4),
                        "replacedWithSilenceSec": operation.replace_with_silence_sec,
                        "reasons": list(operation.reasons),
                    }
                    operation.clip_ref.manifest_state.dirty = True

        results.append(
            {
                "audioPath": str(audio_path),
                "backupPath": backup_path,
                "chapter": operation.chapter_name,
                "lineId": operation.line_id,
                "text": operation.text,
                "reasons": list(operation.reasons),
                "beforeDurationSec": round(before_duration_sec, 4),
                "afterDurationSec": after_duration_sec,
                "appendSilenceSec": round(operation.append_silence_sec, 4),
                "replaceWithSilenceSec": operation.replace_with_silence_sec,
            }
        )

    return results


def persist_manifest_updates(
    manifest_states: dict[Path, CharacterManifestState],
    dry_run: bool,
) -> list[str]:
    """Write back any per-character manifests whose clip metadata changed."""

    updated_paths: list[str] = []
    if dry_run:
        return updated_paths

    for state in manifest_states.values():
        if not state.dirty:
            continue
        metadata = state.payload.get("metadata")
        if isinstance(metadata, dict):
            metadata["lastUpdated"] = utc_now_iso()
        write_json(state.path, state.payload)
        updated_paths.append(str(state.path))

    return updated_paths


def build_report(
    *,
    book_dir: Path,
    operations: dict[Path, AudioOperation],
    results: list[dict[str, Any]],
    warnings: list[str],
    manifest_updates: list[str],
) -> dict[str, Any]:
    """Construct the finishing-pass report payload."""

    reasons_summary: dict[str, int] = {}
    for operation in operations.values():
        for reason in operation.reasons:
            reasons_summary[reason] = reasons_summary.get(reason, 0) + 1

    return {
        "formatVersion": "book-audio-finishing/v1",
        "createdAt": utc_now_iso(),
        "bookDir": str(book_dir.resolve()),
        "summary": {
            "fileCount": len(results),
            "warningCount": len(warnings),
            "manifestUpdateCount": len(manifest_updates),
            "reasons": reasons_summary,
        },
        "warnings": warnings,
        "manifestUpdates": manifest_updates,
        "files": results,
    }


def main() -> None:
    """Run the finishing pass across the selected book directory."""

    args = parse_args()
    book_dir = args.book_dir.resolve()
    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")

    report_dir, backups_dir = ensure_report_paths(book_dir, args.report_dir)
    report_path = report_dir / "report.json"
    if report_path.exists() and not args.force:
        raise FileExistsError(
            "A finishing-pass report already exists. Use --force to run again: "
            f"{report_path}"
        )

    operations, warnings, manifest_states = gather_operations(
        book_dir=book_dir,
        title_silence_sec=float(args.title_silence_sec),
        dash_silence_sec=float(args.dash_silence_sec),
        chapter_end_silence_sec=float(args.chapter_end_silence_sec),
        proverb_end_silence_sec=float(args.proverb_end_silence_sec),
    )

    if not operations:
        raise RuntimeError("No audio operations were scheduled.")

    report_dir.mkdir(parents=True, exist_ok=True)
    if not args.dry_run:
        backups_dir.mkdir(parents=True, exist_ok=True)

    results = apply_operations(
        operations=operations,
        book_dir=book_dir,
        backups_dir=backups_dir,
        dry_run=bool(args.dry_run),
    )
    manifest_updates = persist_manifest_updates(manifest_states, dry_run=bool(args.dry_run))
    report = build_report(
        book_dir=book_dir,
        operations=operations,
        results=results,
        warnings=warnings,
        manifest_updates=manifest_updates,
    )
    write_json(report_path, report)

    print(f"Scheduled files: {len(results)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Manifest updates: {len(manifest_updates)}")
    print(f"Report: {report_path}")
    if not args.dry_run:
        print(f"Backups: {backups_dir}")


if __name__ == "__main__":
    main()
