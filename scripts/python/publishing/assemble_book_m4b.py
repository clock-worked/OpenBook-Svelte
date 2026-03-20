"""Assemble chapter and full-book M4B files from generated line clips."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import soundfile as sf


CHAPTER_FOLDER_PATTERN = re.compile(r"^(?P<number>\d+)-(?P<slug>.+)$")


@dataclass
class ClipCandidate:
    """Describe one possible audio file that could satisfy a chapter line."""

    line_id: int
    chapter_name: str
    folder_name: str
    character_id: str
    character_name: str
    source_file: str
    audio_path: Path
    text: str


@dataclass
class ResolvedLineClip:
    """Describe the selected audio file for a chapter line."""

    line_id: int
    text: str
    character_id: str
    audio_path: Path
    selected_from: int
    duplicate_candidates: list[str]


@dataclass
class ChapterPlan:
    """Represent the ordered audio plan for one chapter build."""

    chapter_dir: Path
    chapter_number: int
    chapter_name: str
    display_title: str
    title_audio_path: Path
    ordered_segments: list[Path]
    resolved_lines: list[ResolvedLineClip]
    duplicate_resolutions: list[dict[str, Any]]


@dataclass
class ChapterResult:
    """Describe one rendered chapter and its output artifacts."""

    chapter_name: str
    display_title: str
    chapter_number: int
    segment_count: int
    duplicate_resolution_count: int
    wav_path: str
    m4b_path: str
    duration_sec: float


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for M4B assembly."""

    parser = argparse.ArgumentParser(
        description=(
            "Assemble per-chapter and full-book M4B files using the chapter title "
            "clip followed by all line clips in ascending line-number order."
        )
    )
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for assembled outputs and intermediate artifacts.",
    )
    parser.add_argument(
        "--book-title",
        default=None,
        help="Override the embedded book title metadata.",
    )
    parser.add_argument(
        "--author",
        default="ErraticErrata",
        help="Author metadata embedded in the outputs.",
    )
    parser.add_argument(
        "--narrator",
        default="Stephen Fry",
        help="Narrator metadata embedded in the outputs.",
    )
    parser.add_argument(
        "--genre",
        default="Audiobook",
        help="Genre metadata embedded in the outputs.",
    )
    parser.add_argument(
        "--bitrate",
        default="96k",
        help="AAC bitrate used for chapter and full-book M4Bs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite any existing assembled outputs.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def safe_name(value: str) -> str:
    """Convert a value into a filesystem-safe slug."""

    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "unknown"


def normalize_identity(value: str) -> str:
    """Normalize character identifiers for duplicate-resolution matching."""

    return re.sub(r"[^a-z0-9]+", "", value.lower())


def humanize_slug(value: str) -> str:
    """Convert a hyphenated slug into a readable title segment."""

    cleaned = value.replace("\u2019", "'")
    return " ".join(part for part in cleaned.split("-") if part)


def humanize_book_title(book_dir: Path) -> str:
    """Derive a display title from the book directory and its parent folder."""

    series_name = humanize_slug(book_dir.parent.name)
    book_name = humanize_slug(book_dir.name)
    if series_name and book_name:
        return f"{series_name} - {book_name}"
    return book_name or series_name or book_dir.name


def derive_display_chapter_title(chapter_name: str) -> tuple[int, str]:
    """Turn a chapter folder name into a human-readable chapter title."""

    match = CHAPTER_FOLDER_PATTERN.match(chapter_name)
    if match is None:
        raise ValueError(f"Unexpected chapter folder name: {chapter_name}")

    chapter_number = int(match.group("number"))
    slug = match.group("slug")
    slug_title = humanize_slug(slug)
    if slug.lower() == "prologue":
        return chapter_number, "Prologue"
    if slug.lower() == "epilogue":
        return chapter_number, "Epilogue"

    parts = [part for part in slug_title.split() if part]
    if len(parts) >= 3 and parts[0].lower() == "chapter" and parts[1].isdigit():
        title_text = " ".join(parts[2:]).strip()
        if title_text:
            return chapter_number, f"Chapter {parts[1]}: {title_text}"
        return chapter_number, f"Chapter {parts[1]}"

    return chapter_number, slug_title or chapter_name


def discover_chapters(book_dir: Path) -> list[Path]:
    """Return sorted chapter directories that have chapter audio manifests."""

    chapters: list[tuple[int, Path]] = []
    for child in sorted(book_dir.iterdir()):
        if not child.is_dir():
            continue
        match = CHAPTER_FOLDER_PATTERN.match(child.name)
        if match is None:
            continue
        if not (child / "audio_lines" / "manifest.json").exists():
            continue
        chapters.append((int(match.group("number")), child))

    chapters.sort(key=lambda item: (item[0], item[1].name.lower()))
    return [item[1] for item in chapters]


def load_json(path: Path) -> dict[str, Any]:
    """Load JSON using UTF-8 with replacement for malformed bytes."""

    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def ffmetadata_escape(value: str) -> str:
    """Escape a string for use in an ffmetadata file."""

    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace(";", "\\;")
    escaped = escaped.replace("#", "\\#")
    escaped = escaped.replace("=", "\\=")
    return escaped.replace("\n", "\\n")


def load_clip_candidates(chapter_dir: Path) -> dict[int, list[ClipCandidate]]:
    """Collect all available line clips from per-character manifests."""

    candidates_by_id: dict[int, list[ClipCandidate]] = {}
    audio_root = chapter_dir / "audio_lines"

    for manifest_path in sorted(audio_root.glob("*/manifest.json")):
        folder_name = manifest_path.parent.name
        if folder_name.startswith("_"):
            continue

        payload = load_json(manifest_path)
        raw_clips = payload.get("clips")
        if not isinstance(raw_clips, list):
            continue

        manifest_character_id = str(payload.get("characterId", "")).strip()
        manifest_character_name = str(payload.get("characterName", "")).strip()

        for clip in raw_clips:
            if not isinstance(clip, dict):
                continue
            line_id = clip.get("id")
            audio_file = str(clip.get("audioFile", "")).strip()
            if not isinstance(line_id, int) or not audio_file:
                continue
            audio_path = (manifest_path.parent / audio_file).resolve()
            candidate = ClipCandidate(
                line_id=line_id,
                chapter_name=chapter_dir.name,
                folder_name=folder_name,
                character_id=str(clip.get("characterId", manifest_character_id)).strip(),
                character_name=str(
                    clip.get("characterName", manifest_character_name)
                ).strip(),
                source_file=str(clip.get("sourceFile", "")).strip(),
                audio_path=audio_path,
                text=str(clip.get("text", "")).strip(),
            )
            candidates_by_id.setdefault(line_id, []).append(candidate)

    return candidates_by_id


def score_candidate(candidate: ClipCandidate, line: dict[str, Any]) -> int:
    """Assign a deterministic selection score to a candidate clip."""

    target_id = str(line.get("characterId", "")).strip()
    target_output = str(line.get("output", "")).strip()
    target_folder = ""
    if "/" in target_output:
        target_folder = target_output.split("/", 1)[0].strip()

    score = 0
    candidate_values = [
        candidate.character_id,
        candidate.character_name,
        candidate.folder_name,
    ]

    for value in candidate_values:
        if not value:
            continue
        if value == target_id:
            score += 120
        if target_folder and value == target_folder:
            score += 110
        if safe_name(value) == safe_name(target_id):
            score += 70
        if target_folder and safe_name(value) == safe_name(target_folder):
            score += 60
        if normalize_identity(value) == normalize_identity(target_id):
            score += 40
        if target_folder and normalize_identity(value) == normalize_identity(target_folder):
            score += 30

    if candidate.source_file.endswith("/chapter.txt"):
        score += 10
    if candidate.audio_path.suffix.lower() == ".wav":
        score += 5

    return score


def resolve_line_clip(
    chapter_name: str,
    line: dict[str, Any],
    candidates: list[ClipCandidate],
) -> ResolvedLineClip:
    """Choose the best audio clip for a chapter line."""

    if not candidates:
        raise FileNotFoundError(
            f"No clip candidates found for {chapter_name} line {line.get('id')}"
        )

    scored = [
        (score_candidate(candidate, line), candidate)
        for candidate in candidates
        if candidate.audio_path.exists()
    ]
    if not scored:
        raise FileNotFoundError(
            f"All clip candidates are missing on disk for {chapter_name} line {line.get('id')}"
        )

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].audio_path.suffix.lower() != ".wav",
            str(item[1].audio_path).lower(),
        )
    )

    best_score, best_candidate = scored[0]
    top_candidates = [candidate for score, candidate in scored if score == best_score]
    if len(top_candidates) > 1:
        best_norm = {
            normalize_identity(best_candidate.character_id),
            normalize_identity(best_candidate.folder_name),
        }
        for candidate in top_candidates[1:]:
            other_norm = {
                normalize_identity(candidate.character_id),
                normalize_identity(candidate.folder_name),
            }
            if other_norm != best_norm:
                raise RuntimeError(
                    "Unresolved duplicate candidates for "
                    f"{chapter_name} line {line.get('id')}: "
                    f"{best_candidate.audio_path} vs {candidate.audio_path}"
                )

    return ResolvedLineClip(
        line_id=int(line["id"]),
        text=str(line.get("text", "")).strip(),
        character_id=str(line.get("characterId", "")).strip(),
        audio_path=best_candidate.audio_path,
        selected_from=len(candidates),
        duplicate_candidates=[str(candidate.audio_path) for candidate in candidates],
    )


