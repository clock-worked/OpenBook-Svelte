#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[2]
BOOK_DIR_DEFAULT = Path(
    "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14"
)


def normalize_character_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def candidate_character_keys(value: str) -> list[str]:
    base = value.strip().lower()
    keys = {
        normalize_character_key(base),
        normalize_character_key(base.replace("the ", "")),
        normalize_character_key(base.replace("the-", "")),
        normalize_character_key(base.replace("the_", "")),
    }
    return [item for item in keys if item]


def chapter_lookup(book_dir: Path) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for chapter_dir in sorted([item for item in book_dir.iterdir() if item.is_dir()]):
        match = re.match(r"^(\d+)\s*-\s*", chapter_dir.name)
        if match:
            result[int(match.group(1))] = chapter_dir.resolve()
    return result


def find_line(dialogue_path: Path, line_id: int) -> dict[str, Any] | None:
    payload = json.loads(dialogue_path.read_text(encoding="utf-8"))
    lines = payload.get("lines", [])
    if not isinstance(lines, list):
        return None

    for line in lines:
        if isinstance(line, dict) and line.get("id") == line_id:
            return line
    return None


def find_target_path(chapter_dir: Path, line_id: int, character_id: str) -> Path:
    audio_lines = (chapter_dir / "audio_lines").resolve()
    if not audio_lines.exists():
        raise FileNotFoundError(f"audio_lines folder not found: {audio_lines}")

    existing = sorted(audio_lines.glob(f"*/{line_id}-*.wav"))
    if existing:
        return existing[0].resolve()

    target_keys = set(candidate_character_keys(character_id))
    matched_dir: Path | None = None
    for folder in sorted([item for item in audio_lines.iterdir() if item.is_dir()]):
        folder_key = normalize_character_key(folder.name)
        if folder_key in target_keys:
            matched_dir = folder.resolve()
            break

    if matched_dir is None:
        fallback_name = character_id.replace("-", " ").title().strip() or "Unknown"
        matched_dir = (audio_lines / fallback_name).resolve()
        matched_dir.mkdir(parents=True, exist_ok=True)

    return (matched_dir / f"{line_id}-{matched_dir.name}.wav").resolve()


def run_generation(
    *,
    python_exe: Path,
    repo_root: Path,
    chapter_dir: Path,
    review_dir: Path,
    line_id: int,
    character: str,
    final_text: str,
    device: str,
) -> Path:
    review_dir.mkdir(parents=True, exist_ok=True)
    segments_path = review_dir / "segments.json"
    segments = [
        {
            "lineId": line_id,
            "character": character,
            "text": final_text,
            "sourceLineIds": [line_id],
        }
    ]
    segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

    command = [
        str(python_exe),
        str((repo_root / "py_services" / "asr_experiment_runner.py").resolve()),
        "multi-heuristic-split",
        "--output-dir",
        str(review_dir),
        "--segments-json",
        str(segments_path),
        "--main-character-name",
        character,
        "--chapter-dir",
        str(chapter_dir),
        "--pause-snap",
        "--vibevoice-device",
        device,
    ]

    completed = subprocess.run(command, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"Generation failed (exit {completed.returncode})")

    split_wavs = sorted((review_dir / "splits").glob("*.wav"))
    if not split_wavs:
        raise FileNotFoundError(f"No split wav generated under {review_dir / 'splits'}")
    return split_wavs[0].resolve()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate one dialogue line with optional tail text, auto-detect character from dialogue.json, "
            "and write the result into the chapter audio_lines folder."
        )
    )
    parser.add_argument("--book-dir", type=Path, default=BOOK_DIR_DEFAULT)
    parser.add_argument("--chapter-number", type=int, required=True)
    parser.add_argument("--line-id", type=int, required=True)
    parser.add_argument(
        "--text-override",
        default=None,
        help="Optional full text override for spoken generation (applied before --tail).",
    )
    parser.add_argument("--tail", default="")
    parser.add_argument("--character-override", default=None)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    parser.add_argument("--python-exe", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--write-mode",
        choices=["replace", "regen"],
        default="replace",
        help="replace: overwrite target wav with backup; regen: write <target>.REGEN",
    )
    args = parser.parse_args()

    book_dir = args.book_dir.resolve()
    repo_root = args.repo_root.resolve()
    python_exe = args.python_exe.resolve()

    chapters = chapter_lookup(book_dir)
    chapter_dir = chapters.get(args.chapter_number)
    if chapter_dir is None:
        raise SystemExit(f"Chapter not found for number {args.chapter_number} under {book_dir}")

    dialogue_path = (chapter_dir / "dialogue.json").resolve()
    if not dialogue_path.exists():
        raise SystemExit(f"dialogue.json not found: {dialogue_path}")

    line = find_line(dialogue_path, args.line_id)
    if line is None:
        raise SystemExit(f"Line id {args.line_id} not found in {dialogue_path}")

    base_character = str(line.get("characterId", "")).strip()
    original_text = str(line.get("text", "")).strip()
    base_text = str(args.text_override).strip() if isinstance(args.text_override, str) and args.text_override.strip() else original_text
    if not base_character or not base_text:
        raise SystemExit(f"Line {args.line_id} missing character/text in {dialogue_path}")

    character = str(args.character_override).strip() if args.character_override else base_character
    tail = str(args.tail).strip()
    final_text = f"{base_text} {tail}".strip() if tail else base_text

    slug_character = re.sub(r"[^a-zA-Z0-9]+", "-", character).strip("-").lower() or "character"
    note_slug = "tail" if tail else "plain"
    review_dir = chapter_dir / "review" / f"regen_{slug_character}_{args.line_id}_single_{note_slug}"

    print(f"Generating: chapter={chapter_dir.name} line={args.line_id} character={character}")
    print(f"Text: {final_text}")

    generated_split = run_generation(
        python_exe=python_exe,
        repo_root=repo_root,
        chapter_dir=chapter_dir,
        review_dir=review_dir,
        line_id=args.line_id,
        character=character,
        final_text=final_text,
        device=args.device,
    )

    target_wav = find_target_path(chapter_dir, args.line_id, character)
    target_wav.parent.mkdir(parents=True, exist_ok=True)

    if args.write_mode == "regen":
        regen_path = Path(str(target_wav) + ".REGEN")
        shutil.copy2(generated_split, regen_path)
        print(f"Wrote REGEN: {regen_path}")
    else:
        backup_path = None
        if target_wav.exists():
            timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            backup_path = target_wav.with_name(f"{target_wav.name}.bak-{timestamp}")
            shutil.copy2(target_wav, backup_path)
        shutil.copy2(generated_split, target_wav)
        if backup_path:
            print(f"Backup: {backup_path}")
        print(f"Replaced: {target_wav}")

    print(f"Review folder: {review_dir}")


if __name__ == "__main__":
    main()
