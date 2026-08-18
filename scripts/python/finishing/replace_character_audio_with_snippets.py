"""Replace generated character audio clips with fixed snippet WAVs.

This utility walks chapter folders under a book directory, finds dialogue lines
for one character, and swaps any matching generated WAVs in `audio_lines/` and
`audio_test_narrator/splits/` while preserving the destination filenames.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioFormat:
    """Describe the output WAV shape to preserve during replacement."""

    sample_rate: int
    channels: int


@dataclass(frozen=True)
class ReplacementPlan:
    """Describe one target file and the snippet chosen for it."""

    chapter_name: str
    line_id: int
    target_path: Path
    snippet_path: Path
    snippet_label: str


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the replacement workflow."""

    parser = argparse.ArgumentParser(
        description=(
            "Replace generated character WAVs with fixed audio snippets while "
            "preserving the original destination filenames."
        )
    )
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument(
        "--character-name",
        default="Sylphie",
        help="Display name used by the character audio folders and filenames.",
    )
    parser.add_argument(
        "--character-id",
        default=None,
        help="Dialogue characterId to match. Defaults to the lowercase character name.",
    )
    parser.add_argument(
        "--first-source",
        type=Path,
        default=None,
        help="Snippet used for the first matching line in each chapter.",
    )
    parser.add_argument(
        "--default-source",
        type=Path,
        default=None,
        help="Snippet used when the line is not close to the previous matching line.",
    )
    parser.add_argument(
        "--close-source",
        type=Path,
        default=None,
        help="Snippet used when the line id gap is smaller than --close-gap.",
    )
    parser.add_argument(
        "--close-gap",
        type=int,
        default=5,
        help="Use the close snippet when current_line_id - previous_line_id is below this value.",
    )
    parser.add_argument(
        "--skip-audio-lines",
        action="store_true",
        help="Do not replace files under audio_lines/<Character>/.",
    )
    parser.add_argument(
        "--skip-audio-test-splits",
        action="store_true",
        help="Do not replace files under audio_test_narrator/splits/<Character>/.",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        type=Path,
        default=Path("ffmpeg"),
        help="ffmpeg executable used for snippet conversion.",
    )
    parser.add_argument(
        "--ffprobe-bin",
        type=Path,
        default=Path("ffprobe"),
        help="ffprobe executable used to inspect destination WAV format.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the replacement plan without modifying files.",
    )
    return parser.parse_args()


def resolve_default_sources(book_dir: Path) -> dict[str, Path]:
    """Resolve the default snippet files relative to the Primal-Hunter tree."""

    snippets_dir = book_dir.parent / "Audio Snippets"
    return {
        "first": snippets_dir / "she-screeched.mp3",
        "default": snippets_dir / "screech.mp3",
        "close": snippets_dir / "another-screech.mp3",
    }


def load_dialogue_lines(dialogue_path: Path) -> list[dict]:
    """Load the top-level dialogue lines array from a chapter dialogue file."""

    payload = json.loads(dialogue_path.read_text(encoding="utf-8"))
    lines = payload.get("lines")
    if not isinstance(lines, list):
        raise ValueError(f"Dialogue file does not contain a top-level lines array: {dialogue_path}")
    return [line for line in lines if isinstance(line, dict)]


def iter_chapter_dirs(book_dir: Path) -> list[Path]:
    """Return chapter directories that contain a dialogue.json file."""

    chapter_dirs: list[Path] = []
    for child in sorted(book_dir.iterdir()):
        if child.is_dir() and (child / "dialogue.json").exists():
            chapter_dirs.append(child)
    return chapter_dirs


def collect_character_line_ids(dialogue_path: Path, character_id: str) -> list[int]:
    """Collect sorted unique line ids for the requested dialogue speaker."""

    matches: set[int] = set()
    for line in load_dialogue_lines(dialogue_path):
        if str(line.get("characterId", "")).casefold() != character_id.casefold():
            continue
        line_id = line.get("id")
        if isinstance(line_id, int):
            matches.add(line_id)
    return sorted(matches)


def collect_target_files(
    chapter_dir: Path,
    character_name: str,
    line_id: int,
    include_audio_lines: bool,
    include_audio_test_splits: bool,
) -> list[Path]:
    """Find generated WAVs for one character line while preserving filenames."""

    targets: list[Path] = []
    pattern = f"{line_id}-*.wav"
    search_roots: list[Path] = []

    if include_audio_lines:
        search_roots.append(chapter_dir / "audio_lines" / character_name)
    if include_audio_test_splits:
        search_roots.append(chapter_dir / "audio_test_narrator" / "splits" / character_name)

    for root in search_roots:
        if not root.exists():
            continue
        targets.extend(sorted(path for path in root.glob(pattern) if path.is_file()))
    return targets


