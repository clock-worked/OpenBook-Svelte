"""Convert per-chapter character rosters to canonical book character ID references."""

from __future__ import annotations

import argparse
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
            "Convert <chapter>/<chapter>.characters.json records to {id: ...} "
            "references resolved from the book-level characters.json. The default "
            "is a validation-only dry run."
        )
    )
    parser.add_argument("book_root", type=Path, help="Book directory containing characters.json.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up and write all validated changes.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help=(
            "Backup destination used with --apply. Defaults to a timestamped "
            "directory beneath <book_root>/_backups/chapter-character-ids."
        ),
    )
    parser.add_argument(
        "--drop-unresolved",
        action="store_true",
        help=(
            "Omit legacy roster entries that have no corresponding ID in the "
            "central characters.json. Without this flag, any such entry aborts "
            "the entire migration."
        ),
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            return json.load(source)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {path} at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def lookup_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def id_candidates(value: str) -> list[str]:
    """Return conservative legacy-name variants that may already be central IDs."""

    slug = re.sub(r"[^a-z0-9\s-]", "", value.casefold())
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    if not slug:
        return []
    candidates = [slug, f"the-{slug}"]
    if slug.endswith("s") and len(slug) > 1:
        singular = slug[:-1]
        candidates.extend([singular, f"the-{singular}"])
    return candidates


def resolve_surface(value: str, ids: dict[str, str], names: dict[str, str]) -> str | None:
    resolved = names.get(lookup_key(value)) or ids.get(lookup_key(value))
    if resolved:
        return resolved
    for candidate in id_candidates(value):
        resolved = ids.get(lookup_key(candidate))
        if resolved:
            return resolved
    return None


def discover_chapter_files(book_root: Path) -> list[Path]:
    return sorted(
        path
        for path in book_root.glob("*/*.characters.json")
        if path.is_file() and path.name == f"{path.parent.name}.characters.json"
    )


def build_character_lookups(
    characters_path: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    payload = read_json(characters_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("characters"), list):
        raise ValueError(f"Expected a characters array in {characters_path}")

    ids: dict[str, str] = {}
    names: dict[str, str] = {}
    ambiguous_names: dict[str, set[str]] = {}

    for index, character in enumerate(payload["characters"]):
        if not isinstance(character, dict):
            raise ValueError(f"Character {index} is not an object in {characters_path}")
        character_id = clean_text(character.get("id"))
        name = clean_text(character.get("name"))
        if not character_id or not name:
            raise ValueError(
                f"Character {index} must have non-empty id and name fields in {characters_path}"
            )

        id_key = lookup_key(character_id)
        existing_id = ids.get(id_key)
        if existing_id and existing_id != character_id:
            raise ValueError(f"Duplicate character ID (case-insensitive): {character_id}")
        ids[id_key] = character_id

        surfaces = [name, *(character.get("aliases") or [])]
        for surface in surfaces:
            surface_text = clean_text(surface)
            if not surface_text:
                continue
            key = lookup_key(surface_text)
            previous = names.get(key)
            if previous and previous != character_id:
                ambiguous_names.setdefault(key, {previous}).add(character_id)
            else:
                names[key] = character_id

    if ambiguous_names:
        details = "; ".join(
            f"{name!r} -> {', '.join(sorted(character_ids))}"
            for name, character_ids in sorted(ambiguous_names.items())
        )
        raise ValueError(f"Ambiguous central character names/aliases: {details}")

    return ids, names


def resolve_reference(
    entry: Any,
    ids: dict[str, str],
    names: dict[str, str],
) -> str | None:
    if isinstance(entry, str):
        value = clean_text(entry)
        if not value:
            return None
        return resolve_surface(value, ids, names)

    if not isinstance(entry, dict):
        return None

    character_id = clean_text(entry.get("id"))
    if character_id:
        resolved_id = ids.get(lookup_key(character_id))
        if resolved_id:
            return resolved_id

    name = clean_text(entry.get("name"))
    if not name:
        return None
    # Some legacy files stored an ID in the display-name field.
    return resolve_surface(name, ids, names)


def convert_file(
    path: Path,
    ids: dict[str, str],
    names: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("characters"), list):
        raise ValueError(f"Expected a characters array in {path}")

    references: list[dict[str, str]] = []
    seen: set[str] = set()
    unresolved: list[str] = []

    for index, entry in enumerate(payload["characters"]):
        character_id = resolve_reference(entry, ids, names)
        if not character_id:
            unresolved.append(f"entry {index}: {entry!r}")
            continue
        key = lookup_key(character_id)
        if key in seen:
            continue
        references.append({"id": character_id})
        seen.add(key)

    converted = dict(payload)
    converted["formatVersion"] = payload.get("formatVersion") or "2.0"
    converted["characters"] = references
    return converted, unresolved


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


def default_backup_dir(book_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return book_root / "_backups" / "chapter-character-ids" / timestamp


def main() -> None:
    args = parse_args()
    book_root = args.book_root.expanduser().resolve()
    characters_path = book_root / "characters.json"

    if not book_root.is_dir():
        raise SystemExit(f"Book directory does not exist: {book_root}")
    if not characters_path.is_file():
        raise SystemExit(f"Central character file does not exist: {characters_path}")

    try:
        ids, names = build_character_lookups(characters_path)
        chapter_files = discover_chapter_files(book_root)
        if not chapter_files:
            raise ValueError(f"No chapter character files found beneath {book_root}")

        changes: list[tuple[Path, dict[str, Any]]] = []
        errors: list[str] = []
        reference_count = 0
        for path in chapter_files:
            converted, unresolved = convert_file(path, ids, names)
            if unresolved:
                errors.extend(f"{path.relative_to(book_root)}: {item}" for item in unresolved)
            reference_count += len(converted["characters"])
            if json_text(read_json(path)) != json_text(converted):
                changes.append((path, converted))

        print(f"Chapter files scanned: {len(chapter_files)}")
        print(f"Canonical references resolved: {reference_count}")
        print(f"Files requiring conversion: {len(changes)}")

        if errors:
            print("Unresolved entries:")
            for error in errors:
                print(f"  - {error}")
            if not args.drop_unresolved:
                raise ValueError("Validation failed; no files were changed.")
            print(f"Unresolved legacy entries to omit: {len(errors)}")

        if not args.apply:
            print("Dry run complete; no files were changed. Re-run with --apply to write them.")
            return

        backup_dir = (
            args.backup_dir.expanduser().resolve()
            if args.backup_dir
            else default_backup_dir(book_root)
        )
        for path, converted in changes:
            relative = path.relative_to(book_root)
            backup_path = backup_dir / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)
            atomic_write_json(path, converted)

        print(f"Files converted: {len(changes)}")
        print(f"Backups: {backup_dir}")
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