def build_chapter_plan(chapter_dir: Path) -> ChapterPlan:
    """Create the ordered audio segment plan for one chapter."""

    chapter_manifest_path = chapter_dir / "audio_lines" / "manifest.json"
    chapter_manifest = load_json(chapter_manifest_path)
    raw_lines = chapter_manifest.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError(f"Invalid chapter manifest: {chapter_manifest_path}")

    chapter_number, display_title = derive_display_chapter_title(chapter_dir.name)
    title_audio_path = (chapter_dir / "title_audio" / "chapter-title-stephen-fry.wav").resolve()
    if not title_audio_path.exists():
        raise FileNotFoundError(f"Missing title clip: {title_audio_path}")

    candidates_by_id = load_clip_candidates(chapter_dir)
    ordered_lines = sorted(
        [
            line
            for line in raw_lines
            if isinstance(line, dict) and str(line.get("text", "")).strip()
        ],
        key=lambda item: int(item.get("id", -1)),
    )

    resolved_lines: list[ResolvedLineClip] = []
    duplicate_resolutions: list[dict[str, Any]] = []
    ordered_segments = [title_audio_path]

    for line in ordered_lines:
        line_id = int(line["id"])
        resolved = resolve_line_clip(
            chapter_name=chapter_dir.name,
            line=line,
            candidates=candidates_by_id.get(line_id, []),
        )
        ordered_segments.append(resolved.audio_path)
        resolved_lines.append(resolved)
        if resolved.selected_from > 1:
            duplicate_resolutions.append(
                {
                    "lineId": resolved.line_id,
                    "text": resolved.text,
                    "selected": str(resolved.audio_path),
                    "candidates": list(resolved.duplicate_candidates),
                }
            )

    return ChapterPlan(
        chapter_dir=chapter_dir,
        chapter_number=chapter_number,
        chapter_name=chapter_dir.name,
        display_title=display_title,
        title_audio_path=title_audio_path,
        ordered_segments=ordered_segments,
        resolved_lines=resolved_lines,
        duplicate_resolutions=duplicate_resolutions,
    )


