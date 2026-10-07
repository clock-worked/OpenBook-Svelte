#!/usr/bin/env python3
"""
Utility to update character statistics from dialogue.json files.
This should be run whenever dialogue files are modified to keep stats in sync.

Writes to the v3 characters/ folder (one file per character, atomic,
write-only-on-change) when the book has a valid v3 store; not-yet-migrated
books keep the legacy root characters.json single-file write. The folder
is the source of truth once present (IN-2): the legacy write is only
reachable when the folder has no valid v3.0 files.
"""

import json
from pathlib import Path
from typing import Dict, List, Set
from collections import defaultdict

from character_store import load_characters, write_character


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


def update_character_stats(book_root: Path) -> Dict:
    """
    Scan all dialogue.json files in a book and update the character store
    (v3 characters/ folder, or legacy characters.json) with current stats.

    Args:
        book_root: Path to book directory (e.g., "Book-1/")

    Returns:
        Dict with statistics about the update
    """
    print(f"Updating character stats for: {book_root}")
    
    # Initialize stat tracking
    character_stats = defaultdict(lambda: {
        'totalLines': 0,
        'chapterCount': 0,
        'chapters': set()
    })
    
    # Scan all chapter directories for dialogue.json files
    chapter_dirs = sorted([d for d in book_root.iterdir() 
                          if d.is_dir() and not d.name.startswith('.')])
    
    processed_chapters = 0
    
    for chapter_dir in chapter_dirs:
        chapter_id = chapter_dir.name
        
        # Try v2.0 format first (dialogue.json)
        dialogue_path = chapter_dir / "dialogue.json"
        script_path = chapter_dir / f"{chapter_id}.script.json"
        
        data_file = None
        file_format = None
        
        if dialogue_path.exists():
            data_file = dialogue_path
            file_format = 'v2'
        elif script_path.exists():
            data_file = script_path
            file_format = 'v1'
        else:
            continue
        
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Count lines per character in this chapter
            allen_count_in_chapter = 0
            for line in data.get('lines', []):
                # v2.0 format uses 'characterId', v1.0 uses 'chosenSpeaker'
                character_id = line.get('characterId') if file_format == 'v2' else line.get('chosenSpeaker')
                
                if character_id:
                    # Normalize to string and strip whitespace
                    character_id = str(character_id).strip()
                    if character_id:
                        character_stats[character_id]['totalLines'] += 1
                        character_stats[character_id]['chapters'].add(chapter_id)
                        if character_id.lower() == 'allen':
                            allen_count_in_chapter += 1
            
            if allen_count_in_chapter > 0:
                print(f"  DEBUG: Found {allen_count_in_chapter} lines for 'allen' in chapter {chapter_id}")
            
            processed_chapters += 1
            
        except Exception as e:
            print(f"  Warning: Could not process {data_file}: {e}")
            continue
    
    # Convert chapter sets to counts
    for char_id, stats in character_stats.items():
        stats['chapterCount'] = len(stats['chapters'])
        del stats['chapters']  # Remove set, we only need count
    
    print(f"  Processed {processed_chapters} chapters")
    print(f"  Found stats for {len(character_stats)} characters")
    
    # Debug: Print character stats found
    if character_stats:
        sample_chars = list(character_stats.keys())[:10]
        print(f"  Sample character IDs found: {sample_chars}")
        if 'allen' in character_stats:
            print(f"  DEBUG: Found 'allen' with {character_stats['allen']} lines")
        else:
            print(f"  DEBUG: 'allen' NOT found in character_stats. Available keys: {list(character_stats.keys())[:20]}")
    
    # Update the character store (v3 characters/ folder, or legacy characters.json)
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
                # Try case-insensitive match on name
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
                # Debug: Log characters that didn't match
                if char_id == 'allen' or char_name == 'Allen':
                    print(f"  DEBUG: Allen didn't match. char_id='{char_id}', char_name='{char_name}'")
                    print(f"  DEBUG: character_stats keys: {list(character_stats.keys())[:20]}")
        
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
        print(f"Error: Book root is not a directory: {args.book_root}")
        return 1
    
    result = update_character_stats(args.book_root)
    
    if not result.get('success'):
        return 1
    
    return 0


if __name__ == '__main__':
    exit(main())

