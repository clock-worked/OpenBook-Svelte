"""Create a compact dialogue file containing only line identity and span data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Create a stripped-down dialogue JSON array containing only each "
            "line's id, characterId, and span."
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to the source dialogue.json file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Destination path. Defaults to dialogue.stripped.json beside the "
            "input file."
        ),
    )
    return parser.parse_args()


def load_dialogue(path: Path) -> dict[str, Any]:
    """Load and validate the top-level dialogue payload."""

    try:
        with path.open("r", encoding="utf-8-sig") as source:
            payload = json.load(source)
    except FileNotFoundError as error:
        raise ValueError(f"Input file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {path} at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error

    if not isinstance(payload, dict):
        raise ValueError("The dialogue file must contain a JSON object.")
    if not isinstance(payload.get("lines"), list):
        raise ValueError("The dialogue file must contain a 'lines' array.")
    return payload


def strip_dialogue(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return dialogue lines containing exactly id, characterId, and span."""

    stripped_lines: list[dict[str, Any]] = []
    required_fields = ("id", "characterId", "span")

    for index, line in enumerate(payload["lines"]):
        if not isinstance(line, dict):
            raise ValueError(f"Line at index {index} must be a JSON object.")

        missing_fields = [field for field in required_fields if field not in line]
        if missing_fields:
            missing = ", ".join(missing_fields)
            raise ValueError(f"Line at index {index} is missing: {missing}")

        stripped_lines.append({field: line[field] for field in required_fields})

    return stripped_lines


def default_output_path(input_path: Path) -> Path:
    """Build the default output path beside the input file."""

    return input_path.with_name(f"{input_path.stem}.stripped.json")


def write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    """Write consistently formatted UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Create the stripped dialogue file."""

    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else default_output_path(input_path)
    )

    if input_path == output_path:
        raise SystemExit("Output path must be different from the input path.")

    try:
        payload = load_dialogue(input_path)
        stripped_lines = strip_dialogue(payload)
        write_json(output_path, stripped_lines)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error

    print(f"Wrote {len(stripped_lines)} lines to {output_path}")


if __name__ == "__main__":
    main()
