"""Router regression tests for /api/save.

SaveRequest.content is a plain dict (Dict[str, Any]); the handler must
serialize it with json.dumps, not a pydantic model method.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from parser_router import create_parser_router


class SaveEndpointTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_save_test_")
        self.book_root = Path(self._temp_dir.name)
        # Hermetic: do not depend on a real .env key for router creation.
        self._env = mock.patch.dict(os.environ, {"VERCEL_JEV_API_KEY": "test-key"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._temp_dir.cleanup()

    def _make_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(create_parser_router(lambda: str(self.book_root)))
        return TestClient(app)

    def test_save_writes_dict_content_as_json(self):
        content = {"characters": [{"id": "mara", "name": "Mara", "aliases": ["M."]}]}
        with self._make_client() as client:
            response = client.post(
                "/api/save",
                json={"file_path": "characters.json", "content": content},
            )
        self.assertEqual(response.status_code, 200)
        written = (self.book_root / "characters.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(written), content)

    def test_save_preserves_non_ascii_content(self):
        content = {"note": "caf\u00e9 \u2014 \u66f8"}
        with self._make_client() as client:
            response = client.post(
                "/api/save",
                json={"file_path": "note.json", "content": content},
            )
        self.assertEqual(response.status_code, 200)
        written = (self.book_root / "note.json").read_text(encoding="utf-8")
        self.assertIn("caf\u00e9", written)
