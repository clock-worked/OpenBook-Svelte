"""Tests for the shared v3 character folder loader (character_store.py).

Covers: folder-vs-legacy resolution, valid/invalid v3 file handling, the
IN-2 write guard, sanitize_filename cases, and write atomicity.
"""

import json
import tempfile
import unittest
from pathlib import Path

from character_store import (
    CROCKFORD_BASE32_ALPHABET,
    CharacterStoreError,
    delete_character_file,
    guid_from_bytes,
    is_valid_guid,
    load_characters,
    load_character_folder,
    mint_guid,
    sanitize_filename,
    write_character,
)


def v3_record(guid: str, title: str, **extra) -> dict:
    record = {
        "formatVersion": "3.0",
        "guid": guid,
        "title": title,
        "gender": "Female",
        "aliases": ["Cat"],
        "descriptors": [],
        "color": "#7C3AED",
        "notes": "",
        "firstAppearance": "chapter-3",
        "stats": {"totalLines": 142, "chapterCount": 9},
    }
    record.update(extra)
    return record


class CharacterStoreTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_char_store_test_")
        self.book_root = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def _write_v3(self, guid: str, title: str, **extra) -> Path:
        folder = self.book_root / "characters"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sanitize_filename(title)}.json"
        path.write_text(json.dumps(v3_record(guid, title, **extra)), encoding="utf-8")
        return path

    def _write_legacy(self, characters: list) -> None:
        (self.book_root / "characters.json").write_text(
            json.dumps(
                {"formatVersion": "2.0", "characters": characters},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    # ------------------------
    # Folder-vs-legacy resolution
    # ------------------------

    def test_folder_wins_when_valid_v3_file_present(self):
        self._write_v3("7KQX2MFT", "Catherine")
        self._write_legacy([{"id": "mara", "name": "Mara", "aliases": []}])

        records, source = load_characters(self.book_root)

        self.assertEqual(source, "folder")
        self.assertEqual([record["id"] for record in records], ["7KQX2MFT"])
        self.assertEqual(records[0]["name"], "Catherine")

    def test_falls_back_to_legacy_without_folder(self):
        self._write_legacy([
            {"id": "mara", "name": "Mara", "aliases": ["M."]},
            {"id": "dorian", "name": "Dorian", "aliases": []},
        ])

        records, source = load_characters(self.book_root)

        self.assertEqual(source, "legacy")
        self.assertEqual([record["id"] for record in records], ["mara", "dorian"])

    def test_invalid_folder_files_fall_back_to_legacy(self):
        folder = self.book_root / "characters"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "bad-guid.json").write_text(
            json.dumps(v3_record("not-a-guid", "Bad Guid")), encoding="utf-8"
        )
        (folder / "bad-version.json").write_text(
            json.dumps(v3_record("7KQX2MFT", "Bad Version", formatVersion="2.0")),
            encoding="utf-8",
        )
        (folder / "broken.json").write_text("{not json", encoding="utf-8")
        self._write_legacy([{"id": "mara", "name": "Mara", "aliases": []}])

        records, source = load_characters(self.book_root)

        self.assertEqual(source, "legacy")
        self.assertEqual([record["id"] for record in records], ["mara"])

    def test_neither_returns_empty_legacy(self):
        records, source = load_characters(self.book_root)
        self.assertEqual(source, "legacy")
        self.assertEqual(records, [])

    def test_folder_wins_over_legacy_when_both_present(self):
        # Migration-in-flight state: the loader's job is folder-vs-legacy
        # resolution only (the abort is the migration's, per the design).
        self._write_v3("7KQX2MFT", "Catherine")
        self._write_legacy([{"id": "catherine", "name": "Catherine", "aliases": []}])

        records, source = load_characters(self.book_root)

        self.assertEqual(source, "folder")
        self.assertEqual(len(records), 1)

    # ------------------------
    # Valid/invalid v3 file handling
    # ------------------------

    def test_records_normalized_with_id_and_name_mirrors(self):
        self._write_v3("7KQX2MFT", "Catherine")

        records, _source = load_characters(self.book_root)

        self.assertEqual(records[0]["id"], "7KQX2MFT")
        self.assertEqual(records[0]["name"], "Catherine")
        self.assertEqual(records[0]["guid"], "7KQX2MFT")
        self.assertEqual(records[0]["title"], "Catherine")

    def test_folder_records_sorted_by_filename(self):
        self._write_v3("AAAAAAAA", "Zed")
        self._write_v3("BBBBBBBB", "Ann")

        records, _source = load_characters(self.book_root)

        self.assertEqual([record["name"] for record in records], ["Ann", "Zed"])

    def test_v2_field_names_not_invented_for_folder_records(self):
        self._write_v3("7KQX2MFT", "Catherine")

        records, _source = load_characters(self.book_root)

        # The v3 file itself keeps v3 field names; id/name are added, not
        # substituted.
        self.assertIn("title", records[0])
        self.assertIn("guid", records[0])

    # ------------------------
    # IN-2 write guard
    # ------------------------

    def test_write_refused_for_guidless_record_when_characters_json_exists(self):
        self._write_legacy([{"id": "mara", "name": "Mara", "aliases": []}])
        record = {"title": "Mara", "name": "Mara", "gender": "Female", "aliases": []}

        with self.assertRaises(CharacterStoreError):
            write_character(self.book_root, record)

        self.assertFalse((self.book_root / "characters").exists())

    def test_write_refused_for_missing_title(self):
        with self.assertRaises(CharacterStoreError):
            write_character(self.book_root, {"guid": "7KQX2MFT", "title": "  "})

    def test_write_allowed_with_valid_guid(self):
        filename = write_character(self.book_root, v3_record("7KQX2MFT", "Catherine"))

        self.assertEqual(filename, "Catherine.json")
        written = json.loads(
            (self.book_root / "characters" / "Catherine.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written["guid"], "7KQX2MFT")
        self.assertEqual(written["formatVersion"], "3.0")
        self.assertIn("updatedAt", written)

    def test_delete_character_file_idempotent(self):
        self._write_v3("7KQX2MFT", "Catherine")

        self.assertTrue(delete_character_file(self.book_root, "Catherine.json"))
        self.assertFalse(delete_character_file(self.book_root, "Catherine.json"))
        self.assertFalse(delete_character_file(self.book_root, "Nobody.json"))

    def test_delete_rejects_path_traversal(self):
        with self.assertRaises(CharacterStoreError):
            delete_character_file(self.book_root, "../characters.json")
        with self.assertRaises(CharacterStoreError):
            delete_character_file(self.book_root, "sub/file.json")

    # ------------------------
    # sanitize_filename
    # ------------------------

    def test_sanitize_filename_cases(self):
        self.assertEqual(sanitize_filename("Catherine"), "Catherine")
        # Spec: replace invalid chars, collapse REPEATED dashes only.
        self.assertEqual(sanitize_filename('Cat: The "Mouse" <file>'), "Cat- The -Mouse- -file")
        self.assertEqual(sanitize_filename('a/b\\c:d*e?f"g<h>i|j'), "a-b-c-d-e-f-g-h-i-j")
        self.assertEqual(sanitize_filename("  --Spaced--  "), "Spaced")
        self.assertEqual(sanitize_filename("CON"), "-CON")
        self.assertEqual(sanitize_filename("nul"), "-nul")
        self.assertEqual(sanitize_filename("COM3"), "-COM3")
        self.assertEqual(sanitize_filename("CONTR"), "CONTR")
        self.assertEqual(len(sanitize_filename("x" * 100)), 80)
        self.assertEqual(sanitize_filename("a" * 79 + "-b"), "a" * 79)
        self.assertEqual(sanitize_filename(""), "-")
        self.assertEqual(sanitize_filename("///"), "-")

    # ------------------------
    # Write atomicity
    # ------------------------

    def test_write_is_atomic_and_replaces_existing(self):
        path = self._write_v3("7KQX2MFT", "Catherine")
        filename = write_character(self.book_root, v3_record("7KQX2MFT", "Catherine", notes="edited"))

        self.assertEqual(filename, path.name)
        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written["notes"], "edited")
        # No stray temp files left in the folder.
        self.assertEqual(
            [item.name for item in (self.book_root / "characters").iterdir()],
            ["Catherine.json"],
        )

    # ------------------------
    # GUID helpers
    # ------------------------

    def test_is_valid_guid(self):
        self.assertTrue(is_valid_guid("7KQX2MFT"))
        self.assertFalse(is_valid_guid("7KQX2MF"))       # 7 chars
        self.assertFalse(is_valid_guid("7KQX2MFTT"))     # 9 chars
        self.assertFalse(is_valid_guid("7kqxmfta"))      # lowercase
        self.assertFalse(is_valid_guid("7KQX2MFI"))      # I not in alphabet
        self.assertFalse(is_valid_guid("7KQX2MFO"))      # O not in alphabet
        self.assertFalse(is_valid_guid(None))
        self.assertFalse(is_valid_guid(7))

    def test_alphabet_is_32_crockford_symbols(self):
        self.assertEqual(len(CROCKFORD_BASE32_ALPHABET), 32)
        self.assertEqual(len(set(CROCKFORD_BASE32_ALPHABET)), 32)
        for excluded in "ILOU":
            self.assertNotIn(excluded, CROCKFORD_BASE32_ALPHABET)

    def test_guid_from_bytes_round_trip(self):
        self.assertEqual(guid_from_bytes(bytes([0, 0, 0, 0, 1])), "00000001")
        # 2**32 big-endian -> first base32 digit is 4 (big-endian check).
        self.assertEqual(guid_from_bytes(bytes([1, 0, 0, 0, 0])), "04000000")
        with self.assertRaises(ValueError):
            guid_from_bytes(bytes(4))

    def test_mint_guid_is_valid_and_unique(self):
        mints = {mint_guid() for _ in range(100)}
        self.assertTrue(all(is_valid_guid(guid) for guid in mints))
        self.assertEqual(len(mints), 100)


if __name__ == "__main__":
    unittest.main()
