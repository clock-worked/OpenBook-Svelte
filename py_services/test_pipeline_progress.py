"""Unit tests for the chunk-pipeline progress bookkeeping in audio/vibevoice.py.

These cover the pure helpers that let the UI poll live pipeline progress:
- ``_resolve_character_work_root`` (single-chapter vs _book_batch layout)
- ``_write_pipeline_progress`` (atomic JSON, stable startedAt, lineCount carry)
"""

import json
import tempfile
import unittest
from pathlib import Path

from audio.vibevoice import (
    PIPELINE_PROGRESS_FILENAME,
    _resolve_character_work_root,
    _write_pipeline_progress,
)


class ResolveWorkRootTests(unittest.TestCase):
    def test_single_chapter_layout(self):
        out = _resolve_character_work_root(
            Path("C:/audio"), "Rashid", single_chapter=True, chapter_title="Ch 1"
        )
        self.assertEqual(out, Path("C:/audio/Ch 1/audio_lines/Rashid"))

    def test_cross_chapter_layout(self):
        out = _resolve_character_work_root(
            Path("C:/audio"), "Rashid", single_chapter=False, chapter_title="Ch 1"
        )
        self.assertEqual(out, Path("C:/audio/_book_batch/Rashid"))


class WritePipelineProgressTests(unittest.TestCase):
    def test_writes_all_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "work"
            _write_pipeline_progress(work_dir, "generating", line_count=24)

            data = json.loads((work_dir / PIPELINE_PROGRESS_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(data["phase"], "generating")
            self.assertIsNone(data["detail"])
            self.assertEqual(data["lineCount"], 24)
            self.assertTrue(data["startedAt"])
            self.assertTrue(data["updatedAt"])

    def test_started_at_preserved_and_line_count_carried(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "work"
            _write_pipeline_progress(work_dir, "starting", line_count=24)
            first = json.loads((work_dir / PIPELINE_PROGRESS_FILENAME).read_text(encoding="utf-8"))

            _write_pipeline_progress(work_dir, "transcribing")

            second = json.loads((work_dir / PIPELINE_PROGRESS_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(second["phase"], "transcribing")
            self.assertEqual(second["startedAt"], first["startedAt"])
            self.assertEqual(second["lineCount"], 24)

    def test_corrupt_previous_file_is_replaced_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "work"
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / PIPELINE_PROGRESS_FILENAME).write_text("not json", encoding="utf-8")

            _write_pipeline_progress(work_dir, "splitting", detail="x")

            data = json.loads((work_dir / PIPELINE_PROGRESS_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(data["phase"], "splitting")
            self.assertEqual(data["detail"], "x")
            self.assertTrue(data["startedAt"])


if __name__ == "__main__":
    unittest.main()
