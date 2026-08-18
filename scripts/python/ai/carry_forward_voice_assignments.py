"""Carry matching character voice assignments forward into a later book."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy the most recent voice assignment for matching canonical character "
            "IDs from earlier books. Target assignments always win. Runs as a dry "
            "run unless --apply is supplied."
        )
    )
    parser.add_argument("series_root", type=Path, help="Series directory containing Book-* folders.")
    parser.add_argument("target_book", type=int, help="Target Book number, for example 18.")
    parser.add_argument("--apply", action="store_true", help="Back up and write the target files.")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Backup destination. Defaults beneath the target book's _backups directory.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            payload = json.load(source)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {path} at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def identity_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def book_number(path: Path) -> int | None:
    match = re.fullmatch(r"Book-(\d+)", path.name, re.IGNORECASE)
    return int(match.group(1)) if match else None


def previous_book_dirs(series_root: Path, target_book: int) -> list[tuple[int, Path]]:
    books: list[tuple[int, Path]] = []
    for path in series_root.iterdir():
        if not path.is_dir():
            continue
        number = book_number(path)
        if number is not None and number < target_book:
            books.append((number, path))
    return sorted(books)


def require_list(payload: dict[str, Any], field: str, path: Path) -> list[Any]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"Expected a {field!r} array in {path}")
    return value


def json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def atomic_write_json(path: Path, payload: Any) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(json_text(payload))
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def default_backup_dir(target_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return target_root / "_backups" / "voice-carry-forward" / timestamp


def add_character_to_voice_metadata(voice: dict[str, Any], character_id: str) -> None:
    metadata = voice.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        voice["metadata"] = metadata
    used_by = metadata.get("usedByCharacters")
    if not isinstance(used_by, list):
        used_by = []
    keys = {identity_key(value) for value in used_by}
    if identity_key(character_id) not in keys:
        used_by.append(character_id)
    metadata["usedByCharacters"] = used_by


def update_character_voice_fields(
    target: dict[str, Any],
    source: dict[str, Any] | None,
    voice: dict[str, Any],
) -> None:
    target["voice"] = source.get("voice") if source else None
    target["provider"] = voice.get("provider") or (source.get("provider") if source else None)
    target["voiceId"] = voice.get("providerVoiceId") or (source.get("voiceId") if source else None)

    source_meta = source.get("voiceMeta") if source else None
    if isinstance(source_meta, dict):
        target["voiceMeta"] = copy.deepcopy(source_meta)
    else:
        target["voiceMeta"] = {
            "name": voice.get("displayName"),
            "previewUrl": voice.get("previewUrl"),
            "provider": voice.get("provider"),
        }


def main() -> None:
    args = parse_args()
    series_root = args.series_root.expanduser().resolve()
    target_root = series_root / f"Book-{args.target_book}"
    target_characters_path = target_root / "characters.json"
    target_voices_path = target_root / "voices.json"

    try:
        if not target_root.is_dir():
            raise ValueError(f"Target book does not exist: {target_root}")
        target_characters = read_json(target_characters_path)
        target_voices = read_json(target_voices_path)
        character_rows = require_list(target_characters, "characters", target_characters_path)
        voice_rows = require_list(target_voices, "voices", target_voices_path)
        assignment_rows = require_list(target_voices, "assignments", target_voices_path)

        target_by_id = {
            identity_key(row.get("id")): row
            for row in character_rows
            if isinstance(row, dict) and clean_text(row.get("id"))
        }
        target_assignment_ids = {
            identity_key(row.get("characterId"))
            for row in assignment_rows
            if isinstance(row, dict) and clean_text(row.get("characterId"))
        }
        target_voices_by_id = {
            identity_key(row.get("id")): row
            for row in voice_rows
            if isinstance(row, dict) and clean_text(row.get("id"))
        }

        # Later books overwrite earlier books, yielding the most recent source.
        sources: dict[str, tuple[int, dict[str, Any], dict[str, Any], dict[str, Any] | None]] = {}
        scanned_books: list[int] = []
        for number, book_root in previous_book_dirs(series_root, args.target_book):
            voices_path = book_root / "voices.json"
            if not voices_path.is_file():
                continue
            voices_payload = read_json(voices_path)
            source_voices = require_list(voices_payload, "voices", voices_path)
            source_assignments = require_list(voices_payload, "assignments", voices_path)
            source_voice_by_id = {
                identity_key(row.get("id")): row
                for row in source_voices
                if isinstance(row, dict) and clean_text(row.get("id"))
            }

            source_character_by_id: dict[str, dict[str, Any]] = {}
            characters_path = book_root / "characters.json"
            if characters_path.is_file():
                characters_payload = read_json(characters_path)
                source_character_by_id = {
                    identity_key(row.get("id")): row
                    for row in require_list(characters_payload, "characters", characters_path)
                    if isinstance(row, dict) and clean_text(row.get("id"))
                }

            scanned_books.append(number)
            for assignment in source_assignments:
                if not isinstance(assignment, dict):
                    continue
                character_key = identity_key(assignment.get("characterId"))
                if character_key not in target_by_id:
                    continue
                voice_id = clean_text(assignment.get("voiceId"))
                voice = source_voice_by_id.get(identity_key(voice_id))
                if not voice_id or not voice:
                    raise ValueError(
                        f"Book-{number} assignment for {assignment.get('characterId')!r} "
                        f"references missing voice {voice_id!r}"
                    )
                sources[character_key] = (
                    number,
                    copy.deepcopy(assignment),
                    copy.deepcopy(voice),
                    copy.deepcopy(source_character_by_id.get(character_key)),
                )

        copied: list[tuple[str, int, str]] = []
        added_voice_ids: list[str] = []
        for character_key, target_character in target_by_id.items():
            if character_key in target_assignment_ids:
                continue
            source = sources.get(character_key)
            if not source:
                continue
            source_book, assignment, source_voice, source_character = source
            character_id = str(target_character["id"])
            assignment["characterId"] = character_id
            voice_id = str(assignment["voiceId"])

            target_voice = target_voices_by_id.get(identity_key(voice_id))
            if target_voice is None:
                target_voice = source_voice
                voice_rows.append(target_voice)
                target_voices_by_id[identity_key(voice_id)] = target_voice
                added_voice_ids.append(voice_id)

            add_character_to_voice_metadata(target_voice, character_id)
            assignment_rows.append(assignment)
            target_assignment_ids.add(character_key)
            update_character_voice_fields(target_character, source_character, target_voice)
            copied.append((character_id, source_book, voice_id))

        known_voice_ids = set(target_voices_by_id)
        missing_references = [
            str(row.get("voiceId"))
            for row in assignment_rows
            if isinstance(row, dict) and identity_key(row.get("voiceId")) not in known_voice_ids
        ]
        if missing_references:
            raise ValueError(
                "Target assignments reference missing voices after merge: "
                + ", ".join(sorted(set(missing_references)))
            )

        print(f"Previous voice catalogs scanned: {', '.join(f'Book-{n}' for n in scanned_books)}")
        print(f"Assignments copied: {len(copied)}")
        print(f"Voice definitions added: {len(added_voice_ids)}")
        for character_id, source_book, voice_id in copied:
            print(f"  {character_id}: {voice_id} (Book-{source_book})")

        if not args.apply:
            print("Dry run complete; no files were changed. Re-run with --apply to write them.")
            return

        backup_dir = (
            args.backup_dir.expanduser().resolve()
            if args.backup_dir
            else default_backup_dir(target_root)
        )
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target_characters_path, backup_dir / "characters.json")
        shutil.copy2(target_voices_path, backup_dir / "voices.json")
        atomic_write_json(target_characters_path, target_characters)
        atomic_write_json(target_voices_path, target_voices)
        print(f"Backups: {backup_dir}")
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
