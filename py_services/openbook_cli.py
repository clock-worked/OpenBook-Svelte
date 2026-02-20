#!/usr/bin/env python3
"""
CLI entrypoint for OpenBook parser (Svelte/Tauri).

Usage:
  python openbook_cli.py parse --input <path>

Outputs a single JSON string to stdout with keys:
  - script: list of dialogue/narration line dicts
  - characters: list of unique character names
  - meta: version info
"""

import argparse
import json
import os
import sys

# Ensure local package import works when called by Tauri
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Import from the package
from openbook_parser.dialogue_parser_service import DialogueParserService, script_to_dict_list
from openbook_parser.knowledge_store import KnowledgeStore
from openbook_parser.blocklist import generate_speaker_blocklist


def _maybe_generate_blocklist_for_input(input_path: str) -> None:
    # Resolve Book-X root using the knowledge store utility
    book_root = KnowledgeStore.find_book_root_from_file(input_path)
    if not book_root:
        return
    # Only generate if we don't already have a blocklist
    import os
    bl_path = os.path.join(book_root, "speaker_blocklist.json")
    if os.path.exists(bl_path):
        return
    # Attempt generation; if no gold exists, this is a no-op
    generate_speaker_blocklist(book_root, min_count=2, quiet=True)


def cmd_parse(input_path: str, manual_blocklist: list = None, parser_options: dict = None):
    # Preflight: try to generate blocklist for the book if possible
    _maybe_generate_blocklist_for_input(input_path)
    parser_service = DialogueParserService()
    
    # Add manual blocklist to the parser service if provided
    if manual_blocklist:
        parser_service.blocked_speakers.update(manual_blocklist)
    
    script_lines, character_names = parser_service.parse_file(input_path, options=parser_options or {})
    result = {
        "script": script_to_dict_list(script_lines),
        "characters": character_names,
        "meta": {"version": "1.0.0"}
    }
    print(json.dumps(result, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(prog="openbook_cli", description="OpenBook Parser CLI")
    sub = ap.add_subparsers(dest="command")

    p_parse = sub.add_parser("parse", help="Parse a .txt file and emit JSON")
    p_parse.add_argument("--input", required=True, help="Path to input .txt file")
    p_parse.add_argument("--manual-blocklist", nargs="*", default=[], help="Manual blocklist of speaker names to exclude")
    p_parse.add_argument("--parser-options", default=None, help="JSON string for parser options")

    args = ap.parse_args()
    if args.command == "parse":
        if not os.path.exists(args.input):
            print(json.dumps({"error": f"Input file not found: {args.input}"}), file=sys.stderr)
            return 2
        try:
            parser_options = None
            if args.parser_options:
                parser_options = json.loads(args.parser_options)
            cmd_parse(args.input, args.manual_blocklist, parser_options)
            return 0
        except Exception as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            return 1
    else:
        ap.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())


