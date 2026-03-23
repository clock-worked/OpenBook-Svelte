"""Merge lowercase-leading chapter lines into the previous line."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MergeRecord:
    """Describe one lowercase-leading line merged into a prior line."""

    source_line_number: int
    target_line_number: int
    moved_text: str


@dataclass
class FileResult:
    """Capture the merge result for one chapter file."""

    path: Path
    merge_count: int
    records: list[MergeRecord]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the lowercase line-wrap fixer."""

    parser = argparse.ArgumentParser(
        description=(
            "Find chapter.txt lines that begin with a lowercase word and fold "
            "them into the immediately previous non-empty line."
        )
    )
    parser.add_argument(
        "--book-dir",
        type=Path,
        required=True,
        help="Book directory that contains per-chapter folders with chapter.txt files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each merged line with source and target line numbers.",
    )
    return parser.parse_args()


def detect_newline(text: str) -> str:
    """Return the dominant newline sequence for a text payload."""

    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def starts_with_lowercase_word(line: str) -> bool:
    """Return whether the first alphabetic character in a line is lowercase."""

    stripped = line.lstrip()
    if not stripped:
        return False

    for character in stripped:
        if character.isalpha():
            return character.islower()
        if character.isdigit():
            return False
    return False


def join_lines(previous_line: str, current_line: str) -> str:
    """Join two wrapped lines, preserving hyphenated word continuations."""

    separator = ""
    if not previous_line.rstrip().endswith("-"):
        separator = " "
    return f"{previous_line.rstrip()}{separator}{current_line.strip()}"


def fix_file(path: Path, *, dry_run: bool) -> FileResult:
    """Merge lowercase-leading lines in one chapter file."""

    original_text = path.read_text(encoding="utf-8")
    newline = detect_newline(original_text)
    had_trailing_newline = original_text.endswith(("\n", "\r"))
    original_lines = original_text.splitlines()

    rewritten_lines: list[str] = []
    rewritten_origins: list[int] = []
    records: list[MergeRecord] = []

    for line_number, line in enumerate(original_lines, start=1):
        if (
            starts_with_lowercase_word(line)
            and rewritten_lines
            and rewritten_lines[-1].strip()
        ):
            rewritten_lines[-1] = join_lines(rewritten_lines[-1], line)
            records.append(
                MergeRecord(
                    source_line_number=line_number,
                    target_line_number=rewritten_origins[-1],
                    moved_text=line.strip(),
                )
            )
            continue

        rewritten_lines.append(line)
        rewritten_origins.append(line_number)

    if records and not dry_run:
        updated_text = newline.join(rewritten_lines)
        if had_trailing_newline:
            updated_text += newline
        path.write_text(updated_text, encoding="utf-8")

    return FileResult(path=path, merge_count=len(records), records=records)


def discover_chapter_files(book_dir: Path) -> list[Path]:
    """Find all chapter.txt files beneath a book directory."""

    return sorted(
        path for path in book_dir.rglob("chapter.txt") if path.is_file()
    )


def main() -> None:
    """Run the lowercase-leading line fixer against chapter text files."""

    args = parse_args()
    book_dir = args.book_dir.expanduser().resolve()
    if not book_dir.is_dir():
        raise SystemExit(f"Book directory does not exist: {book_dir}")

    chapter_files = discover_chapter_files(book_dir)
    if not chapter_files:
        raise SystemExit(f"No chapter.txt files found under: {book_dir}")

    changed_results: list[FileResult] = []
    total_merges = 0

    for chapter_file in chapter_files:
        result = fix_file(chapter_file, dry_run=args.dry_run)
        total_merges += result.merge_count
        if result.merge_count:
            changed_results.append(result)

    action = "Would merge" if args.dry_run else "Merged"
    print(
        f"{action} {total_merges} lowercase-leading lines across "
        f"{len(changed_results)} files."
    )

    for result in changed_results:
        relative_path = result.path.relative_to(book_dir)
        print(f"{relative_path}: {result.merge_count} merges")
        if not args.verbose:
            continue
        for record in result.records:
            print(
                "  "
                f"line {record.source_line_number} -> {record.target_line_number}: "
                f"{record.moved_text}"
            )


if __name__ == "__main__":
    main()