def transcode_for_concat(
    *,
    audio_path: Path,
    output_path: Path,
    sample_rate: int,
    channels: int,
) -> Path:
    """Convert an audio file into the target PCM format for concatenation."""

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-vn",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return output_path


def render_concat_wav(
    *,
    source_paths: list[Path],
    output_path: Path,
    temp_dir: Path,
) -> tuple[float, int, int]:
    """Write a concatenated WAV file from the ordered source clips."""

    if not source_paths:
        raise ValueError("No source paths supplied for concatenation.")

    first_info = sf.info(str(source_paths[0]))
    target_rate = int(first_info.samplerate)
    target_channels = int(first_info.channels)
    transcoded_cache: dict[tuple[Path, int, int], Path] = {}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = 0

    with sf.SoundFile(
        str(output_path),
        mode="w",
        samplerate=target_rate,
        channels=target_channels,
        format="WAV",
        subtype="PCM_16",
    ) as sink:
        for source_path in source_paths:
            source_info = sf.info(str(source_path))
            prepared_path = source_path
            if (
                int(source_info.samplerate) != target_rate
                or int(source_info.channels) != target_channels
            ):
                cache_key = (source_path, target_rate, target_channels)
                prepared_path = transcoded_cache.get(cache_key)
                if prepared_path is None:
                    prepared_path = (
                        temp_dir
                        / f"{safe_name(source_path.stem)}-{len(transcoded_cache)}.wav"
                    )
                    transcode_for_concat(
                        audio_path=source_path,
                        output_path=prepared_path,
                        sample_rate=target_rate,
                        channels=target_channels,
                    )
                    transcoded_cache[cache_key] = prepared_path

            audio, sample_rate = sf.read(
                str(prepared_path),
                dtype="float32",
                always_2d=True,
            )
            if int(sample_rate) != target_rate or int(audio.shape[1]) != target_channels:
                raise RuntimeError(
                    f"Unexpected audio format while concatenating: {prepared_path}"
                )
            sink.write(audio)
            total_frames += int(audio.shape[0])

    duration_sec = round(total_frames / float(target_rate), 4)
    return duration_sec, target_rate, target_channels


