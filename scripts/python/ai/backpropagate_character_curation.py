"""Backpropagate a curated stripped character catalog into source JSON files."""

# SUPERSEDED (2026-10-06, Character Details v3): this script targets the
# retired v2 `characters.json` store. It is superseded by the v3
# `characters/` folder format (docs/schema_characters_v3.md) and, for
# v2 -> v3 migration, by scripts/python/ai/migrate_characters_v3.py.
# Kept as-is for unmigrated books only; do not run against migrated books.

from __future__ import annotations

import argparse
import json
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from strip_characters_json import combine_characters, discover_character_files


CURATED_FIELDS = ("id", "name", "gender", "aliases")
REMOVE = object()
SpeakerResolver = Callable[[Any], tuple[bool, str | None]]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Backpropagate characters.stripped.json changes into Book-*/"
            "characters.json and chapter dialogue.json files. Runs as a dry-run "
            "unless --apply is supplied."
        )
    )
    parser.add_argument("root", type=Path, help="Series directory containing Book-* folders.")
    parser.add_argument(
        "--master",
        type=Path,
        help="Curated catalog. Defaults to <root>/characters.stripped.json.",
    )
    parser.add_argument(
        "--mappings",
        type=Path,
        required=True,
        help="JSON file defining idMappings and preserveDeletedIds.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write verified changes. Without this flag, only report the dry-run.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Backup destination used with --apply. Defaults beneath <root>/_backups.",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    """Read UTF-8 JSON, accepting an optional byte-order mark."""

    try:
        with path.open("r", encoding="utf-8-sig") as source:
            return json.load(source)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {path} at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error


def json_text(payload: Any) -> str:
    """Serialize JSON using the repository's standard formatting."""

    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def clean_text(value: Any) -> str | None:
    """Return stripped non-empty text or None."""

    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def merge_aliases(*alias_groups: Any) -> list[str]:
    """Combine aliases case-insensitively while preserving first spelling."""

    aliases: list[str] = []
    seen: set[str] = set()
    for group in alias_groups:
        if group is None:
            continue
        if not isinstance(group, list):
            raise ValueError("Character aliases must be an array or null.")
        for value in group:
            alias = clean_text(value)
            if alias is None or alias.casefold() in seen:
                continue
            aliases.append(alias)
            seen.add(alias.casefold())
    return aliases


def gender_rank(value: str | None) -> int:
    """Rank known genders above Unknown and missing values."""

    if value is None:
        return 0
    return 1 if value.casefold() == "unknown" else 2


def normalize_master(
    characters: Any,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    """Validate the master and merge duplicate rows with the same ID."""

    if not isinstance(characters, list):
        raise ValueError("The master catalog must contain a 'characters' array.")

    normalized: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0

    for index, source in enumerate(characters):
        if not isinstance(source, dict):
            raise ValueError(f"Master character at index {index} must be an object.")
        character_id = clean_text(source.get("id"))
        name = clean_text(source.get("name"))
        gender = clean_text(source.get("gender"))
        if character_id is None or name is None:
            raise ValueError(f"Master character at index {index} needs a valid id and name.")

        incoming = {
            "id": character_id,
            "name": name,
            "gender": gender,
            "aliases": merge_aliases(source.get("aliases")),
        }
        existing = by_id.get(character_id)
        if existing is None:
            by_id[character_id] = incoming
            normalized.append(incoming)
            continue

        duplicate_rows += 1
        if existing["name"].casefold() != name.casefold():
            raise ValueError(
                f"Duplicate master ID '{character_id}' has conflicting names: "
                f"'{existing['name']}' and '{name}'."
            )
        old_gender = clean_text(existing.get("gender"))
        if (
            gender_rank(old_gender) == 2
            and gender_rank(gender) == 2
            and old_gender.casefold() != gender.casefold()
        ):
            raise ValueError(
                f"Duplicate master ID '{character_id}' has conflicting genders: "
                f"'{old_gender}' and '{gender}'."
            )
        if gender_rank(gender) > gender_rank(old_gender):
            existing["gender"] = gender
        existing["aliases"] = merge_aliases(existing["aliases"], incoming["aliases"])

    for character in normalized:
        canonical_name = character["name"].casefold()
        character["aliases"] = [
            alias for alias in character["aliases"] if alias.casefold() != canonical_name
        ]
    return normalized, by_id, duplicate_rows


def load_mapping_config(path: Path) -> tuple[dict[str, str], set[str]]:
    """Load explicit ID mappings and intentionally preserved deleted IDs."""

    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("The mapping file must contain a JSON object.")
    raw_mappings = payload.get("idMappings", {})
    raw_preserved = payload.get("preserveDeletedIds", [])
    if not isinstance(raw_mappings, dict):
        raise ValueError("'idMappings' must be an object.")
    if not isinstance(raw_preserved, list):
        raise ValueError("'preserveDeletedIds' must be an array.")

    mappings: dict[str, str] = {}
    for old_id, new_id in raw_mappings.items():
        old_clean = clean_text(old_id)
        new_clean = clean_text(new_id)
        if old_clean is None or new_clean is None:
            raise ValueError("Every ID mapping must have non-empty string IDs.")
        mappings[old_clean] = new_clean

    preserved = {value for item in raw_preserved if (value := clean_text(item))}
    return mappings, preserved


def stripped_character(source: dict[str, Any]) -> dict[str, Any]:
    """Extract the four curated character fields."""

    return {field: deepcopy(source.get(field)) for field in CURATED_FIELDS}


def apply_curated_fields(
    record: dict[str, Any], curated: dict[str, Any]
) -> dict[str, Any]:
    """Overlay curated fields without discarding voice or statistics fields."""

    updated = deepcopy(record)
    for field in CURATED_FIELDS:
        updated[field] = deepcopy(curated[field])
    return updated


def merge_extended_records(
    preferred: dict[str, Any], fallback: dict[str, Any]
) -> dict[str, Any]:
    """Fill missing non-curated configuration from a duplicate record."""

    merged = deepcopy(preferred)
    for key, value in fallback.items():
        if key in CURATED_FIELDS:
            continue
        if key not in merged or merged[key] is None:
            merged[key] = deepcopy(value)
    return merged


def transform_book_catalog(
    payload: Any,
    master_by_id: dict[str, dict[str, Any]],
    id_mappings: dict[str, str],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Update, remove, and deduplicate one book character catalog."""

    if not isinstance(payload, dict) or not isinstance(payload.get("characters"), list):
        raise ValueError("Book character catalog must contain a 'characters' array.")

    records_by_id: dict[str, tuple[dict[str, Any], bool]] = {}
    order: list[str] = []
    stats = {"updated": 0, "removed": 0, "deduplicated": 0}

    for index, source in enumerate(payload["characters"]):
        if not isinstance(source, dict):
            raise ValueError(f"Book character at index {index} must be an object.")
        old_id = clean_text(source.get("id"))
        if old_id is None:
            raise ValueError(f"Book character at index {index} has no valid id.")
        target_id = id_mappings.get(old_id, old_id)
        curated = master_by_id.get(target_id)
        if curated is None:
            stats["removed"] += 1
            continue

        updated = apply_curated_fields(source, curated)
        if updated != source:
            stats["updated"] += 1
        is_direct = old_id == target_id
        existing = records_by_id.get(target_id)
        if existing is None:
            records_by_id[target_id] = (updated, is_direct)
            order.append(target_id)
            continue

        stats["deduplicated"] += 1
        existing_record, existing_direct = existing
        if is_direct and not existing_direct:
            merged = merge_extended_records(updated, existing_record)
            records_by_id[target_id] = (apply_curated_fields(merged, curated), True)
        else:
            merged = merge_extended_records(existing_record, updated)
            records_by_id[target_id] = (
                apply_curated_fields(merged, curated),
                existing_direct,
            )

    updated_payload = deepcopy(payload)
    updated_payload["characters"] = [records_by_id[item][0] for item in order]
    return updated_payload, stats


def transform_nested_character_refs(
    value: Any,
    master_by_id: dict[str, dict[str, Any]],
    id_mappings: dict[str, str],
    speaker_resolver: SpeakerResolver,
    stats: dict[str, int],
) -> Any:
    """Update nested candidate character IDs and prune deleted candidates."""

    if isinstance(value, list):
        updated_items: list[Any] = []
        for item in value:
            updated = transform_nested_character_refs(
                item, master_by_id, id_mappings, speaker_resolver, stats
            )
            if updated is not REMOVE:
                updated_items.append(updated)
            else:
                stats["nested_pruned"] += 1
        return updated_items

    if not isinstance(value, dict):
        return value

    updated = deepcopy(value)
    if "characterId" in updated:
        old_id = clean_text(updated.get("characterId"))
        target_id = id_mappings.get(old_id, old_id) if old_id is not None else None
        if target_id is None or target_id not in master_by_id:
            return REMOVE
        if target_id != old_id:
            updated["characterId"] = target_id
            stats["nested_ids"] += 1
        if "name" in updated:
            canonical_name = master_by_id[target_id]["name"]
            if updated["name"] != canonical_name:
                updated["name"] = canonical_name
                stats["nested_names"] += 1
    elif "name" in updated and "confidence" in updated:
        known, target_id = speaker_resolver(updated.get("name"))
        if known and target_id is None:
            return REMOVE
        if target_id is not None:
            canonical_name = master_by_id[target_id]["name"]
            if updated["name"] != canonical_name:
                updated["name"] = canonical_name
                stats["nested_names"] += 1

    for key, item in list(updated.items()):
        if key in {"characterId", "name"}:
            continue
        transformed = transform_nested_character_refs(
            item, master_by_id, id_mappings, speaker_resolver, stats
        )
        if transformed is REMOVE:
            del updated[key]
        else:
            updated[key] = transformed
    return updated


def transform_dialogue(
    payload: Any,
    master_by_id: dict[str, dict[str, Any]],
    id_mappings: dict[str, str],
    speaker_resolver: SpeakerResolver,
) -> tuple[dict[str, Any], dict[str, int], set[str]]:
    """Update primary and nested character references in one dialogue file."""

    if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
        raise ValueError("Dialogue file must contain a 'lines' array.")
    updated_payload = deepcopy(payload)
    stats = {
        "line_ids": 0,
        "nested_ids": 0,
        "nested_names": 0,
        "nested_pruned": 0,
        "legacy_speakers": 0,
        "legacy_unresolved": 0,
        "modern_unresolved": 0,
    }
    referenced_ids: set[str] = set()

    for index, line in enumerate(updated_payload["lines"]):
        if not isinstance(line, dict):
            raise ValueError(f"Dialogue line at index {index} must be an object.")
        old_id = clean_text(line.get("characterId"))
        if old_id is not None:
            target_id = id_mappings.get(old_id, old_id)
            if target_id not in master_by_id:
                raise ValueError(
                    f"Dialogue line {line.get('id', index)} references missing ID: "
                    f"{old_id}"
                )
            if target_id != old_id:
                line["characterId"] = target_id
                stats["line_ids"] += 1
            referenced_ids.add(target_id)
        elif "characterId" in line:
            stats["modern_unresolved"] += 1
        elif "chosenSpeaker" in line:
            chosen_speaker = clean_text(line.get("chosenSpeaker"))
            if chosen_speaker is None:
                stats["legacy_unresolved"] += 1
            else:
                known, target_id = speaker_resolver(chosen_speaker)
                if not known or target_id is None:
                    raise ValueError(
                        f"Dialogue line {line.get('id', index)} has an unresolved "
                        f"chosenSpeaker: {chosen_speaker}"
                    )
                canonical_name = master_by_id[target_id]["name"]
                if line["chosenSpeaker"] != canonical_name:
                    line["chosenSpeaker"] = canonical_name
                    stats["legacy_speakers"] += 1
                referenced_ids.add(target_id)
        else:
            raise ValueError(
                f"Dialogue line {line.get('id', index)} has neither characterId "
                "nor chosenSpeaker."
            )

        for key, item in list(line.items()):
            if key == "characterId":
                continue
            transformed = transform_nested_character_refs(
                item, master_by_id, id_mappings, speaker_resolver, stats
            )
            if transformed is REMOVE:
                del line[key]
            else:
                line[key] = transformed

    return updated_payload, stats, referenced_ids


def build_speaker_resolver(
    book_payload: dict[str, Any],
    master_by_id: dict[str, dict[str, Any]],
    id_mappings: dict[str, str],
) -> SpeakerResolver:
    """Resolve legacy chosenSpeaker names through the original book catalog."""

    surface_index: dict[str, list[tuple[int, str]]] = {}

    def add_surface(value: Any, old_id: str, rank: int) -> None:
        surface = clean_text(value)
        if surface is None:
            return
        surface_index.setdefault(surface.casefold(), []).append((rank, old_id))

    for source in book_payload["characters"]:
        if not isinstance(source, dict):
            continue
        old_id = clean_text(source.get("id"))
        if old_id is None:
            continue
        add_surface(source.get("id"), old_id, 1)
        add_surface(source.get("name"), old_id, 3)
        aliases = source.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                add_surface(alias, old_id, 2)

    master_surface_index: dict[str, set[str]] = {}
    for character_id, character in master_by_id.items():
        for value in (character_id, character.get("name"), *(character.get("aliases") or [])):
            surface = clean_text(value)
            if surface is not None:
                master_surface_index.setdefault(surface.casefold(), set()).add(character_id)

    def resolve(value: Any) -> tuple[bool, str | None]:
        surface = clean_text(value)
        if surface is None:
            return False, None
        key = surface.casefold()
        matches = surface_index.get(key, [])
        if matches:
            best_rank = max(rank for rank, _ in matches)
            old_ids = {old_id for rank, old_id in matches if rank == best_rank}
            targets = {
                target
                for old_id in old_ids
                if (target := id_mappings.get(old_id, old_id)) in master_by_id
            }
            if len(targets) == 1:
                return True, next(iter(targets))
            if not targets:
                return True, None
            raise ValueError(
                f"Ambiguous legacy speaker '{surface}' maps to IDs: {sorted(targets)}"
            )

        master_targets = master_surface_index.get(key, set())
        if len(master_targets) == 1:
            return True, next(iter(master_targets))
        if len(master_targets) > 1:
            raise ValueError(
                f"Ambiguous master speaker '{surface}' maps to IDs: "
                f"{sorted(master_targets)}"
            )
        return False, None

    return resolve


def ensure_book_references_exist(
    payload: dict[str, Any],
    referenced_ids: set[str],
    master_by_id: dict[str, dict[str, Any]],
) -> int:
    """Add minimal book records for referenced curated IDs not already present."""

    existing_ids = {
        character.get("id")
        for character in payload["characters"]
        if isinstance(character, dict)
    }
    added = 0
    for character_id in sorted(referenced_ids - existing_ids):
        payload["characters"].append(stripped_character(master_by_id[character_id]))
        added += 1
    return added


def prepare_changes(
    root: Path,
    master_path: Path,
    mappings_path: Path,
) -> tuple[dict[Path, str], dict[str, Any]]:
    """Load, transform, and fully validate all proposed file changes."""

    master_payload = read_json(master_path)
    if not isinstance(master_payload, dict):
        raise ValueError("The master catalog must contain a JSON object.")
    master_characters, master_by_id, duplicate_rows = normalize_master(
        master_payload.get("characters")
    )
    id_mappings, preserved_ids = load_mapping_config(mappings_path)

    book_files = discover_character_files(root)
    if not book_files:
        raise ValueError(f"No Book-*/characters.json files found beneath {root}")
    source_characters, source_rows = combine_characters(book_files)
    source_by_id = {character["id"]: character for character in source_characters}

    for character_id in sorted(preserved_ids):
        if character_id in master_by_id:
            continue
        source = source_by_id.get(character_id)
        if source is None:
            raise ValueError(f"Preserved ID is absent from source catalogs: {character_id}")
        restored = stripped_character(source)
        master_characters.append(restored)
        master_by_id[character_id] = restored

    for old_id, new_id in id_mappings.items():
        if new_id not in master_by_id:
            raise ValueError(f"Mapping target is absent from the master: {new_id}")
        if old_id not in source_by_id and new_id not in source_by_id:
            raise ValueError(
                f"Neither mapped source ID nor its target exists in source catalogs: "
                f"{old_id} -> {new_id}"
            )
    overlap = set(id_mappings) & preserved_ids
    if overlap:
        raise ValueError(f"IDs cannot be both mapped and preserved: {sorted(overlap)}")

    dialogue_files = sorted(root.glob("Book-*/*/dialogue.json"))
    changes: dict[Path, str] = {}
    report: dict[str, Any] = {
        "book_files": len(book_files),
        "dialogue_files": len(dialogue_files),
        "source_rows": source_rows,
        "master_characters": len(master_characters),
        "master_duplicates_merged": duplicate_rows,
        "preserved_deleted_ids": sorted(preserved_ids),
        "mapped_ids": len(id_mappings),
        "book_files_changed": 0,
        "dialogue_files_changed": 0,
        "book_records_updated": 0,
        "book_records_removed": 0,
        "book_records_deduplicated": 0,
        "book_records_added": 0,
        "dialogue_line_ids_updated": 0,
        "legacy_speakers_updated": 0,
        "legacy_unresolved": 0,
        "modern_unresolved": 0,
        "nested_ids_updated": 0,
        "nested_names_updated": 0,
        "nested_candidates_pruned": 0,
    }

    dialogues_by_book: dict[Path, list[Path]] = {}
    for path in dialogue_files:
        dialogues_by_book.setdefault(path.parent.parent, []).append(path)

    for book_path in book_files:
        original_book = read_json(book_path)
        updated_book, book_stats = transform_book_catalog(
            original_book, master_by_id, id_mappings
        )
        speaker_resolver = build_speaker_resolver(
            original_book, master_by_id, id_mappings
        )
        referenced_in_book: set[str] = set()

        for dialogue_path in dialogues_by_book.get(book_path.parent, []):
            original_dialogue = read_json(dialogue_path)
            try:
                updated_dialogue, dialogue_stats, referenced = transform_dialogue(
                    original_dialogue,
                    master_by_id,
                    id_mappings,
                    speaker_resolver,
                )
            except ValueError as error:
                raise ValueError(f"{dialogue_path}: {error}") from error
            referenced_in_book.update(referenced)
            if updated_dialogue != original_dialogue:
                changes[dialogue_path] = json_text(updated_dialogue)
                report["dialogue_files_changed"] += 1
            report["dialogue_line_ids_updated"] += dialogue_stats["line_ids"]
            report["legacy_speakers_updated"] += dialogue_stats["legacy_speakers"]
            report["legacy_unresolved"] += dialogue_stats["legacy_unresolved"]
            report["modern_unresolved"] += dialogue_stats["modern_unresolved"]
            report["nested_ids_updated"] += dialogue_stats["nested_ids"]
            report["nested_names_updated"] += dialogue_stats["nested_names"]
            report["nested_candidates_pruned"] += dialogue_stats["nested_pruned"]

        added = ensure_book_references_exist(
            updated_book, referenced_in_book, master_by_id
        )
        report["book_records_added"] += added
        for key in ("updated", "removed", "deduplicated"):
            report[f"book_records_{key}"] += book_stats[key]
        if updated_book != original_book:
            changes[book_path] = json_text(updated_book)
            report["book_files_changed"] += 1

        final_book_ids = {item["id"] for item in updated_book["characters"]}
        missing_from_book = referenced_in_book - final_book_ids
        if missing_from_book:
            raise ValueError(
                f"{book_path} is missing referenced IDs after transformation: "
                f"{sorted(missing_from_book)}"
            )

    normalized_master_payload = {"characters": master_characters}
    if normalized_master_payload != master_payload:
        changes[master_path] = json_text(normalized_master_payload)
        report["master_changed"] = True
    else:
        report["master_changed"] = False

    return changes, report


def apply_changes(
    root: Path,
    changes: dict[Path, str],
    backup_dir: Path,
) -> None:
    """Back up and atomically replace every changed file."""

    for path in changes:
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise ValueError(f"Refusing to change a file outside the series root: {path}") from error
        destination = backup_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

    for path, content in changes.items():
        temporary = path.with_name(f".{path.name}.backprop.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def print_report(report: dict[str, Any], changed_files: int, *, applied: bool) -> None:
    """Print a compact reconciliation summary."""

    action = "Applied" if applied else "Dry-run prepared"
    print(f"{action} {changed_files} changed files.")
    print(
        f"Master: {report['master_characters']} characters, "
        f"{report['master_duplicates_merged']} duplicate rows merged, "
        f"preserved deleted IDs={report['preserved_deleted_ids']}"
    )
    print(
        f"Books: {report['book_files_changed']}/{report['book_files']} files changed; "
        f"records updated={report['book_records_updated']}, "
        f"removed={report['book_records_removed']}, "
        f"deduplicated={report['book_records_deduplicated']}, "
        f"added={report['book_records_added']}"
    )
    print(
        f"Dialogue: {report['dialogue_files_changed']}/{report['dialogue_files']} "
        f"files changed; line IDs={report['dialogue_line_ids_updated']}, "
        f"legacy names={report['legacy_speakers_updated']}, "
        f"legacy unresolved={report['legacy_unresolved']}, "
        f"modern unresolved={report['modern_unresolved']}, "
        f"nested IDs={report['nested_ids_updated']}, "
        f"nested names={report['nested_names_updated']}, "
        f"stale candidates pruned={report['nested_candidates_pruned']}"
    )


def main() -> None:
    """Run a dry-run or apply a fully validated character reconciliation."""

    args = parse_args()
    root = args.root.expanduser().resolve()
    master_path = (
        args.master.expanduser().resolve()
        if args.master
        else root / "characters.stripped.json"
    )
    mappings_path = args.mappings.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Series directory does not exist: {root}")
    if not master_path.is_file():
        raise SystemExit(f"Master catalog does not exist: {master_path}")
    if not mappings_path.is_file():
        raise SystemExit(f"Mapping file does not exist: {mappings_path}")

    try:
        changes, report = prepare_changes(root, master_path, mappings_path)
        if not args.apply:
            print_report(report, len(changes), applied=False)
            print("No files were written. Re-run with --apply after reviewing this report.")
            return

        backup_dir = (
            args.backup_dir.expanduser().resolve()
            if args.backup_dir
            else root
            / "_backups"
            / f"character-backprop-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        apply_changes(root, changes, backup_dir)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error

    print_report(report, len(changes), applied=True)
    print(f"Backups: {backup_dir}")


if __name__ == "__main__":
    main()
