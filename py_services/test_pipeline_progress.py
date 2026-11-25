"""Unit tests for the pipeline progress record (audio/pipeline_progress.py).

Covers the one owner of the chunk pipeline's progress.json:
- work-dir + scope resolution (single-chapter vs _book_batch layout)
- the record schema and the atomic write (stable startedAt, carry-over,
  fresh record on STARTING, corrupt-file tolerance)
- read_progress (probes both locations, newest wins, scope backfill)
- ProgressTracker (failure path writes where the run was bound)
- the shape vector shared with the FE (tests/pipeline_progress_vectors.json)
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from audio.pipeline_progress import (
    PIPELINE_PROGRESS_FILENAME,
    RECORD_KEYS,
    SCOPES,
    Phase,
    ProgressTracker,
    location_for,
    read_progress,
    resolve_run_location,
    write_progress,
)

VECTORS_PATH = Path(__file__).resolve().parent.parent / "tests" / "pipeline_progress_vectors.json"


def _read(location):
    return json.loads(location.progress_path.read_text(encoding="utf-8"))


class ResolveRunLocationTests(unittest.TestCase):
    def test_single_chapter_layout(self):
        loc = resolve_run_location(Path("C:/audio"), "Rashid", ["Ch 1", "Ch 1"])
        self.assertEqual(loc.scope, "single")
        self.assertEqual(loc.work_root, Path("C:/audio/Ch 1/audio_lines/Rashid"))
        self.assertEqual(loc.work_dir, Path("C:/audio/Ch 1/audio_lines/Rashid/_chunk_pipeline"))
        self.assertEqual(loc.progress_path.name, PIPELINE_PROGRESS_FILENAME)

    def test_cross_chapter_layout(self):
        loc = resolve_run_location(Path("C:/audio"), "Rashid", ["Ch 2", "Ch 1"])
        self.assertEqual(loc.scope, "cross")
        self.assertEqual(loc.work_root, Path("C:/audio/_book_batch/Rashid"))

    def test_blank_titles_fall_back_to_default(self):
        loc = resolve_run_location(
            Path("C:/audio"), "Rashid", [None, "  ", "Ch 1"], default_chapter_title="Ch 1"
        )
        self.assertEqual(loc.scope, "single")
        self.assertEqual(loc.work_root, Path("C:/audio/Ch 1/audio_lines/Rashid"))

    def test_no_lines_is_single_under_default(self):
        loc = resolve_run_location(Path("C:/audio"), "Rashid", [], default_chapter_title="")
        self.assertEqual(loc.scope, "single")
        self.assertEqual(loc.work_root, Path("C:/audio/unknown/audio_lines/Rashid"))


class WriteProgressTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.loc = location_for(self.root, "Rashid", scope="single", chapter_title="Ch 1")

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_all_fields(self):
        write_progress(self.loc, Phase.GENERATING, line_count=24)
        data = _read(self.loc)
        self.assertEqual(set(data), set(RECORD_KEYS))
        self.assertEqual(data["phase"], "generating")
        self.assertEqual(data["scope"], "single")
        self.assertIsNone(data["detail"])
        self.assertEqual(data["lineCount"], 24)
        self.assertTrue(data["startedAt"])
        self.assertTrue(data["updatedAt"])

    def test_started_at_preserved_and_counts_carried(self):
        write_progress(self.loc, Phase.STARTING, line_count=24)
        first = _read(self.loc)
        write_progress(self.loc, Phase.GENERATING, chunk_index=3, chunk_total=7)
        write_progress(self.loc, Phase.TRANSCRIBING)
        second = _read(self.loc)
        self.assertEqual(second["phase"], "transcribing")
        self.assertEqual(second["startedAt"], first["startedAt"])
        self.assertEqual(second["lineCount"], 24)
        self.assertEqual((second["chunkIndex"], second["chunkTotal"]), (3, 7))

    def test_starting_opens_a_fresh_record(self):
        write_progress(self.loc, Phase.STARTING, line_count=24)
        write_progress(self.loc, Phase.GENERATING, chunk_index=7, chunk_total=7)
        write_progress(self.loc, Phase.DONE)
        old = _read(self.loc)
        time.sleep(0.01)

        write_progress(self.loc, Phase.STARTING, line_count=5)
        fresh = _read(self.loc)
        self.assertNotEqual(fresh["startedAt"], old["startedAt"])
        self.assertEqual(fresh["lineCount"], 5)
        self.assertIsNone(fresh["chunkIndex"])
        self.assertIsNone(fresh["chunkTotal"])

    def test_corrupt_previous_file_is_replaced_not_fatal(self):
        self.loc.work_dir.mkdir(parents=True, exist_ok=True)
        self.loc.progress_path.write_text("not json", encoding="utf-8")
        write_progress(self.loc, Phase.SPLITTING, detail="x")
        data = _read(self.loc)
        self.assertEqual(data["phase"], "splitting")
        self.assertEqual(data["detail"], "x")
        self.assertTrue(data["startedAt"])

    def test_never_raises(self):
        blocker = self.root / "file"
        blocker.write_text("x", encoding="utf-8")
        bad = location_for(blocker, "Rashid", scope="cross")  # parent is a file
        write_progress(bad, Phase.STARTING)  # must not raise


class ReadProgressTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_record_is_unknown(self):
        data = read_progress(self.root, "Rashid", chapter_title="Ch 1")
        self.assertEqual(set(data), set(RECORD_KEYS))
        self.assertEqual(data["phase"], "unknown")
        self.assertIsNone(data["scope"])

    def test_finds_cross_record_without_being_told(self):
        cross = location_for(self.root, "Rashid", scope="cross")
        write_progress(cross, Phase.ALIGNING)
        data = read_progress(self.root, "Rashid", chapter_title="Ch 1")
        self.assertEqual((data["phase"], data["scope"]), ("aligning", "cross"))

    def test_newest_location_wins(self):
        single = location_for(self.root, "Rashid", scope="single", chapter_title="Ch 1")
        cross = location_for(self.root, "Rashid", scope="cross")
        write_progress(cross, Phase.DONE)
        time.sleep(0.01)
        write_progress(single, Phase.GENERATING)
        self.assertEqual(read_progress(self.root, "Rashid", chapter_title="Ch 1")["scope"], "single")
        time.sleep(0.01)
        write_progress(cross, Phase.STARTING)
        self.assertEqual(read_progress(self.root, "Rashid", chapter_title="Ch 1")["scope"], "cross")

    def test_legacy_record_gets_scope_and_missing_keys(self):
        single = location_for(self.root, "Rashid", scope="single", chapter_title="Ch 1")
        single.work_dir.mkdir(parents=True)
        single.progress_path.write_text(
            json.dumps({"phase": "generating", "updatedAt": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        data = read_progress(self.root, "Rashid", chapter_title="Ch 1")
        self.assertEqual(set(data), set(RECORD_KEYS))
        self.assertEqual(data["scope"], "single")


class ProgressTrackerTests(unittest.TestCase):
    def test_unbound_writes_are_dropped(self):
        tracker = ProgressTracker()
        tracker.fail("boom")  # no location yet: nothing to write, no error
        self.assertIsNone(tracker.location)

    def test_fail_writes_at_bound_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc = location_for(Path(tmp), "Rashid", scope="cross")
            tracker = ProgressTracker()
            tracker.bind(loc)
            tracker.write(Phase.STARTING, line_count=3)
            tracker.fail("x" * 600)
            data = _read(loc)
            self.assertEqual(data["phase"], "failed")
            self.assertEqual(len(data["detail"]), 500)
            self.assertEqual(data["lineCount"], 3)


class ShapeVectorTests(unittest.TestCase):
    def test_python_matches_shared_vector(self):
        vectors = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
        self.assertEqual([p.value for p in Phase], vectors["phases"])
        self.assertEqual(list(SCOPES), vectors["scopes"])
        self.assertEqual(list(RECORD_KEYS), vectors["recordKeys"])


if __name__ == "__main__":
    unittest.main()
