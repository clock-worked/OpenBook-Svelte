"""Tests for the incremental character stats path (ADR 0012).

Covers the shared primitives in update_character_stats.py (per-dialogue
counting, book scan, stat derivation, store write) and the stats
accumulation in character_stats_accumulator.py (build, per-chapter delta,
chapter-count transitions, lazy build, write-only-on-change for both the
v3 characters/ folder and the legacy root characters.json).
"""

import json
import tempfile
import unittest
from pathlib import Path

from character_store import load_characters, sanitize_filename
from character_stats_accumulator import (
    StatsAccumulator,
    apply_dialogue_stats_delta,
    stats_accumulator,
)
from update_character_stats import (
    character_counts_in_dialogue,
    derive_character_stats,
    scan_character_chapter_counts,
    update_character_stats,
)


def v3_record(guid: str, title: str, stats: dict) -> dict:
    return {
        "formatVersion": "3.0",
        "guid": guid,
        "title": title,
        "gender": "Unknown",
        "aliases": [],
        "descriptors": [],
        "color": None,
        "notes": "",
        "firstAppearance": None,
        "stats": stats,
    }


def write_v3_character(book_root: Path, guid: str, title: str, stats: dict) -> Path:
    folder = book_root / "characters"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sanitize_filename(title)}.json"
    path.write_text(json.dumps(v3_record(guid, title, stats)), encoding="utf-8")
    return path


def write_dialogue(book_root: Path, chapter: str, counts: dict) -> Path:
    chapter_dir = book_root / chapter
    chapter_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for character_id, count in counts.items():
        lines.extend({"characterId": character_id, "text": "line"} for _ in range(count))
    path = chapter_dir / "dialogue.json"
    path.write_text(json.dumps({"lines": lines}), encoding="utf-8")
    return path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class CharacterCountsTests(unittest.TestCase):
    def test_v2_uses_character_id(self):
        data = {"lines": [
            {"characterId": "alice", "text": "a"},
            {"characterId": "alice", "text": "b"},
            {"characterId": "bob", "text": "c"},
        ]}
        self.assertEqual(character_counts_in_dialogue(data, is_v1=False),
                         {"alice": 2, "bob": 1})

    def test_v1_uses_chosen_speaker(self):
        data = {"lines": [
            {"chosenSpeaker": "Alice", "text": "a"},
            {"characterId": "ignored", "text": "b"},
        ]}
        self.assertEqual(character_counts_in_dialogue(data, is_v1=True),
                         {"Alice": 1})

    def test_empty_speaker_is_not_a_character(self):
        data = {"lines": [
            {"characterId": "alice", "text": "a"},
            {"characterId": None, "text": "narration"},
            {"characterId": "  ", "text": "narration"},
            {"text": "no speaker key"},
            "not a dict",
        ]}
        self.assertEqual(character_counts_in_dialogue(data, is_v1=False),
                         {"alice": 1})


class ScanTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_stats_accum_test_")
        self.book_root = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_scan_builds_per_chapter_matrix(self):
        write_dialogue(self.book_root, "Chapter 1", {"alice": 2, "bob": 1})
        write_dialogue(self.book_root, "Chapter 2", {"alice": 1, "carol": 1})

        matrix, processed = scan_character_chapter_counts(self.book_root)

        self.assertEqual(processed, 2)
        self.assertEqual(matrix, {
            "alice": {"Chapter 1": 2, "Chapter 2": 1},
            "bob": {"Chapter 1": 1},
            "carol": {"Chapter 2": 1},
        })

    def test_dialogue_json_wins_over_script_json(self):
        chapter = self.book_root / "Chapter 1"
        chapter.mkdir(parents=True)
        write_dialogue(self.book_root, "Chapter 1", {"alice": 2})
        (chapter / "Chapter 1.script.json").write_text(
            json.dumps({"lines": [{"chosenSpeaker": "bob"}]}), encoding="utf-8")

        matrix, processed = scan_character_chapter_counts(self.book_root)

        self.assertEqual(processed, 1)
        self.assertEqual(matrix, {"alice": {"Chapter 1": 2}})

    def test_derive_totals_and_chapter_counts(self):
        stats = derive_character_stats({
            "alice": {"Chapter 1": 2, "Chapter 2": 1},
            "bob": {"Chapter 1": 1},
        })
        self.assertEqual(stats["alice"], {"totalLines": 3, "chapterCount": 2})
        self.assertEqual(stats["bob"], {"totalLines": 1, "chapterCount": 1})