def write_ffmetadata(
    *,
    output_path: Path,
    tags: dict[str, str],
    chapters: list[dict[str, Any]],
) -> None:
    """Write an ffmetadata file with tags and chapter marker entries."""

    lines = [";FFMETADATA1"]
    for key, value in tags.items():
        if not value:
            continue
        lines.append(f"{key}={ffmetadata_escape(value)}")

    for chapter in chapters:
        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={int(chapter['startMs'])}",
                f"END={int(chapter['endMs'])}",
                f"title={ffmetadata_escape(str(chapter['title']))}",
            ]
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def encode_m4b(
    *,
    input_wav: Path,
    metadata_path: Path,
    output_path: Path,
    bitrate: str,
) -> None:
    """Encode a WAV plus ffmetadata into an AAC M4B container."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_wav),
        "-f",
        "ffmetadata",
        "-i",
        str(metadata_path),
        "-map",
        "0:a:0",
        "-map_metadata",
        "1",
        "-map_chapters",
        "1",
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        str(bitrate),
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


def ensure_clean_output(output_dir: Path, force: bool) -> None:
    """Prepare the output directory and guard against accidental reuse."""

    if output_dir.exists() and not force:
        existing = [
            output_dir / "chapters",
            output_dir / "intermediate",
            output_dir / "metadata",
            output_dir / "assembly_report.json",
        ]
        if any(path.exists() for path in existing):
            raise FileExistsError(
                "Assembly outputs already exist. Re-run with --force to overwrite: "
                f"{output_dir}"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    for child_name in ["chapters", "intermediate", "metadata"]:
        child_path = output_dir / child_name
        if force and child_path.exists():
            shutil.rmtree(child_path)
        child_path.mkdir(parents=True, exist_ok=True)


def build_chapter_tags(
    *,
    book_title: str,
    author: str,
    narrator: str,
    genre: str,
    chapter_title: str,
    chapter_number: int,
    chapter_count: int,
) -> dict[str, str]:
    """Return embedded metadata tags for one chapter M4B."""

    return {
        "title": chapter_title,
        "album": book_title,
        "artist": narrator,
        "album_artist": narrator,
        "composer": author,
        "genre": genre,
        "track": f"{chapter_number + 1}/{chapter_count}",
        "comment": f"{chapter_title} from {book_title}",
    }


def build_book_tags(
    *,
    book_title: str,
    author: str,
    narrator: str,
    genre: str,
) -> dict[str, str]:
    """Return embedded metadata tags for the full-book M4B."""

    return {
        "title": book_title,
        "album": book_title,
        "artist": narrator,
        "album_artist": narrator,
        "composer": author,
        "genre": genre,
        "comment": f"Complete audiobook for {book_title}",
    }


def build_full_book_chapters(chapter_results: list[ChapterResult]) -> list[dict[str, Any]]:
    """Create chapter marker records for the combined book M4B."""

    chapters: list[dict[str, Any]] = []
    current_start_ms = 0
    for result in chapter_results:
        duration_ms = int(round(result.duration_sec * 1000.0))
        end_ms = current_start_ms + duration_ms
        chapters.append(
            {
                "startMs": current_start_ms,
                "endMs": end_ms,
                "title": result.display_title,
            }
        )
        current_start_ms = end_ms
    return chapters


def assemble_chapters(
    *,
    chapters: list[Path],
    output_dir: Path,
    book_title: str,
    author: str,
    narrator: str,
    genre: str,
    bitrate: str,
    dry_run: bool,
) -> list[ChapterResult]:
    """Render one WAV and one M4B for each chapter."""

    intermediate_dir = output_dir / "intermediate" / "chapter_wav"
    metadata_dir = output_dir / "metadata" / "chapters"
    chapter_m4b_dir = output_dir / "chapters"
    temp_dir = output_dir / "intermediate" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    chapter_results: list[ChapterResult] = []
    chapter_count = len(chapters)

    for chapter_dir in chapters:
        plan = build_chapter_plan(chapter_dir)
        chapter_wav_path = intermediate_dir / f"{plan.chapter_name}.wav"
        chapter_m4b_path = chapter_m4b_dir / f"{plan.chapter_name}.m4b"
        chapter_metadata_path = metadata_dir / f"{plan.chapter_name}.ffmetadata"

        tags = build_chapter_tags(
            book_title=book_title,
            author=author,
            narrator=narrator,
            genre=genre,
            chapter_title=plan.display_title,
            chapter_number=plan.chapter_number,
            chapter_count=chapter_count,
        )

        duration_sec = 0.0
        if not dry_run:
            duration_sec, _, _ = render_concat_wav(
                source_paths=plan.ordered_segments,
                output_path=chapter_wav_path,
                temp_dir=temp_dir,
            )
            write_ffmetadata(
                output_path=chapter_metadata_path,
                tags=tags,
                chapters=[
                    {
                        "startMs": 0,
                        "endMs": int(round(duration_sec * 1000.0)),
                        "title": plan.display_title,
                    }
                ],
            )
            encode_m4b(
                input_wav=chapter_wav_path,
                metadata_path=chapter_metadata_path,
                output_path=chapter_m4b_path,
                bitrate=bitrate,
            )

        chapter_results.append(
            ChapterResult(
                chapter_name=plan.chapter_name,
                display_title=plan.display_title,
                chapter_number=plan.chapter_number,
                segment_count=len(plan.ordered_segments),
                duplicate_resolution_count=len(plan.duplicate_resolutions),
                wav_path=str(chapter_wav_path),
                m4b_path=str(chapter_m4b_path),
                duration_sec=duration_sec,
            )
        )

    return chapter_results


def assemble_full_book(
    *,
    chapter_results: list[ChapterResult],
    output_dir: Path,
    book_title: str,
    author: str,
    narrator: str,
    genre: str,
    bitrate: str,
    dry_run: bool,
) -> tuple[Path, Path, float]:
    """Render the full-book WAV and M4B from the chapter WAV outputs."""

    full_wav_path = output_dir / "intermediate" / f"{safe_name(book_title)}.wav"
    full_metadata_path = output_dir / "metadata" / "book.ffmetadata"
    full_m4b_path = output_dir / f"{safe_name(book_title)}.m4b"

    duration_sec = 0.0
    if not dry_run:
        duration_sec, _, _ = render_concat_wav(
            source_paths=[Path(result.wav_path) for result in chapter_results],
            output_path=full_wav_path,
            temp_dir=output_dir / "intermediate" / "temp",
        )
        write_ffmetadata(
            output_path=full_metadata_path,
            tags=build_book_tags(
                book_title=book_title,
                author=author,
                narrator=narrator,
                genre=genre,
            ),
            chapters=build_full_book_chapters(chapter_results),
        )
        encode_m4b(
            input_wav=full_wav_path,
            metadata_path=full_metadata_path,
            output_path=full_m4b_path,
            bitrate=bitrate,
        )

    return full_wav_path, full_m4b_path, duration_sec


def write_report(
    *,
    output_dir: Path,
    book_dir: Path,
    book_title: str,
    author: str,
    narrator: str,
    chapter_results: list[ChapterResult],
    full_book_wav_path: Path,
    full_book_m4b_path: Path,
    full_book_duration_sec: float,
) -> None:
    """Write a machine-readable report describing the assembly outputs."""

    payload = {
        "formatVersion": "book-m4b-assembly/v1",
        "createdAt": utc_now_iso(),
        "bookDir": str(book_dir.resolve()),
        "outputDir": str(output_dir.resolve()),
        "bookTitle": book_title,
        "author": author,
        "narrator": narrator,
        "summary": {
            "chapterCount": len(chapter_results),
            "totalDuplicateResolutions": sum(
                result.duplicate_resolution_count for result in chapter_results
            ),
            "fullBookDurationSec": round(full_book_duration_sec, 4),
            "fullBookWavPath": str(full_book_wav_path),
            "fullBookM4bPath": str(full_book_m4b_path),
        },
        "chapters": [asdict(result) for result in chapter_results],
    }
    report_path = output_dir / "assembly_report.json"
    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Assemble per-chapter and full-book M4B outputs for a book folder."""

    args = parse_args()
    book_dir = args.book_dir.resolve()
    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")

    chapters = discover_chapters(book_dir)
    if not chapters:
        raise RuntimeError(f"No chapter directories found in {book_dir}")

    book_title = str(args.book_title).strip() if args.book_title else humanize_book_title(book_dir)
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else (book_dir / "_assembled_m4b").resolve()
    )

    ensure_clean_output(output_dir, force=bool(args.force))

    chapter_results = assemble_chapters(
        chapters=chapters,
        output_dir=output_dir,
        book_title=book_title,
        author=str(args.author),
        narrator=str(args.narrator),
        genre=str(args.genre),
        bitrate=str(args.bitrate),
        dry_run=bool(args.dry_run),
    )
    full_book_wav_path, full_book_m4b_path, full_book_duration_sec = assemble_full_book(
        chapter_results=chapter_results,
        output_dir=output_dir,
        book_title=book_title,
        author=str(args.author),
        narrator=str(args.narrator),
        genre=str(args.genre),
        bitrate=str(args.bitrate),
        dry_run=bool(args.dry_run),
    )

    write_report(
        output_dir=output_dir,
        book_dir=book_dir,
        book_title=book_title,
        author=str(args.author),
        narrator=str(args.narrator),
        chapter_results=chapter_results,
        full_book_wav_path=full_book_wav_path,
        full_book_m4b_path=full_book_m4b_path,
        full_book_duration_sec=full_book_duration_sec,
    )

    print(f"Chapters assembled: {len(chapter_results)}")
    print(f"Output directory: {output_dir}")
    print(f"Full book M4B: {full_book_m4b_path}")


if __name__ == "__main__":
    main()
