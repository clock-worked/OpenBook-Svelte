"""Shared loader/writer for the v3 character store (the characters/ folder).

The `characters/` folder is the single source of truth for a book's
characters (invariant IN-2, docs/character_details_alias_design.md).
Every backend reader of character data goes through this module instead
of touching `characters.json` or the folder directly:

- `load_characters(book_root)` -> (records, source)
    `source` is "folder" when `characters/` holds at least one valid
    v3.0 file, otherwise "legacy" (the retired root `characters.json`).
    Records are normalized to the v2-era shape so existing consumers are
    unaffected: v3 files gain `id` (a documented mirror of `guid`) and
    `name` (a mirror of `title`); legacy records pass through unchanged.
- `write_character(book_root, record)` -> filename
    Atomic (tempfile + os.replace) write to `characters/<title>.json`.
    Refuses to materialize a non-GUID (legacy) record into a book that
    still has `characters.json` (IN-2 guard).
- `delete_character_file(book_root, filename)` -> bool (idempotent)

Normative format: docs/schema_characters_v3.md (file format, GUID spec,
filename rules). This module is the Python side's shared implementation
of that GUID spec.
"""

import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

#: Character file format version (independent of the dialogue.json line).
FORMAT_VERSION = "3.0"
FOLDER_NAME = "characters"
LEGACY_FILENAME = "characters.json"

#: Crockford base32, uppercase: 0-9 + A-Z minus I, L, O, U (32 symbols).
CROCKFORD_BASE32_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
#: ^[0-9A-HJKM-NP-QRSTV-Z]{8}$
GUID_PATTERN = re.compile(r"^[0-9A-HJKM-NP-QRSTV-Z]{8}$")


class CharacterStoreError(Exception):
    """Raised when a character store operation is refused or invalid."""


# ---------------------------------------------------------------------------
# GUID helpers (normative: docs/schema_characters_v3.md, section "GUID spec")
# ---------------------------------------------------------------------------


def is_valid_guid(value: Any) -> bool:
    """True when value is an 8-char uppercase Crockford base32 GUID."""
    return isinstance(value, str) and bool(GUID_PATTERN.fullmatch(value))


def guid_from_bytes(raw: bytes) -> str:
    """Encode exactly 5 big-endian bytes (40 bits) as an 8-char GUID."""
    if len(raw) != 5:
        raise ValueError(f"GUID requires exactly 5 bytes, got {len(raw)}")
    value = int.from_bytes(raw, "big")
    chars = []
    for _ in range(8):
        value, digit = divmod(value, 32)
        chars.append(CROCKFORD_BASE32_ALPHABET[digit])
    return "".join(reversed(chars))


def mint_guid() -> str:
    """Mint a fresh random GUID (5 random bytes -> 40 bits -> 8 chars)."""
    return guid_from_bytes(secrets.token_bytes(5))


# ---------------------------------------------------------------------------
# Filename rules (normative: docs/schema_characters_v3.md, "Filename rules")
# ---------------------------------------------------------------------------

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_CONSECUTIVE_DASHES = re.compile(r"-{2,}")
_WINDOWS_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
MAX_FILENAME_CHARS = 80


def sanitize_filename(title: str) -> str:
    """Derive a safe `characters/` filename stem from a character title.

    Replaces `\\/:*?"<>|` and control chars with `-`, collapses repeats,
    trims, caps at 80 chars, and guards Windows reserved names with a
    `-` prefix. Case-collision -> GUID-fallback naming is the
    migration's job, not the loader's.
    """
    text = _INVALID_FILENAME_CHARS.sub("-", str(title or ""))
    text = _CONSECUTIVE_DASHES.sub("-", text).strip().strip("-")
    text = text[:MAX_FILENAME_CHARS].rstrip("-")
    if not text:
        return "-"
    if text.upper() in _WINDOWS_RESERVED_NAMES:
        return f"-{text}"
    return text


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _is_valid_v3_record(record: Dict[str, Any]) -> bool:
    """Validation rules 1/5 subset: v3.0, valid GUID, non-empty title."""
    if str(record.get("formatVersion") or "") != FORMAT_VERSION:
        return False
    if not is_valid_guid(record.get("guid")):
        return False
    return bool(str(record.get("title") or "").strip())


