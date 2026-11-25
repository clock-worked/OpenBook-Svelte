import argparse
import os
from typing import Optional
import sys
from pathlib import Path

# scripts/python bootstrap: puts py_services (and the repo root) on sys.path.
SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

from _path_setup import bootstrap_python_script_paths  # noqa: E402

bootstrap_python_script_paths(__file__)

from openbook_parser.blocklist import generate_speaker_blocklist


def _resolve_book_root(path: str) -> str:
    p = os.path.abspath(path)
    if os.path.basename(p).startswith("Book-"):
        return p
    return os.path.dirname(p)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a dynamic speaker blocklist from parser vs. gold curations")
    ap.add_argument("book_root", help="Path to Book-X directory or a child chapter path")
    ap.add_argument("--min-count", type=int, default=2, help="Min occurrences of a predicted non-gold speaker to include")
    args = ap.parse_args()

    book_root = _resolve_book_root(args.book_root)
    out = generate_speaker_blocklist(book_root, min_count=args.min_count, quiet=False)
    return 0 if out else 2


if __name__ == "__main__":
    raise SystemExit(main())


