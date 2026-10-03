#!/usr/bin/env python3
"""Offline JEV verification runner for parser experiments.

Parses chapter(s) from a book directory with the standard BookNLP parser,
runs the JEV cross-verification layer over gated lines, and writes a
predictions file consumable by eval_fp_suite.py.

Usage:
  python jev_verify_cli.py <book_root> --out predictions.json
  python jev_verify_cli.py <book_root> --chapter "0011 - ..." --gate carryover
  python jev_verify_cli.py <book_root> --gate none   # baseline: no JEV calls
"""

import argparse
import json
import os
import re
import sys
import time

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from local_dialogue_ai_service import _load_local_env_file  # noqa: E402
from openbook_parser.booknlp_parser_service import BookNLPParserService  # noqa: E402
from openbook_parser.jev_verify_service import (  # noqa: E402
    JevVerifyCache,
    VerifyPolicy,
    load_character_lookup,
    verify_chapter,
)


def _read_text(path: str) -> str:
    # Must match the parser's read (newline=''): dialogue spans live in that
    # coordinate space, and universal-newline CRLF->LF translation shifts
    # every offset after the first line break.
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _iter_chapter_dirs(book_root: str, chapter: str, chapter_regex: str, limit: int):
    matcher = re.compile(chapter_regex) if chapter_regex else None
    items = []
    for name in sorted(os.listdir(book_root)):
        chapter_dir = os.path.join(book_root, name)
        if not os.path.isdir(chapter_dir):
            continue
        if chapter and name != chapter:
            continue
        if matcher and not matcher.search(name):
            continue
        txt_path = os.path.join(chapter_dir, "chapter.txt")
        if not os.path.exists(txt_path):
            continue
        items.append((name, txt_path))
    return items[:limit] if limit > 0 else items


def _serialize_lines(lines) -> list:
    return [
        {
            "id": index + 1,
            "text": line.text,
            "speaker": line.speaker,
            "lineType": line.line_type,
            "isSuggestion": line.is_suggestion,
            "suggestions": list(line.suggestions or []),
            "spanStart": line.span_start,
            "spanEnd": line.span_end,
            "attribution": line.attribution,
        }
        for index, line in enumerate(lines)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the heuristic parser plus JEV cross-verification over a book."
    )
    parser.add_argument("book_root", help="Book directory with <chapter>/chapter.txt folders")
    parser.add_argument("--chapter", default=None, help="Exact chapter folder name (default: all)")
    parser.add_argument("--chapter-regex", default=None, help="Regex filter on chapter folder names")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N chapters")
    parser.add_argument(
        "--gate",
        choices=["none", "carryover", "full"],
        default="carryover",
        help="Risk gate for JEV calls (default: carryover)",
    )
    parser.add_argument("--out", required=True, help="Predictions JSON output path")
    parser.add_argument("--max-calls", type=int, default=60, help="Max JEV calls per chapter")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--downgrade-conf", type=float, default=0.60)
    parser.add_argument("--promote-conf", type=float, default=0.70)
    parser.add_argument("--no-cache", action="store_true", help="Bypass the disk cache")
    args = parser.parse_args()

    book_root = os.path.abspath(args.book_root)
    chapter_items = _iter_chapter_dirs(book_root, args.chapter, args.chapter_regex, args.limit)
    if not chapter_items:
        raise SystemExit("No chapter folders with chapter.txt found")

    _load_local_env_file()
    if args.gate != "none" and not os.getenv("VERCEL_JEV_API_KEY"):
        raise SystemExit("VERCEL_JEV_API_KEY not found (set --gate none or add the key to .env)")
    client = None
    if args.gate != "none":
        from jev_client import JevClient

        client = JevClient(api_key=os.environ["VERCEL_JEV_API_KEY"])

    # budget_seconds=0 = no budget: the offline experiments run to completion.
    policy = VerifyPolicy(
        gate=args.gate,
        max_calls=args.max_calls,
        concurrency=args.concurrency,
        downgrade_conf=args.downgrade_conf,
        promote_conf=args.promote_conf,
        budget_seconds=0,
    )
    cache = JevVerifyCache(enabled=not args.no_cache)
    name_lookup = load_character_lookup(book_root)
    parser_service = BookNLPParserService()

    chapters: dict = {}
    skipped: list = []
    for chapter_name, txt_path in chapter_items:
        print(f"=== {chapter_name} ===", file=sys.stderr)
        started = time.monotonic()
        try:
            lines, _names, _meta = parser_service.parse_file(txt_path, source_path=txt_path)
            chapter_text = _read_text(txt_path)
        except Exception as exc:  # noqa: BLE001 - one unreadable chapter must not kill the book
            skipped.append(chapter_name)
            print(f"  ! skipped (parse failed: {exc})", file=sys.stderr)
            continue
        try:
            summary = verify_chapter(
                lines,
                chapter_text,
                name_lookup,
                policy=policy,
                client=client,
                cache=cache,
                log=lambda message: print(message, file=sys.stderr),
            )
        except Exception as exc:  # noqa: BLE001 - one bad chapter must not kill the book
            skipped.append(chapter_name)
            print(f"  ! skipped (verify failed: {exc})", file=sys.stderr)
            continue
        summary["parseMs"] = int((time.monotonic() - started) * 1000)
        chapters[chapter_name] = {"summary": summary, "lines": _serialize_lines(lines)}
        actions = summary.get("actions", {})
        print(
            f"  targets={summary.get('targets')} calls={summary.get('modelCalls')} "
            f"cacheHits={summary.get('cacheHits')} errors={summary.get('errors')} "
            f"actions={actions}",
            file=sys.stderr,
        )
    if skipped:
        print(f"Skipped {len(skipped)} unreadable chapter(s): {', '.join(skipped)}", file=sys.stderr)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"book": book_root, "gate": args.gate, "chapters": chapters}, handle, ensure_ascii=False)
    print(f"Predictions written: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
