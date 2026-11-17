"""Router regression tests for /api/save and /api/save-dialogue.

SaveRequest.content is a plain dict (Dict[str, Any]); the handler must
serialize it with json.dumps, not a pydantic model method.

/api/save-dialogue (ADR 0012) writes the dialogue file, then applies the
character stats delta; a stats failure never fails the save.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

    def test_unexpected_jev_failure_returns_json_with_cors(self):
        app = FastAPI()
        origin = "http://127.0.0.1:5173"
        app.add_middleware(CORSMiddleware, allow_origins=[origin])
        with mock.patch("parser_router.JevService") as service_class:
            service_class.return_value.run.side_effect = RuntimeError("test JEV failure")
            app.include_router(create_parser_router(lambda: str(self.book_root)))
            with TestClient(app) as client, self.assertLogs("parser_router", level="ERROR"):
                response = client.post(
                    "/api/jev-dialogue-ai",
                    headers={"Origin": origin},
                    json={
                        "request_id": "failed-jev",
                        "chapter_path": "chapter",
                        "chapter_text": "Hello",
                        "dialogue": {
                            "formatVersion": "2.0",
                            "chapterId": "chapter",
                            "lines": [],
                            "stats": {"totalLines": 0, "conflicts": 0, "characterBreakdown": {}},
                        },
                    },
                )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers.get("access-control-allow-origin"), origin)
        self.assertEqual(response.json()["detail"], "JEV dialogue assist failed: test JEV failure")


class SaveDialogueEndpointTests(unittest.TestCase):
    """Pins the /api/save-dialogue contract: write, then stats delta; a
    stats failure is logged and never fails the save. /api/save has no
    stats step."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_save_dialogue_test_")
        self.book_root = Path(self._temp_dir.name)
        self._env = mock.patch.dict(os.environ, {"VERCEL_JEV_API_KEY": "test-key"})
        self._env.start()
        self.dialogue = {
            "formatVersion": "3.2",
            "chapterId": "Chapter 1",
            "lines": [{"id": 1, "characterId": "AA01BB02", "text": "Hello."}],
        }

    def tearDown(self):
        self._env.stop()
        self._temp_dir.cleanup()

    def _make_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(create_parser_router(lambda: str(self.book_root)))
        return TestClient(app)

    def _post(self, endpoint: str):
        with self._make_client() as client:
            return client.post(
                endpoint,
                json={"file_path": "Chapter 1/dialogue.json", "content": self.dialogue},
            )

    def _written(self):
        return json.loads((self.book_root / "Chapter 1" / "dialogue.json").read_text(encoding="utf-8"))

    def test_save_dialogue_writes_then_applies_stats_delta(self):
        with mock.patch("parser_router.apply_dialogue_stats_delta") as apply_delta:
            response = self._post("/api/save-dialogue")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._written(), self.dialogue)
        apply_delta.assert_called_once()
        root_arg, path_arg, content_arg = apply_delta.call_args.args
        self.assertEqual(Path(root_arg), self.book_root)
        self.assertEqual(Path(path_arg).resolve(), (self.book_root / "Chapter 1" / "dialogue.json").resolve())
        self.assertEqual(content_arg, self.dialogue)

    def test_stats_failure_never_fails_the_save(self):
        with mock.patch(
            "parser_router.apply_dialogue_stats_delta",
            side_effect=RuntimeError("accumulator exploded"),
        ), self.assertLogs("parser_router", level="WARNING") as logs:
            response = self._post("/api/save-dialogue")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._written(), self.dialogue)
        self.assertTrue(any("stats delta failed" in line for line in logs.output))

    def test_plain_save_has_no_stats_step(self):
        with mock.patch("parser_router.apply_dialogue_stats_delta") as apply_delta:
            response = self._post("/api/save")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._written(), self.dialogue)
        apply_delta.assert_not_called()
