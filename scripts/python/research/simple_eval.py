#!/usr/bin/env python3
"""Simple evaluation script that compares curated results without running the full parser."""

import argparse
import json
import os
import re
from collections import defaultdict
from typing import Dict, List, Tuple


def iter_chapter_items(book_root: str) -> List[Tuple[str, str, str]]:
    """Yield (chapter_dir, txt, gold_script_json) for each chapter that has both files."""
    items = []
    for name in sorted(os.listdir(book_root)):
        p = os.path.join(book_root, name)
        if not os.path.isdir(p) or not re.match(r"^\d{2}-", name):
            continue
        txt = os.path.join(book_root, f"{name}.txt")
        gold = os.path.join(p, f"{name}.script.json")
        if os.path.exists(txt) and os.path.exists(gold):
            items.append((p, txt, gold))
    return items


def load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def analyze_curated_data(book_root: str) -> None:
    """Analyze the curated data to understand patterns and conflicts."""
    print("Analyzing curated Book-1 data...")
    
    total_lines = 0
    dialogue_lines = 0
    character_counts = defaultdict(int)
    conflicts = []
    
    for chapter_dir, txt_path, gold_path in iter_chapter_items(book_root):
        chapter_name = os.path.basename(chapter_dir)
        print(f"\n--- {chapter_name} ---")
        
        gold = load_json(gold_path)
        gold_lines = gold.get("lines", [])
        
        chapter_dialogue = 0
        chapter_total = len(gold_lines)
        
        for i, line in enumerate(gold_lines):
            speaker = line.get("chosenSpeaker", "")
            text = line.get("text", "")
            is_conflict = line.get("isConflict", False)
            candidates = line.get("candidates", [])
            
            total_lines += 1
            if speaker and speaker != "Narrator":
                dialogue_lines += 1
                chapter_dialogue += 1
                character_counts[speaker] += 1
            
            if is_conflict:
                conflicts.append({
                    "chapter": chapter_name,
                    "line": i,
                    "speaker": speaker,
                    "text": text[:100] + "..." if len(text) > 100 else text,
                    "candidates": [c.get("name", "") for c in candidates]
                })
        
        print(f"  Total lines: {chapter_total}")
        print(f"  Dialogue lines: {chapter_dialogue}")
        print(f"  Conflicts: {len([c for c in conflicts if c['chapter'] == chapter_name])}")
    
    print(f"\n=== SUMMARY ===")
    print(f"Total lines: {total_lines}")
    print(f"Dialogue lines: {dialogue_lines}")
    print(f"Total conflicts: {len(conflicts)}")
    
    print(f"\nCharacter frequency:")
    for char, count in sorted(character_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {char}: {count}")
    
    print(f"\nTop conflicts (first 10):")
    for conflict in conflicts[:10]:
        print(f"  [{conflict['chapter']}] {conflict['speaker']} -> {conflict['candidates']}")
        print(f"    Text: {conflict['text']}")
    
    # Analyze conflict patterns
    print(f"\nConflict analysis:")
    conflict_patterns = defaultdict(int)
    for conflict in conflicts:
        if len(conflict['candidates']) >= 2:
            pattern = f"{conflict['candidates'][0]} vs {conflict['candidates'][1]}"
            conflict_patterns[pattern] += 1
    
    for pattern, count in sorted(conflict_patterns.items(), key=lambda x: x[1], reverse=True):
        print(f"  {pattern}: {count}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze curated Book-1 data patterns")
    ap.add_argument("book_root", help="Path to Book-X directory")
    args = ap.parse_args()
    
    book_root = os.path.abspath(args.book_root)
    analyze_curated_data(book_root)


if __name__ == "__main__":
    main()
