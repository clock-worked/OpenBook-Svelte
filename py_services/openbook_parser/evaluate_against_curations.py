import argparse
import json
import os
import re
from collections import defaultdict
from typing import Dict, List, Tuple

from dialogue_parser_service import DialogueParserService


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


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate parser against curated .script.json gold")
    ap.add_argument("book_root", help="Path to Book-X directory")
    args = ap.parse_args()

    book_root = os.path.abspath(args.book_root)
    svc = DialogueParserService()

    total = 0
    correct = 0
    total_dialogue = 0
    correct_dialogue = 0
    per_character_tp = defaultdict(int)
    per_character_fp = defaultdict(int)
    per_character_fn = defaultdict(int)

    misattrs: List[Dict] = []

    for chapter_dir, txt_path, gold_path in iter_chapter_items(book_root):
        with open(txt_path, "r", encoding="utf-8", newline='') as f:
            text = f.read()
        gold = load_json(gold_path)
        gold_lines = gold.get("lines", [])

        parsed_lines, _ = svc.parse_file(txt_path)
        # Build simple index by order; spans may not perfectly align
        for i, gold_line in enumerate(gold_lines):
            gold_speaker = gold_line.get("chosenSpeaker")
            gold_type = "dialogue" if gold_speaker and gold_speaker != "Narrator" else "narration"
            if i >= len(parsed_lines):
                total += 1
                if gold_type == "dialogue":
                    total_dialogue += 1
                continue
            pred = parsed_lines[i]
            pred_speaker = pred.speaker
            is_correct = (pred_speaker == gold_speaker)
            total += 1
            if gold_type == "dialogue":
                total_dialogue += 1
            if is_correct:
                correct += 1
                if gold_type == "dialogue":
                    correct_dialogue += 1
                    per_character_tp[gold_speaker] += 1
            else:
                if gold_type == "dialogue":
                    per_character_fn[gold_speaker] += 1
                    if pred_speaker and pred_speaker != "Narrator":
                        per_character_fp[pred_speaker] += 1
                    misattrs.append({
                        "chapter": os.path.basename(chapter_dir),
                        "index": i,
                        "text": gold_line.get("text", "")[:160],
                        "gold": gold_speaker,
                        "pred": pred_speaker,
                    })

    print("Results:")
    overall_acc = (correct / total) if total else 0.0
    dialogue_acc = (correct_dialogue / total_dialogue) if total_dialogue else 0.0
    print(f"  Overall accuracy: {overall_acc:.3f} ({correct}/{total})")
    print(f"  Dialogue accuracy: {dialogue_acc:.3f} ({correct_dialogue}/{total_dialogue})")

    print("\nTop misattributions (first 25):")
    for m in misattrs[:25]:
        print(f"  [{m['chapter']}] idx={m['index']} gold={m['gold']} pred={m['pred']} text={m['text']}")

    print("\nPer-character precision/recall (dialogue only):")
    for name in sorted(set(list(per_character_tp.keys()) + list(per_character_fp.keys()) + list(per_character_fn.keys())), key=str.lower):
        tp = per_character_tp[name]
        fp = per_character_fp[name]
        fn = per_character_fn[name]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        print(f"  {name}: P={precision:.3f} R={recall:.3f} (tp={tp}, fp={fp}, fn={fn})")

    # Rule performance emitted by service during parse; nothing to print here to keep it last in stderr


if __name__ == "__main__":
    main()