def choose_snippet_label(index: int, line_id: int, previous_line_id: int | None, close_gap: int) -> str:
    """Apply the chapter-local gap rule to select the snippet label."""

    if index == 0:
        return "first"
    if previous_line_id is not None and line_id - previous_line_id < close_gap:
        return "close"
    return "default"


def probe_audio_format(target_path: Path, ffprobe_bin: Path) -> AudioFormat:
    """Inspect the destination WAV so the replacement preserves its basic format."""

    result = subprocess.run(
        [
            str(ffprobe_bin),
            "-v",
            "error",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "json",
            str(target_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise ValueError(f"ffprobe returned no streams for {target_path}")
    stream = streams[0]
    return AudioFormat(sample_rate=int(stream["sample_rate"]), channels=int(stream["channels"]))


def replace_audio(target_path: Path, snippet_path: Path, audio_format: AudioFormat, ffmpeg_bin: Path) -> None:
    """Overwrite one target WAV using the chosen snippet source."""

    subprocess.run(
        [
            str(ffmpeg_bin),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(snippet_path),
            "-ar",
            str(audio_format.sample_rate),
            "-ac",
            str(audio_format.channels),
            "-c:a",
            "pcm_s16le",
            str(target_path),
        ],
        check=True,
    )


def build_replacement_plan(args: argparse.Namespace) -> tuple[list[ReplacementPlan], list[str]]:
    """Resolve all target files that should be replaced."""

    include_audio_lines = not args.skip_audio_lines
    include_audio_test_splits = not args.skip_audio_test_splits
    if not include_audio_lines and not include_audio_test_splits:
        raise ValueError("At least one target location must be enabled.")

    default_sources = resolve_default_sources(args.book_dir)
    snippet_sources = {
        "first": args.first_source or default_sources["first"],
        "default": args.default_source or default_sources["default"],
        "close": args.close_source or default_sources["close"],
    }
    for label, snippet_path in snippet_sources.items():
        if not snippet_path.exists():
            raise FileNotFoundError(f"Missing {label} snippet source: {snippet_path}")

    character_id = args.character_id or args.character_name.lower()
    plan: list[ReplacementPlan] = []
    missing: list[str] = []

    for chapter_dir in iter_chapter_dirs(args.book_dir):
        line_ids = collect_character_line_ids(chapter_dir / "dialogue.json", character_id)
        if not line_ids:
            continue

        previous_line_id: int | None = None
        for index, line_id in enumerate(line_ids):
            snippet_label = choose_snippet_label(index, line_id, previous_line_id, args.close_gap)
            target_files = collect_target_files(
                chapter_dir,
                args.character_name,
                line_id,
                include_audio_lines,
                include_audio_test_splits,
            )
            if not target_files:
                missing.append(f"{chapter_dir.name}: line {line_id} has dialogue but no target WAVs")
            for target_path in target_files:
                plan.append(
                    ReplacementPlan(
                        chapter_name=chapter_dir.name,
                        line_id=line_id,
                        target_path=target_path,
                        snippet_path=snippet_sources[snippet_label],
                        snippet_label=snippet_label,
                    )
                )
            previous_line_id = line_id

    return plan, missing


def print_plan(plan: list[ReplacementPlan], missing: list[str], book_dir: Path) -> None:
    """Print a readable summary of the planned replacements."""

    print(f"planned_replacements={len(plan)}")
    for item in plan:
        relative_path = item.target_path.relative_to(book_dir)
        print(
            f"{item.chapter_name}\tline={item.line_id}\t{relative_path}\t{item.snippet_label}\t{item.snippet_path.name}"
        )
    print(f"missing_targets={len(missing)}")
    for entry in missing:
        print(entry)


def main() -> int:
    """Run the replacement workflow."""

    args = parse_args()
    args.book_dir = args.book_dir.resolve()

    plan, missing = build_replacement_plan(args)
    print_plan(plan, missing, args.book_dir)

    if args.dry_run:
        return 0

    for item in plan:
        audio_format = probe_audio_format(item.target_path, args.ffprobe_bin)
        replace_audio(item.target_path, item.snippet_path, audio_format, args.ffmpeg_bin)

    print("replacement_complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI entrypoint guard
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc