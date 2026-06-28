"""Combine book character files into one stripped character catalog."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Combine Book-*/characters.json files into a stripped catalog with "
            "only id, name, gender, and aliases."
        )
    )
    parser.add_argument(
        "root",
        type=Path,
        help="Series directory containing Book-* folders.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Destination path. Defaults to characters.stripped.json in the "
            "series directory."
        ),
    )
    return parser.parse_args()


def natural_sort_key(path: Path) -> list[int | str]:
    """Sort paths so Book-9 precedes Book-10."""

    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(path))
    ]


def discover_character_files(root: Path) -> list[Path]:
    """Find book-level character catalogs in book-number order."""

    return sorted(
        (path for path in root.glob("Book-*/characters.json") if path.is_file()),
        key=natural_sort_key,
    )


def load_characters(path: Path) -> list[dict[str, Any]]:
    """Load and validate one book-level character catalog."""

    try:
        with path.open("r", encoding="utf-8-sig") as source:
            payload = json.load(source)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {path} at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error

    if not isinstance(payload, dict):
        raise ValueError(f"Character file must contain a JSON object: {path}")

    characters = payload.get("characters")
    if not isinstance(characters, list):
        raise ValueError(f"Character file must contain a 'characters' array: {path}")

    for index, character in enumerate(characters):
        if not isinstance(character, dict):
            raise ValueError(
                f"Character at index {index} must be a JSON object: {path}"
            )
    return characters


def clean_optional_text(value: Any) -> str | None:
    """Return trimmed text or None for empty values."""

    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def name_quality(value: str | None) -> tuple[int, int]:
    """Rank canonical names by casing quality, then descriptive length."""

    if value is None:
        return (0, 0)
    has_upper = any(character.isupper() for character in value)
    has_lower = any(character.islower() for character in value)
    if value.istitle():
        casing_quality = 3
    elif has_upper and has_lower:
        casing_quality = 2
    elif has_upper:
        casing_quality = 1
    else:
        casing_quality = 0
    return (casing_quality, len(value))


def gender_quality(value: str | None) -> int:
    """Prefer a known gender over Unknown or a missing value."""

    if value is None:
        return 0
    if value.casefold() == "unknown":
        return 1
    return 2


def merge_aliases(existing: list[str], incoming: Any) -> None:
    """Append unique, non-empty aliases while preserving their first spelling."""

    if incoming is None:
        return
    if not isinstance(incoming, list):
        raise ValueError("Character 'aliases' must be an array or null.")

    seen = {alias.casefold() for alias in existing}
    for value in incoming:
        alias = clean_optional_text(value)
        if alias is None or alias.casefold() in seen:
            continue
        existing.append(alias)
        seen.add(alias.casefold())


def combine_characters(
    character_files: list[Path],
) -> tuple[list[dict[str, Any]], int]:
    """Merge characters by ID, favoring newer non-empty scalar values."""

    combined_by_id: dict[str, dict[str, Any]] = {}
    source_rows = 0

    for path in character_files:
        for index, source in enumerate(load_characters(path)):
            source_rows += 1
            character_id = clean_optional_text(source.get("id"))
            if character_id is None:
                raise ValueError(f"Character at index {index} has no valid id: {path}")

            name = clean_optional_text(source.get("name"))
            gender = clean_optional_text(source.get("gender"))
            existing = combined_by_id.get(character_id)

            if existing is None:
                existing = {
                    "id": character_id,
                    "name": name,
                    "gender": gender,
                    "aliases": [],
                }
                combined_by_id[character_id] = existing
            else:
                previous_name = existing["name"]
                if name is not None and name.casefold() != str(previous_name).casefold():
                    if name_quality(name) > name_quality(previous_name):
                        merge_aliases(existing["aliases"], [previous_name])
                        existing["name"] = name
                    else:
                        merge_aliases(existing["aliases"], [name])
                if gender_quality(gender) >= gender_quality(existing["gender"]):
                    existing["gender"] = gender

            merge_aliases(existing["aliases"], source.get("aliases"))

    for character in combined_by_id.values():
        canonical_name = character["name"]
        if canonical_name is not None:
            character["aliases"] = [
                alias
                for alias in character["aliases"]
                if alias.casefold() != canonical_name.casefold()
            ]

    return list(combined_by_id.values()), source_rows


def write_json(path: Path, characters: list[dict[str, Any]]) -> None:
    """Write the combined stripped catalog as UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"characters": characters}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Combine and strip all book-level character files."""

    args = parse_args()
    root = args.root.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else root / "characters.stripped.json"
    )

    if not root.is_dir():
        raise SystemExit(f"Series directory does not exist: {root}")

    character_files = discover_character_files(root)
    if not character_files:
        raise SystemExit(f"No Book-*/characters.json files found under: {root}")
    if output_path in character_files:
        raise SystemExit("Output path must be different from every input file.")

    try:
        characters, source_rows = combine_characters(character_files)
        write_json(output_path, characters)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error

    print(
        f"Combined {source_rows} rows from {len(character_files)} books into "
        f"{len(characters)} characters at {output_path}"
    )


if __name__ == "__main__":
    main()
