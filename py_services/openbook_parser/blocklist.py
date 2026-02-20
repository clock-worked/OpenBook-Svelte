import os
import re
import json
import sys
from typing import Dict, List, Optional, Set, Tuple

# Use package-relative import for the parser service
from .dialogue_parser_service import DialogueParserService


def _iter_chapter_items(book_root: str) -> List[Tuple[str, str, str]]:
    items: List[Tuple[str, str, str]] = []
    for name in sorted(os.listdir(book_root)):
        p = os.path.join(book_root, name)
        if not os.path.isdir(p) or not re.match(r"^\d{2}-", name):
            continue
        txt = os.path.join(book_root, f"{name}.txt")
        gold = os.path.join(p, f"{name}.script.json")
        if os.path.exists(txt) and os.path.exists(gold):
            items.append((p, txt, gold))
    return items


def _load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_speaker_blocklist(book_root: str, *, min_count: int = 2, quiet: bool = True) -> Optional[str]:
    """Generate a dynamic speaker blocklist file for a book if gold scripts exist.

    Returns path to the written blocklist JSON, or None if nothing was generated.
    """
    book_root = os.path.abspath(book_root)
    items = _iter_chapter_items(book_root)
    if not items:
        return None

    svc = DialogueParserService()

    gold_speakers: Set[str] = set()
    pred_counts: Dict[str, int] = {}

    for chapter_dir, txt_path, gold_path in items:
        gold = _load_json(gold_path)
        for line in gold.get("lines", []):
            sp = line.get("chosenSpeaker")
            if sp and sp != "Narrator":
                gold_speakers.add(sp)

        parsed_lines, _ = svc.parse_file(txt_path)
        for pl in parsed_lines:
            if getattr(pl, "line_type", "") != "dialogue":
                continue
            sp = getattr(pl, "speaker", None)
            if not sp or sp == "Narrator":
                continue
            pred_counts[sp] = pred_counts.get(sp, 0) + 1

    blocked: List[str] = []
    for sp, cnt in sorted(pred_counts.items(), key=lambda kv: (-kv[1], kv[0].lower())):
        if sp not in gold_speakers and cnt >= min_count:
            blocked.append(sp)

    out_path = os.path.join(book_root, "speaker_blocklist.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"blocked_speakers": blocked, "min_count": min_count}, f, ensure_ascii=False, indent=2)

    if not quiet:
        print(f"Wrote blocklist with {len(blocked)} entries to: {out_path}", file=sys.stderr)
        if blocked:
            print("Top blocked:", file=sys.stderr)
            for name in blocked[:25]:
                print(f"  {name}: {pred_counts.get(name, 0)}", file=sys.stderr)

    return out_path