class AccumulatorTests(unittest.TestCase):
    """StatsAccumulator with a fresh instance (no singleton involved)."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_stats_accum_test_")
        self.book_root = Path(self._temp_dir.name)
        self.accumulator = StatsAccumulator()

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_build_then_apply_chapter_delta(self):
        write_dialogue(self.book_root, "Chapter 1", {"alice": 2, "bob": 1})
        write_dialogue(self.book_root, "Chapter 2", {"alice": 1})
        self.accumulator.build(self.book_root)

        stats = self.accumulator.apply_chapter(self.book_root, "Chapter 1",
                                               {"alice": 3, "bob": 1})

        self.assertEqual(stats["alice"], {"totalLines": 4, "chapterCount": 2})
        self.assertEqual(stats["bob"], {"totalLines": 1, "chapterCount": 1})

    def test_build_replaces_previous_matrix(self):
        write_dialogue(self.book_root, "Chapter 1", {"alice": 2})
        self.accumulator.build(self.book_root)

        # The book changed on disk (e.g. a re-scan after external edits);
        # a rebuild must replace, not merge, the previous matrix.
        (self.book_root / "Chapter 1" / "dialogue.json").unlink()
        write_dialogue(self.book_root, "Chapter 2", {"bob": 5})
        self.accumulator.build(self.book_root)

        stats = self.accumulator.apply_chapter(self.book_root, "Chapter 2", {"bob": 5})
        self.assertNotIn("alice", stats)
        self.assertEqual(stats["bob"], {"totalLines": 5, "chapterCount": 1})

    def test_apply_chapter_chapter_count_transitions(self):
        write_dialogue(self.book_root, "Chapter 1", {"alice": 2})
        self.accumulator.build(self.book_root)

        # 0 -> 1: alice gains a second chapter.
        stats = self.accumulator.apply_chapter(self.book_root, "Chapter 2", {"alice": 1})
        self.assertEqual(stats["alice"], {"totalLines": 3, "chapterCount": 2})

        # 1 -> 0: alice's first chapter is re-saved with no attributed lines.
        stats = self.accumulator.apply_chapter(self.book_root, "Chapter 1", {})
        self.assertEqual(stats["alice"], {"totalLines": 1, "chapterCount": 1})

    def test_apply_chapter_drops_fully_empty_character(self):
        write_dialogue(self.book_root, "Chapter 1", {"alice": 2, "bob": 1})
        self.accumulator.build(self.book_root)

        stats = self.accumulator.apply_chapter(self.book_root, "Chapter 1", {"alice": 2})
        self.assertNotIn("bob", stats)

    def test_apply_chapter_lazy_builds_when_missing(self):
        write_dialogue(self.book_root, "Chapter 1", {"alice": 2, "bob": 1})
        write_dialogue(self.book_root, "Chapter 2", {"carol": 1})
        # No build() call: a save right after a backend restart must work.

        stats = self.accumulator.apply_chapter(self.book_root, "Chapter 2", {"carol": 3})

        self.assertEqual(stats["alice"], {"totalLines": 2, "chapterCount": 1})
        self.assertEqual(stats["bob"], {"totalLines": 1, "chapterCount": 1})
        self.assertEqual(stats["carol"], {"totalLines": 3, "chapterCount": 1})


class V3DeltaTests(unittest.TestCase):
    """apply_dialogue_stats_delta against a v3 characters/ folder."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_stats_accum_test_")
        self.book_root = Path(self._temp_dir.name)
        # File stats match what the accumulation will derive, so the delta
        # after a changed save touches exactly one file.
        self.alice_path = write_v3_character(self.book_root, "AAAAAAAA", "Alice",
                                             {"totalLines": 3, "chapterCount": 2})
        self.bob_path = write_v3_character(self.book_root, "BBBBBBBB", "Bob",
                                           {"totalLines": 1, "chapterCount": 1})
        self.carol_path = write_v3_character(self.book_root, "CCCCCCCC", "Carol",
                                             {"totalLines": 1, "chapterCount": 1})
        write_dialogue(self.book_root, "Chapter 1", {"AAAAAAAA": 2, "BBBBBBBB": 1})
        write_dialogue(self.book_root, "Chapter 2", {"AAAAAAAA": 1, "CCCCCCCC": 1})

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_only_changed_character_file_rewritten(self):
        # Build from the current disk state first, so the delta below is a
        # genuine change against the accumulation (not a lazy rebuild).
        stats_accumulator.build(self.book_root)
        before = {name: path.read_text(encoding="utf-8")
                  for name, path in [("bob", self.bob_path), ("carol", self.carol_path)]}

        dialogue_path = write_dialogue(self.book_root, "Chapter 1",
                                       {"AAAAAAAA": 3, "BBBBBBBB": 1})
        result = apply_dialogue_stats_delta(self.book_root, dialogue_path,
                                            read_json(dialogue_path))

        self.assertTrue(result["success"])
        self.assertEqual(result["updatedCharacters"], 1)
        self.assertEqual(result["totalCharacters"], 3)

        alice = read_json(self.alice_path)
        self.assertEqual(alice["stats"], {"totalLines": 4, "chapterCount": 2})
        # Write-only-on-change: untouched characters keep their exact files.
        self.assertEqual(self.bob_path.read_text(encoding="utf-8"), before["bob"])
        self.assertEqual(self.carol_path.read_text(encoding="utf-8"), before["carol"])

    def test_unmatched_character_files_get_zero_stats(self):
        # A save that attributes a chapter to a name no character record
        # matches: records drained to zero lines are zeroed, not dropped;
        # characters with lines in other chapters keep their stats.
        stats_accumulator.build(self.book_root)
        dialogue_path = write_dialogue(self.book_root, "Chapter 1", {"unknown": 4})
        result = apply_dialogue_stats_delta(self.book_root, dialogue_path,
                                            read_json(dialogue_path))

        self.assertTrue(result["success"])
        self.assertEqual(read_json(self.alice_path)["stats"],
                         {"totalLines": 1, "chapterCount": 1})
        self.assertEqual(read_json(self.bob_path)["stats"],
                         {"totalLines": 0, "chapterCount": 0})
        self.assertEqual(read_json(self.carol_path)["stats"],
                         {"totalLines": 1, "chapterCount": 1})


