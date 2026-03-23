"""Remove blank lines from chapter bodies while preserving the title separator."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileResult:
    """Capture blank-line cleanup results for one chapter file."""

    path: Path
    removed_blank_lines: int
    inserted_title_separator: bool


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the chapter blank-line cleanup."""

    parser = argparse.ArgumentParser(
        description=(
            "Remove blank lines from chapter.txt bodies while preserving a single "
            "blank title separator at line 2."
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
        help="Print one line of detail per changed file.",
    )
    return parser.parse_args()


def detect_newline(text: str) -> str:
    """Return the dominant newline sequence for a text payload."""

    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def discover_chapter_files(book_dir: Path) -> list[Path]:
    """Find all chapter.txt files beneath a book directory."""

    return sorted(path for path in book_dir.rglob("chapter.txt") if path.is_file())


def strip_blank_lines_from_body(path: Path, *, dry_run: bool) -> FileResult:
    """Preserve the title separator and remove later blank lines."""

    original_text = path.read_text(encoding="utf-8")
    newline = detect_newline(original_text)
    had_trailing_newline = original_text.endswith(("\n", "\r"))
    original_lines = original_text.splitlines()

    if not original_lines:
        return FileResult(
            path=path,
            removed_blank_lines=0,
            inserted_title_separator=False,
        )

    title_line = original_lines[0]
    inserted_title_separator = True
    body_start_index = 1
    if len(original_lines) > 1 and not original_lines[1].strip():
        inserted_title_separator = False
        body_start_index = 2

    body_lines = original_lines[body_start_index:]
    cleaned_body_lines: list[str] = []
    removed_blank_lines = 0

    for line in body_lines:
        if not line.strip():
            removed_blank_lines += 1
            continue
        cleaned_body_lines.append(line)

    changed = removed_blank_lines > 0 or inserted_title_separator
    if changed and not dry_run:
        updated_lines = [title_line, "", *cleaned_body_lines]
        updated_text = newline.join(updated_lines)
        if had_trailing_newline:
            updated_text += newline
        path.write_text(updated_text, encoding="utf-8")

    return FileResult(
        path=path,
        removed_blank_lines=removed_blank_lines,
        inserted_title_separator=inserted_title_separator,
    )


def main() -> None:
    """Run the chapter blank-line cleanup against chapter text files."""

    args = parse_args()
    book_dir = args.book_dir.expanduser().resolve()
    if not book_dir.is_dir():
        raise SystemExit(f"Book directory does not exist: {book_dir}")

    chapter_files = discover_chapter_files(book_dir)
    if not chapter_files:
        raise SystemExit(f"No chapter.txt files found under: {book_dir}")

    changed_results: list[FileResult] = []
    total_removed_blank_lines = 0
    title_separator_insertions = 0

    for chapter_file in chapter_files:
        result = strip_blank_lines_from_body(chapter_file, dry_run=args.dry_run)
        total_removed_blank_lines += result.removed_blank_lines
        if result.inserted_title_separator:
            title_separator_insertions += 1
        if result.removed_blank_lines > 0 or result.inserted_title_separator:
            changed_results.append(result)

    action = "Would remove" if args.dry_run else "Removed"
    print(
        f"{action} {total_removed_blank_lines} body blank lines across "
        f"{len(changed_results)} files."
    )
    if title_separator_insertions:
        print(
            f"Ensured a line-2 title separator in {title_separator_insertions} files."
        )

    if not args.verbose:
        return

    for result in changed_results:
        relative_path = result.path.relative_to(book_dir)
        details = f"{relative_path}: removed {result.removed_blank_lines} blank lines"
        if result.inserted_title_separator:
            details += ", inserted title separator"
        print(details)


if __name__ == "__main__":
    main()
