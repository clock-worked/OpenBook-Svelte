#!/usr/bin/env python3
"""
Utility to update character statistics from dialogue.json files.
This should be run whenever dialogue files are modified to keep stats in sync.

Writes to the v3 characters/ folder (one file per character, atomic,
write-only-on-change) when the book has a valid v3 store; not-yet-migrated
books keep the legacy root characters.json single-file write. The folder
is the source of truth once present (IN-2): the legacy write is only
reachable when the folder has no valid v3.0 files.

ADR 0012: the public `scan_character_chapter_counts` /
`derive_character_stats` / `write_character_stats` primitives are shared
with `character_stats_accumulator`, which maintains the in-memory stats
accumulation used by the per-save delta path. `update_character_stats`
itself remains the full book-wide rebuild (manual button, migration).
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from character_store import load_characters, write_character


def character_counts_in_dialogue(data: dict, is_v1: bool) -> Dict[str, int]:
    """Count lines per character in one dialogue document.

    v2.0 format uses 'characterId', v1.0 uses 'chosenSpeaker'. A missing
    or empty speaker is not a character (unattributed line).
    """
    counts: Dict[str, int] = {}
    for line in data.get('lines', []):
        if not isinstance(line, dict):
            continue
        key = line.get('chosenSpeaker') if is_v1 else line.get('characterId')
        if key:
            key = str(key).strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def scan_character_chapter_counts(book_root: Path) -> Tuple[Dict[str, Dict[str, int]], int]:
    """Scan every chapter of the book once.

    Returns ({character: {chapter_id: line_count}}, processed_chapters).
    This is the full O(book) scan: it is the cost the stats accumulation
    (ADR 0012) pays once per book open instead of once per save.
    """
    matrix: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    chapter_dirs = sorted([d for d in book_root.iterdir()
                           if d.is_dir() and not d.name.startswith('.')])

    processed_chapters = 0
    for chapter_dir in chapter_dirs:
        chapter_id = chapter_dir.name

        # Try v2.0 format first (dialogue.json)
        dialogue_path = chapter_dir / "dialogue.json"
        script_path = chapter_dir / f"{chapter_id}.script.json"

        data_file = None
        is_v1 = False
        if dialogue_path.exists():
            data_file = dialogue_path
        elif script_path.exists():
            data_file = script_path
            is_v1 = True
        else:
            continue

        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for character_key, count in character_counts_in_dialogue(data, is_v1).items():
                matrix[character_key][chapter_id] += count
            processed_chapters += 1
        except Exception as e:
            print(f"  Warning: Could not process {data_file}: {e}")
            continue

    return dict(matrix), processed_chapters


def derive_character_stats(matrix: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    """Derive per-character stats (totalLines, chapterCount) from the matrix.

    Characters drained to zero lines in every chapter yield no entry; the
    store-write layer is responsible for zeroing their files.
    """
    return {
        character_key: {
            'totalLines': sum(chapter_counts.values()),
            'chapterCount': len(chapter_counts),
        }
        for character_key, chapter_counts in matrix.items()
        if chapter_counts
    }


def _update_folder_stats(
    book_root: Path,
    records: List[Dict],
    character_stats: Dict,
    processed_chapters: int,
) -> Dict:
    """Rewrite the affected characters/<file>.json files (v3 store).

    Write-only-on-change: stats are compared before each write because
    this runs on every dialogue save and would otherwise fan out to
    N files.
    """
    # Build a normalized version of character_stats for matching
    normalized_stats = {k.lower(): v for k, v in character_stats.items()}

    updated_count = 0
    for character in records:
        char_id = character.get('id')      # v3: the GUID
        char_name = character.get('name')   # v3: the title
        char_id_lower = char_id.lower() if char_id else None
        char_name_lower = char_name.lower() if char_name else None

        new_stats = None
        if char_id and char_id in character_stats:
            new_stats = character_stats[char_id]
        elif char_id_lower and char_id_lower in normalized_stats:
            original_key = next((k for k in character_stats.keys() if k.lower() == char_id_lower), None)
            if original_key:
                new_stats = character_stats[original_key]
        elif char_name and char_name in character_stats:
            new_stats = character_stats[char_name]
        elif char_name_lower and char_name_lower in normalized_stats:
            original_key = next((k for k in character_stats.keys() if k.lower() == char_name_lower), None)
            if original_key:
                new_stats = character_stats[original_key]
        if new_stats is None:
            # Character exists but has no lines
            new_stats = {'totalLines': 0, 'chapterCount': 0}

        existing = character.get('stats')
        existing = existing if isinstance(existing, dict) else {}
        if (existing.get('totalLines') == new_stats['totalLines']
                and existing.get('chapterCount') == new_stats['chapterCount']):
            continue

        character['stats'] = new_stats
        write_character(book_root, character)
        updated_count += 1

    print(f"  Updated {updated_count} character files in characters/")

    return {
        'success': True,
        'updatedCharacters': updated_count,
        'totalCharacters': len(records),
        'processedChapters': processed_chapters
    }


def _update_legacy_stats(
    book_root: Path,
    character_stats: Dict,
    processed_chapters: int,
) -> Dict:
    """Update the legacy root characters.json (full file rewrite)."""
    characters_path = book_root / "characters.json"

    if not characters_path.exists():
        print(f"  Error: {characters_path} not found!")
        return {
            'success': False,
            'error': 'characters.json not found',
            'processedChapters': processed_chapters
        }

    try:
        with open(characters_path, 'r', encoding='utf-8') as f:
            characters_data = json.load(f)

        # Update stats for each character
        # Stats are keyed by either ID (v2.0) or name (v1.0), so check both
        updated_count = 0
        for character in characters_data.get('characters', []):
            char_id = character.get('id')
            char_name = character.get('name')

            # Normalize IDs for comparison (case-insensitive)
            char_id_lower = char_id.lower() if char_id else None
            char_name_lower = char_name.lower() if char_name else None

            # Build a normalized version of character_stats for matching
            normalized_stats = {k.lower(): v for k, v in character_stats.items()}

            # Check both ID and name for matches (handles v1.0 and v2.0)
            matched = False
            if char_id and char_id in character_stats:
                character['stats'] = character_stats[char_id]
                updated_count += 1
                matched = True
            elif char_id_lower and char_id_lower in normalized_stats:
                # Try case-insensitive match on ID
                original_key = next((k for k in character_stats.keys() if k.lower() == char_id_lower), None)
                if original_key:
                    character['stats'] = character_stats[original_key]
                    updated_count += 1
                    matched = True
            elif char_name and char_name in character_stats:
                character['stats'] = character_stats[char_name]
                updated_count += 1
                matched = True
            elif char_name_lower and char_name_lower in normalized_stats:
                original_key = next((k for k in character_stats.keys() if k.lower() == char_name_lower), None)
                if original_key:
                    character['stats'] = character_stats[original_key]
                    updated_count += 1
                    matched = True

            if not matched:
                # Character exists but has no lines
                character['stats'] = {
                    'totalLines': 0,
                    'chapterCount': 0
                }

        # Write updated characters.json
        with open(characters_path, 'w', encoding='utf-8') as f:
            json.dump(characters_data, f, indent=2, ensure_ascii=False)

        print(f"  Updated {updated_count} character entries in {characters_path.name}")

        return {
            'success': True,
            'updatedCharacters': updated_count,
            'totalCharacters': len(characters_data.get('characters', [])),
            'processedChapters': processed_chapters
        }

    except Exception as e:
        print(f"  Error updating {characters_path}: {e}")
        return {
            'success': False,
            'error': str(e),
            'processedChapters': processed_chapters
        }


def write_character_stats(
    book_root: Path,
    character_stats: Dict,
    processed_chapters: int,
) -> Dict:
    """Update the character store with derived stats.

    Shared write dispatch: the v3 characters/ folder when it holds a valid
    v3 store, otherwise the legacy root characters.json. Used by both the
    full rebuild and the per-save delta path (ADR 0012).
    """
    records, source = load_characters(book_root)

    if source == "folder":
        # IN-2: the folder is the source of truth; the retired root
        # characters.json is never written on this path.
        try:
            return _update_folder_stats(book_root, records, character_stats, processed_chapters)
        except Exception as e:
            print(f"  Error updating characters/ folder: {e}")
            return {
                'success': False,
                'error': str(e),
                'processedChapters': processed_chapters
            }

    return _update_legacy_stats(book_root, character_stats, processed_chapters)


def update_character_stats(book_root: Path) -> Dict:
    """
    Scan all dialogue.json files in a book and update the character store
    (v3 characters/ folder, or legacy characters.json) with current stats.

    This is the full book-wide rebuild: the manual "update stats" button
    and the v3 migration. Per-save updates go through
    `character_stats_accumulator.apply_dialogue_stats_delta` instead.

    Args:
        book_root: Path to book directory (e.g., "Book-1/")

    Returns:
        Dict with statistics about the update
    """
    print(f"Updating character stats for: {book_root}")

    matrix, processed_chapters = scan_character_chapter_counts(book_root)
    character_stats = derive_character_stats(matrix)

    print(f"  Processed {processed_chapters} chapters")
    print(f"  Found stats for {len(character_stats)} characters")

    return write_character_stats(book_root, character_stats, processed_chapters)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Update character statistics in characters.json from dialogue files'
    )
    parser.add_argument('book_root', type=Path, help='Path to book root directory')

    args = parser.parse_args()

    if not args.book_root.exists():
        print(f"Error: Book root not found: {args.book_root}")
        return 1

    if not args.book_root.is_dir():
        print(f"Error: Book root is not a directory")
        return 1

    result = update_character_stats(args.book_root)

    if not result.get("success"):
        return 1

    return 0


if __name__ == '__main__':
    exit(main())