def load_character_folder(book_root: Union[str, Path]) -> List[Dict[str, Any]]:
    """Read the v3 `characters/` folder: valid v3.0 records, filename order.

    Invalid files (unparseable, wrong formatVersion, bad/missing GUID,
    empty title) are skipped with a warning. The folder counts as the
    source of truth only when at least one valid file is present.
    """
    folder = Path(book_root) / FOLDER_NAME
    if not folder.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"  Warning: skipping unparseable character file: {path.name}")
            continue
        if not isinstance(raw, dict) or not _is_valid_v3_record(raw):
            print(f"  Warning: skipping invalid v3 character file: {path.name}")
            continue
        record = dict(raw)
        record["id"] = str(raw["guid"])  # documented mirror: id = guid
        record["name"] = str(raw["title"] or "").strip()
        records.append(record)
    return records


def load_legacy_document(book_root: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """Return the raw root `characters.json` document, or None."""
    path = Path(book_root) / LEGACY_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_characters(book_root: Union[str, Path]) -> Tuple[List[Dict[str, Any]], str]:
    """Load a book's characters from the folder or the legacy file.

    Returns (records, source) with source in {"folder", "legacy"}. The
    folder wins when it holds at least one valid v3.0 file; otherwise the
    retired root `characters.json` is used. A book with neither returns
    empty records with source "legacy", preserving current behavior.
    Records are normalized to the v2-era shape (id/name), so existing
    consumers keep their view.
    """
    root = Path(book_root)
    if not root.is_dir():
        return [], "legacy"
    folder_records = load_character_folder(root)
    if folder_records:
        return folder_records, "folder"
    document = load_legacy_document(root)
    if document is None:
        return [], "legacy"
    characters = document.get("characters")
    if not isinstance(characters, list):
        return [], "legacy"
    return [character for character in characters if isinstance(character, dict)], "legacy"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """Atomic write: temp file in the same directory + os.replace."""
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        temporary_path = handle.name
    os.replace(temporary_path, path)


def write_character(book_root: Union[str, Path], record: Dict[str, Any]) -> str:
    """Atomically write one character to `characters/<sanitized-title>.json`.

    Returns the filename (e.g. "Catherine.json"). IN-2 guard: refuses
    (raises CharacterStoreError, nothing is written) when the book still
    has a root `characters.json` and the record is not a v3 record (no
    valid guid) - no writer may create a v3 file into a not-yet-migrated
    book outside the migration path. A record with a valid guid may be
    written even while `characters.json` is still present (the
    migration window).
    """
    root = Path(book_root)
    title = str(record.get("title") or record.get("name") or "").strip()
    if not title:
        raise CharacterStoreError("Character record has no title")
    guid = record.get("guid")
    if not is_valid_guid(guid):
        if (root / LEGACY_FILENAME).is_file():
            raise CharacterStoreError(
                "Refusing to write a v3 character file: root characters.json "
                "still exists and the record has no valid guid "
                "(book not migrated; IN-2)"
            )
        raise CharacterStoreError(
            "Refusing to write a v3 character file: record has no valid guid"
        )
    folder = root / FOLDER_NAME
    folder.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    payload["guid"] = str(guid)
    payload["title"] = title
    payload.setdefault("formatVersion", FORMAT_VERSION)
    payload["updatedAt"] = datetime.now(timezone.utc).isoformat()
    filename = f"{sanitize_filename(title)}.json"
    _write_json_atomic(folder / filename, payload)
    return filename


def delete_character_file(book_root: Union[str, Path], filename: str) -> bool:
    """Delete `characters/<filename>`; idempotent (False when absent)."""
    name = str(filename or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise CharacterStoreError(f"Invalid character filename: {filename!r}")
    path = Path(book_root) / FOLDER_NAME / name
    if not path.is_file():
        return False
    path.unlink()
    return True
