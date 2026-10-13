"""Tests for run_status.py: the shared run-status tracker.

Covers the lifecycle (queued -> running -> completed), progress clamping,
inheritance of blank fields, the no-op on blank request ids, and the
64-entry cap that evicts the oldest entry.
"""

import unittest

from run_status import RunStatusTracker


class RunStatusTrackerTests(unittest.TestCase):
    def test_lifecycle_updates(self):
        tracker = RunStatusTracker()
        tracker.prepare("req-1", total_lines=4, chapter_path="chapter-1")
        status = tracker.get("req-1")
        self.assertEqual(status.status, "queued")
        self.assertEqual(status.totalLines, 4)
        self.assertEqual(status.processedLines, 0)
        self.assertEqual(status.chapterPath, "chapter-1")

        tracker.update("req-1", status="running", processed_lines=2, total_lines=4)
        status = tracker.get("req-1")
        self.assertEqual(status.status, "running")
        self.assertEqual(status.processedLines, 2)
        self.assertEqual(status.progressRatio, 0.5)

        tracker.update("req-1", status="completed", processed_lines=4, total_lines=4)
        status = tracker.get("req-1")
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.progressRatio, 1.0)

    def test_unknown_request_returns_none(self):
        tracker = RunStatusTracker()
        self.assertIsNone(tracker.get("nope"))

    def test_blank_request_id_is_noop(self):
        tracker = RunStatusTracker()
        tracker.update("   ", status="running", processed_lines=1, total_lines=1)
        self.assertIsNone(tracker.get(""))

    def test_progress_clamped_to_total(self):
        tracker = RunStatusTracker()
        tracker.update("req-1", status="running", processed_lines=99, total_lines=4)
        status = tracker.get("req-1")
        self.assertEqual(status.processedLines, 4)
        self.assertEqual(status.progressRatio, 1.0)

    def test_blank_fields_inherit_previous(self):
        tracker = RunStatusTracker()
        tracker.update(
            "req-1",
            status="queued",
            processed_lines=0,
            total_lines=4,
            chapter_path="chapter-1",
            log_directory="/logs/run",
        )
        tracker.update("req-1", status="running", processed_lines=1, total_lines=4)
        status = tracker.get("req-1")
        self.assertEqual(status.chapterPath, "chapter-1")
        self.assertEqual(status.logDirectory, "/logs/run")

    def test_failed_status_records_error(self):
        tracker = RunStatusTracker()
        tracker.update("req-1", status="failed", processed_lines=1, total_lines=4, error="boom")
        status = tracker.get("req-1")
        self.assertEqual(status.status, "failed")
        self.assertEqual(status.error, "boom")

    def test_cap_evicts_oldest_entry(self):
        tracker = RunStatusTracker()
        for index in range(RunStatusTracker.MAX_ENTRIES + 1):
            tracker.update(f"req-{index:03d}", status="completed", processed_lines=1, total_lines=1)
        self.assertIsNone(tracker.get("req-000"))
        newest = f"req-{RunStatusTracker.MAX_ENTRIES:03d}"
        self.assertIsNotNone(tracker.get(newest))


if __name__ == "__main__":
    unittest.main()
