"""Migrate a book's character store to the v3.0 per-character folder.

Implements the same five-stage algorithm as the FE auto-on-open driver in
apps/desktop/src/lib/services/characterMigration.ts (normative:
docs/schema_characters_v3.md §Migration + §GUID spec, and
docs/character_details_alias_design.md §Migration):

  0. Idempotency gate (read-only): characters/ WITH FILES AND the legacy
     store present -> abort (crashed prior run / in-flight; never guess —
     checked FIRST, a partial folder may already hold valid files);
     characters/ with >=1 valid v3.0 file and unique GUIDs -> no-op.
  1. Inventory (read-only): parse the legacy store (utf-8-sig), validate
     every record (id + name, case-insensitively unique ids, no ambiguous
     name/alias, no case-insensitive filename collision), walk every
     reference family: dialogue (lines[].characterId,
     lines[].candidates[].characterId,
     lines[].attribution.candidates[].characterId,
     stats.characterBreakdown keys), rosters (<Ch>/<Ch>.characters.json),
     voices.json (assignments + usedByCharacters), audio manifests
     (audio_lines/**/manifest.json, top-level + per-clip characterId,
     plus the dialogue-style lines[].characterId carried by chapter-level
     TTS-pipeline manifests).
  2. Deterministic minting: SHA-256("<book-name>::v3::<v2-id>") -> first 5
     bytes -> 8-char Crockford base32 (shared table in
     py_services/character_store.py). Mint order: clusters sorted by
     firstAppearance (nulls last) then title.
  3. Validation gates: any hit -> abort, zero writes.
  4. Backup: _backups/character-v3/<UTC ts>/ mirrors the relative path of
     every file that will change (shutil.copy2, byte-identical).
  5. Ordered apply (crash-safe): (a) write all characters/<Title>.json ->
     (b) remap + atomically write every touched reference file (exact
     old-id -> GUID substitution at every inventoried site) -> (c) LAST,
     delete the legacy store -> (d) post-apply rescan asserting zero
     surviving v2 slugs across all inventoried reference families.

The default is a validation-only dry run; --apply runs stages 4-5.

v2 books: root characters.json is the source. v1 books (only
book.characters.json, name-keyed, m/f/u genders; ruling R11) synthesize v2
records in memory (slug ids, bijective gender mapping, aliases/color
carried, zero stats) and run the identical pipeline; the v1 store is
retired (deleted) last. Chapters with only a v1 script.json are left
name-based (the format has no characterId slot) and flagged in the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

from _path_setup import bootstrap_python_script_paths  # noqa: E402

bootstrap_python_script_paths(__file__)

from py_services.character_store import (  # noqa: E402
    FORMAT_VERSION,
    guid_from_bytes,
    is_valid_guid,
    sanitize_filename,
    write_character,
)


LEGACY_V2_FILENAME = "characters.json"
LEGACY_V1_FILENAME = "book.characters.json"
NARRATOR_SENTINEL = "narrator"
V1_GENDER_MAP = {"m": "Male", "f": "Female", "u": "Unknown"}


class MigrationAbort(ValueError):
    """A validation/apply failure: the migration stops, zero (or partial) writes."""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate a book's character store (characters.json v2, or the v1 "
            "book.characters.json) to the v3.0 per-character characters/ folder. "
            "The default is a validation-only dry run."
        )
    )
    parser.add_argument(
        "book_root",
        type=Path,
        help="Book directory containing characters.json (v2) or book.characters.json (v1).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up and write all validated changes (stages 4-5).",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help=(
            "Backup destination used with --apply. Defaults to a timestamped "
            "directory beneath <book_root>/_backups/character-v3."
        ),
    )
    parser.add_argument(
        "--drop-unresolved",
        action="store_true",
        help=(
            "Drop legacy roster entries that cannot be resolved to a central "
            "character. Without this flag, unresolvable references are left "
            "in place, unremapped (the migration itself never aborts on them)."
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Shared helpers (mirror the FE module byte-for-byte)
# ---------------------------------------------------------------------------


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            return json.load(source)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {path} at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error


def load_inventoried(path: Path) -> Any:
    """Read an inventoried file: None when absent; MigrationAbort on a JSON
    parse failure (a validation gate — never a silent skip)."""
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except ValueError as error:
        raise MigrationAbort(f"JSON parse failure in {path}: {error}") from error


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def lookup_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def id_candidates(value: str) -> list[str]:
    """Conservative legacy-name variants that may already be central IDs
    (identical to migrate_chapter_character_ids.py)."""
    slug = re.sub(r"[^a-z0-9\s-]", "", value.casefold())
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    if not slug:
        return []
    candidates = [slug, f"the-{slug}"]
    if slug.endswith("s") and len(slug) > 1:
        singular = slug[:-1]
        candidates.extend([singular, f"the-{singular}"])
    return candidates


def make_resolver(
    ids: dict[str, str], names: dict[str, str]
) -> Callable[[str], str | None]:
    """Resolve a reference value: exact id (case-insensitive) first, then
    name/alias, then slug candidates."""

    def resolve(value: str) -> str | None:
        key = lookup_key(value)
        if not key:
            return None
        if key in ids:
            return ids[key]
        if key in names:
            return names[key]
        for candidate in id_candidates(value):
            resolved = ids.get(lookup_key(candidate))
            if resolved:
                return resolved
        return None

    return resolve


def as_number(value: Any, fallback: Any) -> Any:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return value
    return fallback


# ---------------------------------------------------------------------------
# Stage 0: idempotency gate (read-only)
# ---------------------------------------------------------------------------


def idempotency_gate(book_root: Path, legacy_path: Path) -> str:
    folder = book_root / "characters"
    file_names = sorted(p.name for p in folder.glob("*.json")) if folder.is_dir() else []
    valid_guids: list[str] = []
    for name in file_names:
        try:
            with (folder / name).open("r", encoding="utf-8-sig") as source:
                doc = json.load(source)
        except (OSError, ValueError):
            continue
        if (
            isinstance(doc, dict)
            and doc.get("formatVersion") == FORMAT_VERSION
            and is_valid_guid(doc.get("guid"))
        ):
            valid_guids.append(doc["guid"])
    # In-flight FIRST: a folder with any files alongside the legacy store is
    # a crashed prior run (a partial folder may already hold valid files) —
    # never guess, never auto-adopt it.
    if file_names and legacy_path.is_file():
        return "in-flight"
    if valid_guids and len(set(valid_guids)) == len(valid_guids):
        return "already-migrated"
    return "proceed"


# ---------------------------------------------------------------------------
# v1 -> synthesized v2 records (ruling R11)
# ---------------------------------------------------------------------------


def synthesize_v2_records(entries: list, source_name: str) -> list[dict]:
    records: list[dict] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        name = clean_text(entry.get("name"))
        if not name:
            raise MigrationAbort(f"{source_name} entry {index} has no name")
        candidates = id_candidates(name)
        if not candidates:
            raise MigrationAbort(f"Cannot derive a slug id for character '{name}'")
        gender_raw = str(entry.get("gender") or "u").strip().lower()
        records.append(
            {
                "id": candidates[0],
                "name": name,
                "gender": V1_GENDER_MAP.get(gender_raw, "Unknown"),
                "aliases": [a for a in (entry.get("aliases") or []) if isinstance(a, str)],
                "color": entry.get("color"),
                "voice": entry.get("voice"),
                "firstAppearance": clean_text(entry.get("first_seen_chapter")),
                "notes": "",
                "stats": {"totalLines": 0, "chapterCount": 0},
            }
        )
    return records


# ---------------------------------------------------------------------------
# Record validation (stage 1/3)
# ---------------------------------------------------------------------------


def validate_records(records: list[dict]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    ids: dict[str, str] = {}
    names: dict[str, str] = {}
    ambiguous: dict[str, set[str]] = {}
    issues: list[str] = []

    for index, record in enumerate(records):
        character_id = clean_text(record.get("id"))
        name = clean_text(record.get("name"))
        if not character_id or not name:
            issues.append(f"record {index} must have non-empty id and name")
            continue
        id_key = lookup_key(character_id)
        existing = ids.get(id_key)
        if existing and existing != character_id:
            issues.append(f"Duplicate character ID (case-insensitive): {character_id}")
        ids[id_key] = character_id

        for surface in [name, *(record.get("aliases") or [])]:
            text = clean_text(surface)
            if not text:
                continue
            key = lookup_key(text)
            previous = names.get(key)
            if previous and previous != character_id:
                ambiguous.setdefault(key, {previous}).add(character_id)
            elif not previous or previous == character_id:
                names[key] = character_id

    if ambiguous:
        details = "; ".join(
            f"{name!r} -> {', '.join(sorted(character_ids))}"
            for name, character_ids in sorted(ambiguous.items())
        )
        issues.append(f"Ambiguous central character names/aliases: {details}")

    # Case-insensitive filename collision (also catches duplicate titles,
    # including case/whitespace variants) -> abort naming the pair.
    by_stem: dict[str, list[str]] = {}
    for record in records:
        name = clean_text(record.get("name"))
        if not name:
            continue
        stem = sanitize_filename(name)
        by_stem.setdefault(lookup_key(stem), []).append(name)
    for stem, titles in sorted(by_stem.items()):
        if len(titles) > 1:
            issues.append(
                f"Filename collision: {', '.join(repr(t) for t in titles)} "
                f"-> characters/{stem}.json (case-insensitive)"
            )

    return ids, names, issues


# ---------------------------------------------------------------------------
# Stage 2: deterministic GUID minting
# ---------------------------------------------------------------------------


def sort_clusters_for_minting(records: list[dict]) -> list[dict]:
    """Mint order: firstAppearance (nulls last) then title (casefold)."""

    def key(record: dict) -> tuple:
        first = record.get("firstAppearance")
        first = first if isinstance(first, str) else None
        return (first is None, first or "", str(record.get("name") or "").casefold())

    return sorted(records, key=key)


def mint_guids(book_root: Path, ordered_records: list[dict]) -> tuple[dict[str, str], str | None]:
    """SHA-256("<book-name>::v3::<v2-id>") -> first 5 bytes -> 8-char Crockford.
    Returns (remap, collision-guid-or-None); a collision is a validation abort."""
    remap: dict[str, str] = {}
    taken: set[str] = set()
    for record in ordered_records:
        v2_id = clean_text(record.get("id"))
        if not v2_id:
            continue  # validation reports this separately
        digest = hashlib.sha256(f"{book_root.name}::v3::{v2_id}".encode("utf-8")).digest()
        guid = guid_from_bytes(digest[:5])
        if guid in taken:
            return remap, guid
        taken.add(guid)
        remap[v2_id] = guid
    return remap, None


# ---------------------------------------------------------------------------
# Reference-family remap walks (exact old-id -> GUID substitution)
# ---------------------------------------------------------------------------

Resolver = Callable[[str], str | None]


def remap_field(
    container: dict, field_name: str, resolve: Resolver, remap: dict[str, str]
) -> tuple[bool, str | None]:
    value = container.get(field_name)
    if not isinstance(value, str):
        return False, None
    text = value.strip()
    if not text or text == NARRATOR_SENTINEL:
        return False, None
    resolved = resolve(text)
    if resolved is None:
        return False, text
    guid = remap[resolved]
    if container[field_name] == guid:
        return False, None
    container[field_name] = guid
    return True, None


def walk_dialogue(payload: Any, resolve: Resolver, remap: dict[str, str]) -> tuple[bool, int, list[str]]:
    changed = False
    refs = 0
    unresolved: list[str] = []

    lines = payload.get("lines") if isinstance(payload, dict) else None
    if not isinstance(lines, list):
        lines = []
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        did, bad = remap_field(line, "characterId", resolve, remap)
        if did:
            changed, refs = True, refs + 1
        elif bad:
            unresolved.append(f"lines[{index}].characterId '{bad}'")
        for cindex, candidate in enumerate(line.get("candidates") or []):
            if not isinstance(candidate, dict):
                continue
            did, bad = remap_field(candidate, "characterId", resolve, remap)
            if did:
                changed, refs = True, refs + 1
            elif bad:
                unresolved.append(f"lines[{index}].candidates[{cindex}].characterId '{bad}'")
        attribution = line.get("attribution")
        if isinstance(attribution, dict):
            for cindex, candidate in enumerate(attribution.get("candidates") or []):
                if not isinstance(candidate, dict):
                    continue
                did, bad = remap_field(candidate, "characterId", resolve, remap)
                if did:
                    changed, refs = True, refs + 1
                elif bad:
                    unresolved.append(
                        f"lines[{index}].attribution.candidates[{cindex}].characterId '{bad}'"
                    )

    stats = payload.get("stats") if isinstance(payload, dict) else None
    if isinstance(stats, dict) and isinstance(stats.get("characterBreakdown"), dict):
        breakdown = stats["characterBreakdown"]
        next_breakdown: dict[Any, Any] = {}
        for key, count in breakdown.items():
            if not isinstance(key, str) or not key or key == NARRATOR_SENTINEL:
                next_breakdown[key] = count
                continue
            resolved = resolve(key)
            if resolved is None:
                unresolved.append(f"stats.characterBreakdown key '{key}'")
                next_breakdown[key] = count
                continue
            guid = remap[resolved]
            previous = next_breakdown.get(guid)
            next_breakdown[guid] = (
                previous if isinstance(previous, (int, float)) and not isinstance(previous, bool) else 0
            ) + (count if isinstance(count, (int, float)) and not isinstance(count, bool) else 0)
            changed = True
            refs += 1
        stats["characterBreakdown"] = next_breakdown

    return changed, refs, unresolved


def walk_roster(
    payload: Any,
    resolve: Resolver,
    remap: dict[str, str],
    drop_unresolved: bool = False,
) -> tuple[bool, int, list[str]]:
    """Roster entries become [{ "id": "<GUID>" }]; the file formatVersion -> "3.0".

    Unresolvable entries are left in place (unremapped) by default; with
    ``drop_unresolved`` they are omitted from the rewritten roster.
    """
    entries = payload.get("characters") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        entries = []
    next_entries: list[Any] = []
    seen: set[str] = set()
    changed = False
    refs = 0
    unresolved: list[str] = []

    for index, entry in enumerate(entries):
        surface: str | None = None
        if isinstance(entry, str):
            surface = entry
        elif isinstance(entry, dict):
            surface = clean_text(entry.get("id")) or clean_text(entry.get("name"))
        if not surface:
            unresolved.append(f"entry {index}: {entry!r}")
            if not drop_unresolved:
                next_entries.append(entry)  # left in place, unremapped
            continue
        resolved = resolve(surface)
        if resolved is None:
            unresolved.append(f"entry {index}: {entry!r}")
            if not drop_unresolved:
                next_entries.append(entry)  # left in place, unremapped
            continue
        key = lookup_key(remap[resolved])
        if key in seen:
            continue  # dedupe (house behavior of the legacy roster migrator)
        seen.add(key)
        next_entries.append({"id": remap[resolved]})
        refs += 1
        changed = True

    if changed:
        payload["characters"] = next_entries
        payload["formatVersion"] = FORMAT_VERSION
    return changed, refs, unresolved


def walk_voices(payload: Any, resolve: Resolver, remap: dict[str, str]) -> tuple[bool, int, list[str]]:
    changed = False
    refs = 0
    unresolved: list[str] = []

    assignments = payload.get("assignments") if isinstance(payload, dict) else None
    if not isinstance(assignments, list):
        assignments = []
    for index, assignment in enumerate(assignments):
        if not isinstance(assignment, dict):
            continue
        did, bad = remap_field(assignment, "characterId", resolve, remap)
        if did:
            changed, refs = True, refs + 1
        elif bad:
            unresolved.append(f"assignments[{index}].characterId '{bad}'")

    voices = payload.get("voices") if isinstance(payload, dict) else None
    if not isinstance(voices, list):
        voices = []
    for index, voice in enumerate(voices):
        metadata = voice.get("metadata") if isinstance(voice, dict) else None
        if not isinstance(metadata, dict) or not isinstance(metadata.get("usedByCharacters"), list):
            continue
        remapped: list[Any] = []
        for jindex, value in enumerate(metadata["usedByCharacters"]):
            if not isinstance(value, str):
                remapped.append(value)
                continue
            text = value.strip()
            if not text or text == NARRATOR_SENTINEL:
                remapped.append(value)
                continue
            resolved = resolve(text)
            if resolved is None:
                unresolved.append(f"voices[{index}].metadata.usedByCharacters[{jindex}] '{text}'")
                remapped.append(value)
                continue
            remapped.append(remap[resolved])
            changed = True
            refs += 1
        metadata["usedByCharacters"] = remapped

    return changed, refs, unresolved


def walk_manifest(payload: Any, resolve: Resolver, remap: dict[str, str]) -> tuple[bool, int, list[str]]:
    """Top-level + per-clip characterId. characterName stays a name (R8).

    Chapter-level TTS-pipeline manifests also carry a dialogue-style
    `lines` array; build_plan pairs this walk with walk_dialogue on the
    same payload so those sites are remapped too (walker/rescan symmetry
    with the stage-5d rescan, which polices `lines` in every inventoried
    file)."""
    changed = False
    refs = 0
    unresolved: list[str] = []
    if not isinstance(payload, dict):
        return changed, refs, unresolved

    did, bad = remap_field(payload, "characterId", resolve, remap)
    if did:
        changed, refs = True, refs + 1
    elif bad:
        unresolved.append(f"characterId '{bad}'")

    clips = payload.get("clips")
    if not isinstance(clips, list):
        clips = []
    for index, clip in enumerate(clips):
        if not isinstance(clip, dict):
            continue
        did, bad = remap_field(clip, "characterId", resolve, remap)
        if did:
            changed, refs = True, refs + 1
        elif bad:
            unresolved.append(f"clips[{index}].characterId '{bad}'")

    return changed, refs, unresolved


# ---------------------------------------------------------------------------
# v3 file shape (v2 -> v3 field mapping, docs/schema_characters_v3.md)
# ---------------------------------------------------------------------------

def build_v3_record(record: dict, guid: str, now: str) -> dict:
    """v2 record -> v3.0 file payload in canonical field order
    (docs/schema_characters_v3.md §File format). Drops the v2 `id`;
    legacy count/chapterCount fold into stats (stats wins); unknown
    fields round-trip verbatim (never lose data)."""
    stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
    payload: dict[str, Any] = {
        "formatVersion": FORMAT_VERSION,
        "guid": guid,
        "title": clean_text(record.get("name")),
        "gender": record.get("gender") if isinstance(record.get("gender"), str) else "Unknown",
        "aliases": [a for a in (record.get("aliases") or []) if isinstance(a, str)],
        "descriptors": [d for d in (record.get("descriptors") or []) if isinstance(d, str)],
        "race": record.get("race"),
        "color": record.get("color"),
        "notes": record.get("notes") if isinstance(record.get("notes"), str) else "",
        "firstAppearance": record.get("firstAppearance"),
        "stats": {
            "totalLines": as_number(stats.get("totalLines"), as_number(record.get("count"), 0)),
            "chapterCount": as_number(stats.get("chapterCount"), as_number(record.get("chapterCount"), 0)),
        },
        "voice": record.get("voice"),
        "provider": record.get("provider"),
        "voiceId": record.get("voiceId"),
        "voiceMeta": record.get("voiceMeta"),
        "manifestStats": record.get("manifestStats"),
        "roleLabels": [r for r in (record.get("roleLabels") or []) if isinstance(r, str)],
        "updatedAt": now,
    }
    for key, value in record.items():
        if key == "id" or key in payload:
            continue
        payload[key] = value
    return payload


# ---------------------------------------------------------------------------
# Stage 1: discovery + reference inventory
# ---------------------------------------------------------------------------


def discover_chapters(book_root: Path) -> list[Path]:
    return sorted(
        path
        for path in book_root.iterdir()
        if path.is_dir() and not path.name.startswith((".", "_")) and path.name != "characters"
    )


def discover_manifests(book_root: Path) -> list[Path]:
    """Every manifest.json beneath an audio_lines directory (recursive)."""
    return sorted(
        path
        for path in book_root.glob("**/manifest.json")
        if path.is_file() and "audio_lines" in path.parts
    )


@dataclass
class MigrationPlan:
    mode: str  # "v2" | "v1"
    clusters: list[tuple[dict, str, str]] = field(default_factory=list)  # (v2 record, guid, filename), mint order
    remap: dict[str, str] = field(default_factory=dict)  # v2 id -> guid
    ref_changes: dict[Path, dict] = field(default_factory=dict)  # path -> new payload (touched only)
    inventory: set[Path] = field(default_factory=set)  # every inventoried reference file
    counts: dict[str, int] = field(
        default_factory=lambda: {"dialogueRefs": 0, "rosterRefs": 0, "voiceRefs": 0, "manifestRefs": 0}
    )
    violations: list[str] = field(default_factory=list)
    unresolved_refs: list[str] = field(default_factory=list)  # left in place, unremapped
    resolve: Resolver | None = field(default=None, compare=False)  # for the stage-5d site-aware rescan
    v1_script_only: list[str] = field(default_factory=list)
    noop: bool = False


def build_plan(
    book_root: Path,
    mode: str,
    legacy_path: Path,
    drop_unresolved: bool,
) -> MigrationPlan:
    plan = MigrationPlan(mode=mode)

    # Stage 1a: parse + validate the legacy store.
    doc = load_inventoried(legacy_path)
    if not isinstance(doc, dict) or not isinstance(doc.get("characters"), list):
        raise MigrationAbort(f"{legacy_path.name}: expected a 'characters' array")
    entries = doc["characters"]
    if mode == "v1":
        records = synthesize_v2_records(entries, legacy_path.name)
    else:
        records = [entry for entry in entries if isinstance(entry, dict)]
    if not records:
        plan.noop = True
        return plan

    ids, names, issues = validate_records(records)
    plan.violations.extend(issues)

    # Stage 2: deterministic minting (in mint order).
    ordered = sort_clusters_for_minting(records)
    remap, collision = mint_guids(book_root, ordered)
    if collision:
        plan.violations.append(f"Minted GUID collision: {collision}")
        plan.remap = remap
        return plan
    plan.remap = remap
    plan.clusters = [
        (record, remap[clean_text(record.get("id"))], f"{sanitize_filename(clean_text(record.get('name')))}.json")
        for record in ordered
        if clean_text(record.get("id")) in remap and clean_text(record.get("name"))
    ]

    # Stage 1b: walk every reference family.
    resolve = make_resolver(ids, names)
    plan.resolve = resolve

    for chapter in discover_chapters(book_root):
        # <Ch>/dialogue.json (four reference sites)
        dialogue_path = chapter / "dialogue.json"
        dialogue = load_inventoried(dialogue_path)
        if dialogue is not None:
            plan.inventory.add(dialogue_path)
            copy = deepcopy(dialogue)
            changed, refs, unresolved = walk_dialogue(copy, resolve, remap)
            plan.counts["dialogueRefs"] += refs
            plan.unresolved_refs.extend(f"{dialogue_path.relative_to(book_root)}: {site}" for site in unresolved)
            if changed:
                plan.ref_changes[dialogue_path] = copy
        elif (chapter / f"{chapter.name}.script.json").is_file():
            # R11: v1-script chapters have no characterId slot; they stay
            # name-based and are flagged (never silently remapped).
            plan.v1_script_only.append(chapter.name)

        # <Ch>/<Ch>.characters.json (roster)
        roster_path = chapter / f"{chapter.name}.characters.json"
        roster = load_inventoried(roster_path)
        if roster is not None:
            plan.inventory.add(roster_path)
            copy = deepcopy(roster)
            changed, refs, unresolved = walk_roster(copy, resolve, remap, drop_unresolved)
            plan.counts["rosterRefs"] += refs
            plan.unresolved_refs.extend(
                f"{roster_path.relative_to(book_root)}: {site}" for site in unresolved
            )
            if changed:
                plan.ref_changes[roster_path] = copy

    # voices.json
    voices_path = book_root / "voices.json"
    voices = load_inventoried(voices_path)
    if voices is not None:
        plan.inventory.add(voices_path)
        copy = deepcopy(voices)
        changed, refs, unresolved = walk_voices(copy, resolve, remap)
        plan.counts["voiceRefs"] += refs
        plan.unresolved_refs.extend(f"voices.json: {site}" for site in unresolved)
        if changed:
            plan.ref_changes[voices_path] = copy

    # audio_lines/**/manifest.json
    for manifest_path in discover_manifests(book_root):
        manifest = load_inventoried(manifest_path)
        if manifest is None:
            continue
        plan.inventory.add(manifest_path)
        copy = deepcopy(manifest)
        changed, refs, unresolved = walk_manifest(copy, resolve, remap)
        # Chapter TTS-pipeline manifests (audio_lines/manifest.json) embed a
        # dialogue-style `lines` array; the stage-5d rescan polices those
        # sites, so the walker must remap them too (walker/rescan symmetry).
        # A plain clip manifest has no `lines` key -> this is a no-op.
        dchanged, drefs, dunresolved = walk_dialogue(copy, resolve, remap)
        if dchanged:
            changed = True
        refs += drefs
        unresolved.extend(dunresolved)
        plan.counts["manifestRefs"] += refs
        plan.unresolved_refs.extend(
            f"{manifest_path.relative_to(book_root)}: {site}" for site in unresolved
        )
        if changed:
            plan.ref_changes[manifest_path] = copy

    # Unresolvable references never abort: they are left in place,
    # unremapped (see walk_roster for the --drop-unresolved variant).

    return plan


# ---------------------------------------------------------------------------
# Stage 4: backup
# ---------------------------------------------------------------------------


def default_backup_dir(book_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return book_root / "_backups" / "character-v3" / timestamp


def make_backup(book_root: Path, plan: MigrationPlan, legacy_path: Path, backup_dir: Path) -> None:
    """Mirror the relative path of every file that will change
    (byte-identical pre-images; new character files have no pre-image)."""
    for path in [legacy_path, *sorted(plan.ref_changes)]:
        if not path.is_file():
            continue
        target = backup_dir / path.relative_to(book_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


# ---------------------------------------------------------------------------
# Stage 5: ordered apply + post-apply rescan
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, payload: Any) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _stale_site_value(value: Any, resolve: Resolver, remap: dict[str, str]) -> bool:
    """True when a reference-site value still carries a RESOLVABLE v2 value
    (the walker should have remapped it). Unresolvable values — left in place
    by design — the narrator sentinel (left literal by the walkers) and plain
    text content are out of scope."""
    if not isinstance(value, str) or not value:
        return False
    text = clean_text(value)
    if not text or text == NARRATOR_SENTINEL:
        return False
    rid = resolve(text)
    return rid is not None and value != remap[rid]


def _payload_has_stale_site(payload: Any, resolve: Resolver, remap: dict[str, str]) -> bool:
    """Walks exactly the inventoried reference sites (the same sites the
    walkers remap): dialogue lines/candidates/attribution +
    stats.characterBreakdown keys, roster entries, voices assignments +
    usedByCharacters, manifest top-level + per-clip characterId. A dialogue
    line whose TEXT merely contains a slug word is NOT a stale site."""
    if not isinstance(payload, dict):
        return False
    for line in payload.get("lines") or []:
        if not isinstance(line, dict):
            continue
        if _stale_site_value(line.get("characterId"), resolve, remap):
            return True
        for cand in line.get("candidates") or []:
            if isinstance(cand, dict) and _stale_site_value(cand.get("characterId"), resolve, remap):
                return True
        attribution = line.get("attribution")
        if isinstance(attribution, dict):
            for cand in attribution.get("candidates") or []:
                if isinstance(cand, dict) and _stale_site_value(cand.get("characterId"), resolve, remap):
                    return True
    stats = payload.get("stats")
    breakdown = stats.get("characterBreakdown") if isinstance(stats, dict) else None
    if isinstance(breakdown, dict):
        for key in breakdown:
            if _stale_site_value(key, resolve, remap):
                return True
    for entry in payload.get("characters") or []:
        if isinstance(entry, str):
            if _stale_site_value(entry, resolve, remap):
                return True
        elif isinstance(entry, dict):
            if _stale_site_value(entry.get("id"), resolve, remap) or _stale_site_value(entry.get("name"), resolve, remap):
                return True
    for assignment in payload.get("assignments") or []:
        if isinstance(assignment, dict) and _stale_site_value(assignment.get("characterId"), resolve, remap):
            return True
    for voice in payload.get("voices") or []:
        if not isinstance(voice, dict):
            continue
        metadata = voice.get("metadata")
        if isinstance(metadata, dict):
            for name in metadata.get("usedByCharacters") or []:
                if _stale_site_value(name, resolve, remap):
                    return True
    if _stale_site_value(payload.get("characterId"), resolve, remap):
        return True
    for clip in payload.get("clips") or []:
        if isinstance(clip, dict) and _stale_site_value(clip.get("characterId"), resolve, remap):
            return True
    return False


def rescan_for_stale_ids(book_root: Path, plan: MigrationPlan) -> list[str]:
    """Stage 5d: assert that no inventoried REFERENCE SITE still carries a
    resolvable v2 value. Ordinary text (line bodies, notes) and
    unresolvable values (left in place by design) are out of scope."""
    resolve = plan.resolve
    if resolve is None or not plan.remap:
        return []
    stale: list[str] = []
    for path in sorted(plan.inventory):
        if not path.is_file():
            continue
        try:
            payload = read_json(path)
        except ValueError:
            stale.append(str(path.relative_to(book_root)))
            continue
        if _payload_has_stale_site(payload, resolve, plan.remap):
            stale.append(str(path.relative_to(book_root)))
    return stale


def apply_plan(book_root: Path, plan: MigrationPlan, legacy_path: Path, backup_dir: Path) -> None:
    # Stage 4: backup every file that will change.
    make_backup(book_root, plan, legacy_path, backup_dir)

    # Stage 5a: all character files, in mint order (atomic writes via the
    # shared character store).
    now = datetime.now(timezone.utc).isoformat()
    for record, guid, _filename in plan.clusters:
        write_character(book_root, build_v3_record(record, guid, now))

    # Stage 5b: every touched reference file (deterministic order).
    for path, payload in sorted(plan.ref_changes.items()):
        atomic_write_json(path, payload)

    # Stage 5c: LAST — retire the legacy store.
    legacy_path.unlink()

    # Stage 5d: post-apply rescan (mandatory before success).
    stale = rescan_for_stale_ids(book_root, plan)
    if stale:
        raise MigrationAbort(
            f"Post-apply rescan found surviving v2 ids in: {', '.join(stale)}. "
            f"Migration is incomplete — restore from {backup_dir} and re-run."
        )


# ---------------------------------------------------------------------------
# Report + entry point
# ---------------------------------------------------------------------------


def print_report(plan: MigrationPlan, book_root: Path, legacy_path: Path, backup_dir: Path) -> None:
    print(f"Book: {book_root.name}")
    print(
        f"Mode: {'v2 (characters.json)' if plan.mode == 'v2' else 'v1 (book.characters.json)'}"
    )
    print(f"Clusters: {len(plan.clusters)}")
    print("Planned character files:")
    for record, guid, filename in plan.clusters:
        print(f"  - characters/{filename}  (guid {guid}, was id '{clean_text(record.get('id'))}')")
    print(f"Planned reference changes: {len(plan.ref_changes)}")
    for path in sorted(plan.ref_changes):
        print(f"  - {path.relative_to(book_root)}")
    counts = plan.counts
    print(
        "Reference counts: "
        f"dialogue {counts['dialogueRefs']} · roster {counts['rosterRefs']} · "
        f"voices {counts['voiceRefs']} · manifests {counts['manifestRefs']}"
    )
    if plan.unresolved_refs:
        print(f"Unresolved references (left in place, unremapped): {len(plan.unresolved_refs)}")
        for item in plan.unresolved_refs:
            print(f"  - {item}")
    else:
        print("Unresolved references: none")
    if plan.v1_script_only:
        print("v1-script-only chapters (name-based, left untouched):")
        for chapter in plan.v1_script_only:
            print(f"  - {chapter}")
    print(f"Planned backup dir: {backup_dir}")


def main() -> None:
    # Non-ASCII titles (Café, 王梅, …) must not crash the report on a
    # non-UTF-8 console (Windows cp1252): UTF-8 out, undisplayable chars
    # replaced instead of raising.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    args = parse_args()
    book_root = args.book_root.expanduser().resolve()

    if not book_root.is_dir():
        raise SystemExit(f"Book directory does not exist: {book_root}")

    legacy_v2 = book_root / LEGACY_V2_FILENAME
    legacy_v1 = book_root / LEGACY_V1_FILENAME
    legacy_path = (
        legacy_v2
        if legacy_v2.is_file()
        else legacy_v1 if legacy_v1.is_file() else legacy_v2
    )
    mode = "v2" if legacy_path is legacy_v2 else "v1"

    try:
        # Stage 0: idempotency gate (read-only).
        gate = idempotency_gate(book_root, legacy_path)
        if gate == "already-migrated":
            print(
                "Already migrated: the characters/ folder holds valid v3.0 files "
                "with unique GUIDs. No-op; nothing was changed."
            )
            return
        if gate == "in-flight":
            message = (
                "Migration appears to be in flight: the characters/ folder and "
                f"{legacy_path.name} are both present. This state is never resolved "
                "automatically: restore from _backups/character-v3/<ts>/ (or clear "
                "the partial characters/ folder) and re-run."
            )
            print(message)
            raise MigrationAbort(message)
        if not legacy_v2.is_file() and not legacy_v1.is_file():
            raise SystemExit(
                f"No {LEGACY_V2_FILENAME} or {LEGACY_V1_FILENAME} in {book_root}; "
                "nothing to migrate."
            )

        # Stages 1-3: inventory + deterministic minting + validation gates.
        plan = build_plan(book_root, mode, legacy_path, args.drop_unresolved)

        if plan.noop:
            print(f"No character records in {legacy_path.name}; nothing to migrate.")
            return

        if plan.violations:
            print("Validation failed; no files were changed:")
            for violation in plan.violations:
                print(f"  - {violation}")
            raise MigrationAbort("Validation failed; no files were changed.")

        backup_dir = (
            args.backup_dir.expanduser().resolve()
            if args.backup_dir
            else default_backup_dir(book_root)
        )
        print_report(plan, book_root, legacy_path, backup_dir)

        if not args.apply:
            print("Dry run complete; no files were changed. Re-run with --apply to write them.")
            return

        # Stages 4-5: backup + ordered apply + post-apply rescan.
        apply_plan(book_root, plan, legacy_path, backup_dir)
        print(
            f"Migration complete: {len(plan.clusters)} character files, "
            f"{len(plan.ref_changes)} reference files remapped, {legacy_path.name} retired."
        )
        print(f"Backups: {backup_dir}")
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
