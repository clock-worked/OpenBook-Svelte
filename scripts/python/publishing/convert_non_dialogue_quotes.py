from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_END_PUNCTUATION = ".!?,-"


def convert_line(line: str, end_punctuation: str) -> tuple[str, int]:
    """Convert paired straight quotes whose contents do not end like dialogue."""
    pieces: list[str] = []
    converted = 0
    i = 0

    while i < len(line):
        if line[i] != '"':
            pieces.append(line[i])
            i += 1
            continue

        close_index = line.find('"', i + 1)
        if close_index == -1:
            pieces.append(line[i:])
            break

        quoted_text = line[i + 1 : close_index]
        stripped_text = quoted_text.rstrip()

        if stripped_text and stripped_text[-1] not in end_punctuation:
            pieces.append(f"'{quoted_text}'")
            converted += 1
        else:
            pieces.append(line[i : close_index + 1])

        i = close_index + 1

    return "".join(pieces), converted


def convert_text(text: str, end_punctuation: str) -> tuple[str, int]:
    converted_lines: list[str] = []
    converted = 0

    for line in text.splitlines(keepends=True):
        converted_line, line_converted = convert_line(line, end_punctuation)
        converted_lines.append(converted_line)
        converted += line_converted

    return "".join(converted_lines), converted


def iter_files(paths: list[Path], suffix: str, name: str | None) -> list[Path]:
    files: list[Path] = []

    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob(f"*{suffix}") if p.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(path)

    if name is not None:
        files = [path for path in files if path.name == name]

    return files


def read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    return raw.decode("utf-8"), "utf-8"


def write_text(path: Path, text: str, encoding: str) -> None:
    if encoding == "utf-8-sig":
        path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    else:
        path.write_bytes(text.encode(encoding))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replace straight double quotes with single quotes when the quoted "
            "text does not end with dialogue punctuation."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Files or directories to process.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag the script only prints what it would change.",
    )
    parser.add_argument(
        "--suffix",
        default=".txt",
        help="File suffix to process when a directory is passed. Default: .txt",
    )
    parser.add_argument(
        "--name",
        help="Only process files with this exact name, such as chapter.txt.",
    )
    parser.add_argument(
        "--end-punctuation",
        default=DEFAULT_END_PUNCTUATION,
        help='Characters that mean a quote should be left alone. Default: ".!?,-"',
    )
    parser.add_argument(
        "--fix-doubled-crlf",
        action="store_true",
        help="Repair files from the earlier Windows newline bug by changing CRCRLF back to CRLF.",
    )
    parser.add_argument(
        "--newlines-only",
        action="store_true",
        help="Only repair doubled CRLF newlines; do not convert any quotes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.newlines_only and not args.fix_doubled_crlf:
        raise SystemExit("--newlines-only requires --fix-doubled-crlf")

    files = iter_files(args.paths, args.suffix, args.name)
    changed_files = 0
    total_converted = 0

    for path in files:
        text, encoding = read_text(path)
        if args.newlines_only:
            converted_text = text
            converted = 0
        else:
            converted_text, converted = convert_text(text, args.end_punctuation)

        if args.fix_doubled_crlf:
            converted_text = converted_text.replace("\r\r\n", "\r\n")

        if converted == 0 and converted_text == text:
            continue

        changed_files += 1
        total_converted += converted
        action = "updated" if args.apply else "would update"
        repairs = text.count("\r\r\n") if args.fix_doubled_crlf else 0
        details = [f"{converted} quote pair{'s' if converted != 1 else ''}"]
        if repairs:
            details.append(f"{repairs} newline repair{'s' if repairs != 1 else ''}")
        print(f"{action}: {path} ({', '.join(details)})")

        if args.apply:
            write_text(path, converted_text, encoding)

    if args.apply:
        print(f"Done. Updated {changed_files} file(s), converted {total_converted} quote pair(s).")
    else:
        print(
            f"Dry run. Would update {changed_files} file(s), "
            f"converting {total_converted} quote pair(s)."
        )
        print("Run again with --apply to write the changes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
