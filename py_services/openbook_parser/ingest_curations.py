import argparse
import json
import os
import re
from typing import Dict, List

from knowledge_store import KnowledgeStore
from dialogue_parser_service import KNOWN_ALIASES


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
    ks = KnowledgeStore(book_root)
    ks.load()

    # Pre-seed known aliases
    for alias, canonical in KNOWN_ALIASES.items():
        ks.add_alias(alias, canonical)

    noisy_tokens = {"their", "those grooves", "the voice"}

    for chapter_dir in iter_chapter_dirs(book_root):
        chapter_name = os.path.basename(chapter_dir)
        chars_json = os.path.join(chapter_dir, f"{chapter_name}.characters.json")
        script_json = os.path.join(chapter_dir, f"{chapter_name}.script.json")
        txt_path = os.path.join(book_root, f"{chapter_name}.txt")

        if os.path.exists(chars_json):
            cj = load_json(chars_json)
            for c in cj.get("characters", []):
                name = c.get("name")
                if not name or name in noisy_tokens:
                    continue
                ks.ensure_character(name)
                ks.update_counts(name, first_seen_chapter=chapter_name)

        if os.path.exists(script_json):
            sj = load_json(script_json)
            for line in sj.get("lines", []):
                chosen = line.get("chosenSpeaker")
                if chosen and chosen != "Narrator" and chosen not in noisy_tokens:
                    ks.update_counts(chosen, dialogue_inc=1, first_seen_chapter=chapter_name)
                # Mention counts from text: naive match of capitalized tokens
                text = line.get("text", "")
                for name in list(ks.genders.keys()):
                    if name and name != "Narrator":
                        if re.search(rf"\b{re.escape(name)}\b", text):
                            ks.update_counts(name, mention_inc=1, first_seen_chapter=chapter_name)

        # Heuristic alias harvesting from narration phrases containing known canonical name
        if os.path.exists(script_json):
            sj = load_json(script_json)
            for line in sj.get("lines", []):
                text = line.get("text", "")
                # Patterns like "Lord Black", "the Black Knight"
                for canonical in list(ks.genders.keys()):
                    if canonical and canonical != "Narrator":
                        pattern1 = rf"\b(Lord\s+{re.escape(canonical)})\b"
                        pattern2 = rf"\b(the\s+{re.escape(canonical)}\s+\w+)\b"
                        for m in re.finditer(pattern1, text, re.I):
                            ks.add_alias(m.group(1), canonical)
                        for m in re.finditer(pattern2, text, re.I):
                            ks.add_alias(m.group(1), canonical)

    ks.save()


if __name__ == "__main__":
    main()


