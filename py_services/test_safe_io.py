"""Tests for safe_io.py: path-traversal guarding and atomic JSON writes.

resolve_within_root must reject anything that escapes the book root
(``..`` segments, absolute paths) while accepting legitimate names that
merely *contain* the substring ``..`` (e.g. ``a..b.txt``).
write_json_atomic must land valid JSON and never leave a temp file behind.
"""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from safe_io import resolve_within_root, write_json_atomic


class ResolveWithinRootTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_safe_io_test_")
        self.book_root = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_parent_traversal_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            resolve_within_root(self.book_root, "../outside.txt")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_nested_traversal_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            resolve_within_root(self.book_root, "characters/../../outside.txt")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_absolute_path_is_rejected(self):
        outside = self.book_root.parent / "outside.txt"
        with self.assertRaises(HTTPException) as ctx:
            resolve_within_root(self.book_root, str(outside))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_name_containing_dot_dot_is_allowed(self):
        resolved = resolve_within_root(self.book_root, "a..b.txt")
        self.assertEqual(resolved, (self.book_root / "a..b.txt").resolve())

    def test_nested_path_inside_root_is_allowed(self):
        resolved = resolve_within_root(self.book_root, "characters/Catherine.json")
        self.assertEqual(resolved, (self.book_root / "characters" / "Catherine.json").resolve())


class WriteJsonAtomicTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_safe_io_test_")
        self.target = Path(self._temp_dir.name) / "out.json"

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_writes_valid_json(self):
        payload = {"note": "caf\u00e9 \u2014 \u66f8", "n": 1}
        write_json_atomic(self.target, payload)
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), payload)

    def test_overwrites_existing_file(self):
        self.target.write_text("{}", encoding="utf-8")
        write_json_atomic(self.target, {"replaced": True})
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"replaced": True})

    def test_no_temp_file_left_on_failure(self):
        # A non-serializable payload makes json.dump fail after the temp
        # file has been created; the temp file must be cleaned up.
        with self.assertRaises(TypeError):
            write_json_atomic(self.target, {"bad": {1, 2, 3}})
        self.assertEqual([entry.name for entry in self.target.parent.iterdir()], [])


if __name__ == "__main__":
    unittest.main()
