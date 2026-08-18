"""Build dialogue-attribution fine-tuning data from a range of books."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

# We set a fixed seed so your dataset is reproducible across runs
random.seed(42)

# Notice how your deterministic heuristics have been translated into LLM rules!
SYSTEM_PROMPT = (
    "You are an expert dialogue attribution AI. Read the metadata and the text context. "
    "The target dialogue is wrapped in <quote> tags. Output ONLY the characterId of the speaker, or 'None'. "
    "Do not include any other text.\n\n"
    "Rules:\n"
    "1. Output 'None' if the text inside the tags is pure narration or if the speaker is completely ambiguous.\n"
    "2. Explicit tags (e.g., 'Alice said') immediately before or after the quote take highest priority.\n"
    "3. Tag continuation: If the pattern is dialogue-narration-dialogue, the speaker usually remains the explicitly tagged character.\n"
    "4. Contiguous dialogue: Adjacent quotes in the same paragraph usually inherit the previous speaker.\n"
    "5. Do not confuse the person being addressed with the speaker."
)
CONTEXT_WINDOW = 600


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create one JSONL fine-tuning dataset from chapter.txt/dialogue.json "
            "pairs in Book-N directories."
        )
    )
    parser.add_argument(
        "source_root",
        type=Path,
        help="Directory containing Book-N folders (for example, .../Primal-Hunter).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("training_data.jsonl"),
        help="Combined JSONL output path (default: ./training_data.jsonl).",
    )
    parser.add_argument("--first-book", type=int, default=13)
    parser.add_argument("--last-book", type=int, default=17)
    parser.add_argument("--context-window", type=int, default=CONTEXT_WINDOW)
    parser.add_argument(
        "--narrator-ratio",
        type=float,
        default=0.20,
        help="Percentage of 'narrator' lines to keep (as 'None') to prevent class imbalance (0.0 to 1.0).",
    )
    parser.add_argument(
        "--dataset-ratio",
        type=float,
        default=1.0,
        help="Exact fraction of generated examples to retain (default: 1.0).",
    )
    parser.add_argument(
        "--characters-file",
        type=Path,
        help="Optional character-reference JSON to include in every user prompt.",
    )
    return parser.parse_args()


def load_dialogue_lines(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    lines = data.get("lines") if isinstance(data, dict) else data
    if not isinstance(lines, list):
        raise ValueError("expected a JSON array or an object containing a 'lines' array")
    if not all(isinstance(item, dict) for item in lines):
        raise ValueError("every dialogue line must be a JSON object")
    return lines


def get_last_speaker(current_index: int, data: list[dict[str, Any]]) -> str:
    """Find the last labeled character who spoke, excluding the narrator/None."""
    for item in reversed(data[:current_index]):
        character_id = item.get("characterId")
        if isinstance(character_id, str) and character_id and character_id not in ("narrator", "None"):
            return character_id
    return "None"


def get_scene_characters(
    current_index: int, data: list[dict[str, Any]], window: int = 5
) -> str:
    """Return a stable roster of labeled characters surrounding a line."""
    start = max(0, current_index - window)
    end = min(len(data), current_index + window + 1)
    roster = {
        character_id
        for item in data[start:end]
        if isinstance((character_id := item.get("characterId")), str)
        and character_id
        and character_id not in ("narrator", "None")
    }
    return ", ".join(sorted(roster)) if roster else "None"


def discover_pairs(
    source_root: Path, first_book: int, last_book: int
) -> tuple[list[tuple[Path, Path]], int, int]:
    pairs: list[tuple[Path, Path]] = []
    missing_dialogue = 0
    missing_chapter = 0

    for book_number in range(first_book, last_book + 1):
        book_dir = source_root / f"Book-{book_number}"
        if not book_dir.is_dir():
            raise FileNotFoundError(f"book directory not found: {book_dir}")

        chapter_files = sorted(book_dir.rglob("chapter.txt"))
        dialogue_files = sorted(book_dir.rglob("dialogue.json"))
        chapter_dirs = {path.parent for path in chapter_files}

        for chapter_path in chapter_files:
            dialogue_path = chapter_path.with_name("dialogue.json")
            if dialogue_path.is_file():
                pairs.append((chapter_path, dialogue_path))
            else:
                missing_dialogue += 1

        missing_chapter += sum(
            1 for dialogue_path in dialogue_files if dialogue_path.parent not in chapter_dirs
        )

    return pairs, missing_dialogue, missing_chapter


def create_rows(
    raw_text: str,
    dialogue_data: list[dict[str, Any]],
    context_window: int,
    narrator_ratio: float,
    character_reference: str | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, int]:
    rows: list[dict[str, Any]] = []
    unlabeled = 0
    excluded = 0
    invalid_spans = 0
    text_mismatches = 0

    for index, item in enumerate(dialogue_data):
        original_char_id = item.get("characterId")
        if not isinstance(original_char_id, str) or not original_char_id.strip():
            unlabeled += 1
            continue
            
        # Downsample narrator class to combat LLM guess-bias
        if original_char_id == "narrator":
            if random.random() > narrator_ratio:
                excluded += 1
                continue
            character_id = "None"
        else:
            character_id = original_char_id

        span = item.get("span")
        if (
            not isinstance(span, dict)
            or not isinstance(span.get("start"), int)
            or not isinstance(span.get("end"), int)
        ):
            invalid_spans += 1
            continue

        start = span["start"]
        end = span["end"]
        if start < 0 or end < start or start >= len(raw_text):
            invalid_spans += 1
            continue
        if end > len(raw_text):
            invalid_spans += 1
            end = len(raw_text)

        target_text = raw_text[start:end]
        stored_text = item.get("text")
        if isinstance(stored_text, str) and stored_text != target_text:
            text_mismatches += 1

        # Spatial Awareness: Injecting tags directly into the sliced text
        context_start = max(0, start - context_window)
        context_end = min(len(raw_text), end + context_window)
        
        text_before = raw_text[context_start:start]
        text_after = raw_text[end:context_end]
        
        # We wrap the quote exactly where it belongs in the paragraph
        context_with_tags = f"{text_before}<quote>{target_text}</quote>{text_after}".strip()

        reference_content = (
            f"Character Reference (JSON):\n{character_reference}\n\n"
            if character_reference is not None
            else ""
        )
        
        # Clean User Content (Notice Target Quote block is removed)
        user_content = (
            reference_content
            + f"Scene Characters: {get_scene_characters(index, dialogue_data)}\n"
            f"Last Speaker: {get_last_speaker(index, dialogue_data)}\n\n"
            f"Context:\n{context_with_tags}"
        )
        
        rows.append(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": character_id},
                ]
            }
        )

    return rows, unlabeled, excluded, invalid_spans, text_mismatches


def main() -> None:
    args = parse_args()
    if args.first_book > args.last_book:
        raise ValueError("--first-book must be less than or equal to --last-book")
    if args.context_window < 0:
        raise ValueError("--context-window cannot be negative")
    if not 0.0 <= args.narrator_ratio <= 1.0:
        raise ValueError("--narrator-ratio must be between 0.0 and 1.0")
    if not 0.0 <= args.dataset_ratio <= 1.0:
        raise ValueError("--dataset-ratio must be between 0.0 and 1.0")

    source_root = args.source_root.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    character_reference = None
    if args.characters_file is not None:
        characters_path = args.characters_file.expanduser().resolve()
        with characters_path.open("r", encoding="utf-8") as characters_file:
            characters_data = json.load(characters_file)
        character_reference = json.dumps(
            characters_data, ensure_ascii=False, separators=(",", ":")
        )

    pairs, missing_dialogue, missing_chapter = discover_pairs(
        source_root, args.first_book, args.last_book
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    sample_temp_path = output_path.with_suffix(output_path.suffix + ".sample.tmp")
    examples = unlabeled = excluded = invalid_spans = text_mismatches = failed_files = 0

    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as output_file:
            for chapter_path, dialogue_path in pairs:
                try:
                    with chapter_path.open("r", encoding="utf-8", newline="") as chapter_file:
                        raw_text = chapter_file.read()
                    dialogue_data = load_dialogue_lines(dialogue_path)
                    rows, skipped, filtered, bad_spans, mismatches = create_rows(
                        raw_text,
                        dialogue_data,
                        args.context_window,
                        narrator_ratio=args.narrator_ratio,
                        character_reference=character_reference,
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                    failed_files += 1
                    print(f"WARNING: skipped {dialogue_path}: {error}")
                    continue

                for row in rows:
                    output_file.write(json.dumps(row, ensure_ascii=False) + "\n")
                examples += len(rows)
                unlabeled += skipped
                excluded += filtered
                invalid_spans += bad_spans
                text_mismatches += mismatches

        generated_examples = examples
        if args.dataset_ratio < 1.0:
            retained_count = round(generated_examples * args.dataset_ratio)
            retained_indexes = set(
                random.Random(42).sample(range(generated_examples), retained_count)
            )
            with temp_path.open("rb") as source_file, sample_temp_path.open(
                "wb"
            ) as sampled_file:
                for index, line in enumerate(source_file):
                    if index in retained_indexes:
                        sampled_file.write(line)
            sample_temp_path.replace(output_path)
            temp_path.unlink()
            examples = retained_count
        else:
            temp_path.replace(output_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        sample_temp_path.unlink(missing_ok=True)
        raise

    print(f"Books: {args.first_book}-{args.last_book}")
    print(f"Chapter/dialogue pairs processed: {len(pairs) - failed_files}/{len(pairs)}")
    if args.dataset_ratio < 1.0:
        print(f"Examples generated before dataset sampling: {generated_examples}")
        print(f"Dataset sampling ratio: {args.dataset_ratio:.2%}")
    print(f"Examples written: {examples}")
    print(f"Unlabeled lines skipped: {unlabeled}")
    print(f"Downsampled narrator lines excluded: {excluded}")
    print(f"Invalid or clipped spans: {invalid_spans}")
    print(f"Stored-text/span mismatches: {text_mismatches}")
    print(f"Chapters without dialogue.json: {missing_dialogue}")
    print(f"Dialogue files without chapter.txt: {missing_chapter}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
