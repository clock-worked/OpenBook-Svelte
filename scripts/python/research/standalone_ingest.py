#!/usr/bin/env python3
"""Standalone ingestion script that doesn't require ML dependencies."""

import argparse
import json
import os
import re
from typing import Dict, List


def iter_chapter_dirs(book_root: str) -> List[str]:
    entries = []
    for name in sorted(os.listdir(book_root)):
        p = os.path.join(book_root, name)
        if os.path.isdir(p) and re.match(r"^\d{2}-", name):
            entries.append(p)
    return entries


def load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest curated scripts to update book.characters.json knowledge store")
    ap.add_argument("book_root", help="Path to Book-X directory containing book.characters.json and chapter subfolders")
    args = ap.parse_args()

    book_root = os.path.abspath(args.book_root)
    book_characters_path = os.path.join(book_root, "book.characters.json")
    
    # Load existing book.characters.json
    characters = {}
    if os.path.exists(book_characters_path):
        with open(book_characters_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("characters", []):
            name = entry.get("name")
            if name:
                characters[name] = {
                    "name": name,
                    "gender": entry.get("gender", "u"),
                    "aliases": list(entry.get("aliases", [])),
                    "first_seen_chapter": entry.get("first_seen_chapter"),
                    "count_dialogue": entry.get("count_dialogue", entry.get("count", 0)),
                    "count_mentions": entry.get("count_mentions", 0),
                    "color": entry.get("color"),
                    "voice": entry.get("voice"),
                }
    
    # Known aliases from the original parser
    KNOWN_ALIASES = {
        "Catherine Foundling": "Catherine", "the dark-haired sergeant": "Ebele",
        "the dark-skinned woman": "Booker", "a woman in the back": "Unknown Women",
        "the green-eyed man": "Black", "the dark-skinned man": "Warlock",
        "the scarred woman": "Ebele", "The stocky man": "Fenn", "the Black Knight": "Black",
        "The balding man": "Harrion", "the dark-haired man": "Black", "the old man": "Harrion",
        "Black Knight": "Black", "Lord Black": "Black", "The pale mage": "Zacharis",
        "Foundling": "Catherine", "the voice": "Captain",
        "Solider1": "Allen", "Solider2": "Joseph", "Knight": "Black",
        "Sir": "Black"
    }
    
    # Pre-seed known aliases
    for alias, canonical in KNOWN_ALIASES.items():
        if canonical not in characters:
            characters[canonical] = {
                "name": canonical,
                "gender": "u",
                "aliases": [],
                "first_seen_chapter": None,
                "count_dialogue": 0,
                "count_mentions": 0,
            }
        if alias not in characters[canonical]["aliases"]:
            characters[canonical]["aliases"].append(alias)

    noisy_tokens = {"their", "those grooves", "the voice"}

    print("Processing chapters...")
    for chapter_dir in iter_chapter_dirs(book_root):
        chapter_name = os.path.basename(chapter_dir)
        print(f"  Processing {chapter_name}...")
        
        chars_json = os.path.join(chapter_dir, f"{chapter_name}.characters.json")
        script_json = os.path.join(chapter_dir, f"{chapter_name}.script.json")

        # Process character definitions
        if os.path.exists(chars_json):
            cj = load_json(chars_json)
            for c in cj.get("characters", []):
                name = c.get("name")
                if not name or name in noisy_tokens:
                    continue
                if name not in characters:
                    characters[name] = {
                        "name": name,
                        "gender": "u",
                        "aliases": [],
                        "first_seen_chapter": chapter_name,
                        "count_dialogue": 0,
                        "count_mentions": 0,
                        "color": c.get("color"),
                        "voice": c.get("voice"),
                    }
                else:
                    if not characters[name]["first_seen_chapter"]:
                        characters[name]["first_seen_chapter"] = chapter_name

        # Process script dialogue counts
        if os.path.exists(script_json):
            sj = load_json(script_json)
            for line in sj.get("lines", []):
                chosen = line.get("chosenSpeaker")
                if chosen and chosen != "Narrator" and chosen not in noisy_tokens:
                    if chosen not in characters:
                        characters[chosen] = {
                            "name": chosen,
                            "gender": "u",
                            "aliases": [],
                            "first_seen_chapter": chapter_name,
                            "count_dialogue": 0,
                            "count_mentions": 0,
                        }
                    characters[chosen]["count_dialogue"] += 1
                    if not characters[chosen]["first_seen_chapter"]:
                        characters[chosen]["first_seen_chapter"] = chapter_name
                
                # Mention counts from text
                text = line.get("text", "")
                for name in list(characters.keys()):
                    if name and name != "Narrator":
                        if re.search(rf"\b{re.escape(name)}\b", text):
                            characters[name]["count_mentions"] += 1

        # Heuristic alias harvesting from narration phrases
        if os.path.exists(script_json):
            sj = load_json(script_json)
            for line in sj.get("lines", []):
                text = line.get("text", "")
                # Patterns like "Lord Black", "the Black Knight"
                for canonical in list(characters.keys()):
                    if canonical and canonical != "Narrator":
                        pattern1 = rf"\b(Lord\s+{re.escape(canonical)})\b"
                        pattern2 = rf"\b(the\s+{re.escape(canonical)}\s+\w+)\b"
                        for m in re.finditer(pattern1, text, re.I):
                            alias = m.group(1)
                            if alias not in characters[canonical]["aliases"]:
                                characters[canonical]["aliases"].append(alias)
                        for m in re.finditer(pattern2, text, re.I):
                            alias = m.group(1)
                            if alias not in characters[canonical]["aliases"]:
                                characters[canonical]["aliases"].append(alias)

    # Save updated book.characters.json
    output = {"characters": []}
    for name, entry in sorted(characters.items(), key=lambda kv: kv[0].lower()):
        output["characters"].append({
            "name": name,
            "gender": entry.get("gender", "u"),
            "aliases": sorted(entry.get("aliases", [])),
            "first_seen_chapter": entry.get("first_seen_chapter"),
            "count_dialogue": entry.get("count_dialogue", 0),
            "count_mentions": entry.get("count_mentions", 0),
            "color": entry.get("color"),
            "voice": entry.get("voice"),
        })
    
    with open(book_characters_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\nUpdated {book_characters_path}")
    print(f"Total characters: {len(characters)}")
    
    # Show top characters by dialogue count
    top_chars = sorted(characters.items(), key=lambda x: x[1].get("count_dialogue", 0), reverse=True)[:10]
    print("\nTop characters by dialogue:")
    for name, data in top_chars:
        print(f"  {name}: {data.get('count_dialogue', 0)} dialogue, {data.get('count_mentions', 0)} mentions")


if __name__ == "__main__":
    main()