class LegacyDeltaTests(unittest.TestCase):
    """apply_dialogue_stats_delta against the legacy root characters.json."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_stats_accum_test_")
        self.book_root = Path(self._temp_dir.name)
        (self.book_root / "characters.json").write_text(json.dumps({
            "formatVersion": "2.0",
            "characters": [
                {"id": "alice", "name": "Alice", "stats": {"totalLines": 2, "chapterCount": 1}},
                {"id": "bob", "name": "Bob", "stats": {"totalLines": 1, "chapterCount": 1}},
            ],
        }), encoding="utf-8")
        self.dialogue_path = write_dialogue(self.book_root, "Chapter 1",
                                            {"alice": 2, "bob": 1})

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_legacy_characters_json_updated_from_content(self):
        new_dialogue = read_json(self.dialogue_path)
        new_dialogue["lines"].append({"characterId": "alice", "text": "extra"})
        # Simulate the combined endpoint: the document is already in hand.
        self.dialogue_path.write_text(json.dumps(new_dialogue), encoding="utf-8")

        result = apply_dialogue_stats_delta(self.book_root, self.dialogue_path,
                                            new_dialogue)

        self.assertTrue(result["success"])
        characters = {c["id"]: c for c in read_json(self.book_root / "characters.json")["characters"]}
        self.assertEqual(characters["alice"]["stats"], {"totalLines": 3, "chapterCount": 1})
        self.assertEqual(characters["bob"]["stats"], {"totalLines": 1, "chapterCount": 1})

    def test_reads_file_from_disk_when_content_none(self):
        # The dual-I/O (FSA) case: the frontend wrote the file, the backend
        # reads it back.
        new_dialogue = read_json(self.dialogue_path)
        new_dialogue["lines"].append({"characterId": "bob", "text": "extra"})
        self.dialogue_path.write_text(json.dumps(new_dialogue), encoding="utf-8")

        result = apply_dialogue_stats_delta(self.book_root, self.dialogue_path)

        self.assertTrue(result["success"])
        characters = {c["id"]: c for c in read_json(self.book_root / "characters.json")["characters"]}
        self.assertEqual(characters["bob"]["stats"], {"totalLines": 2, "chapterCount": 1})


class FullRebuildTests(unittest.TestCase):
    """The full book-wide rebuild (manual button / migration) is unchanged."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_stats_accum_test_")
        self.book_root = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_full_rebuild_v3_folder(self):
        write_v3_character(self.book_root, "AAAAAAAA", "Alice", {"totalLines": 0, "chapterCount": 0})
        write_dialogue(self.book_root, "Chapter 1", {"AAAAAAAA": 2})

        result = update_character_stats(self.book_root)

        self.assertTrue(result["success"])
        self.assertEqual(result["updatedCharacters"], 1)
        records, source = load_characters(self.book_root)
        self.assertEqual(source, "folder")
        self.assertEqual(records[0]["stats"], {"totalLines": 2, "chapterCount": 1})

    def test_full_rebuild_legacy(self):
        (self.book_root / "characters.json").write_text(json.dumps({
            "formatVersion": "2.0",
            "characters": [
                {"id": "alice", "name": "Alice", "stats": {"totalLines": 0, "chapterCount": 0}},
                {"id": "bob", "name": "Bob", "stats": {"totalLines": 0, "chapterCount": 0}},
            ],
        }), encoding="utf-8")
        write_dialogue(self.book_root, "Chapter 1", {"alice": 3})

        result = update_character_stats(self.book_root)

        self.assertTrue(result["success"])
        self.assertEqual(result["updatedCharacters"], 1)
        characters = {c["id"]: c for c in read_json(self.book_root / "characters.json")["characters"]}
        self.assertEqual(characters["alice"]["stats"], {"totalLines": 3, "chapterCount": 1})
        self.assertEqual(characters["bob"]["stats"], {"totalLines": 0, "chapterCount": 0})


if __name__ == '__main__':
    unittest.main()
