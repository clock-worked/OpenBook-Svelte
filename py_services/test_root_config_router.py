"""Router tests for /api/set-book-root (ADR 0012, candidate 5).

Selecting a book (re)builds the in-memory stats accumulation. A build
failure never fails the root switch, but it is not silent either: the
response reports it (`stats_rebuilt`) and it is logged.
"""

import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import root_config_router
from root_config_router import create_root_config_router


class _FakeRuntimeState:
    """Only what set_book_root touches; an audio root is already set so the
    audio service is never initialized."""

    def __init__(self) -> None:
        self.book_root = ""

    def set_book_root(self, root_path: str) -> str:
        self.book_root = root_path
        return root_path

    def get_audio_root(self) -> str:
        return "already-set"


class SetBookRootTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_root_test_")
        self.book_root = self._temp_dir.name
        self.runtime_state = _FakeRuntimeState()

    def tearDown(self):
        self._temp_dir.cleanup()

    def _make_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(create_root_config_router(self.runtime_state))
        return TestClient(app)

    def test_success_reports_stats_rebuilt(self):
        with mock.patch.object(root_config_router.stats_accumulator, "build") as build:
            with self._make_client() as client:
                response = client.post("/api/set-book-root", json={"root_path": self.book_root})
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["stats_rebuilt"], True)
        build.assert_called_once_with(self.book_root)

    def test_build_failure_is_reported_and_logged_not_fatal(self):
        with mock.patch.object(
            root_config_router.stats_accumulator, "build", side_effect=RuntimeError("disk gone")
        ):
            with self.assertLogs(root_config_router.logger, level="WARNING") as logs:
                with self._make_client() as client:
                    response = client.post("/api/set-book-root", json={"root_path": self.book_root})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIs(body["stats_rebuilt"], False)
        self.assertEqual(body["path"], self.book_root)
        self.assertEqual(self.runtime_state.book_root, self.book_root)
        self.assertTrue(any("stats accumulation build failed" in line for line in logs.output))

    def test_invalid_directory_is_rejected_before_any_build(self):
        with mock.patch.object(root_config_router.stats_accumulator, "build") as build:
            with self._make_client() as client:
                response = client.post(
                    "/api/set-book-root", json={"root_path": self.book_root + "/missing"}
                )
        self.assertEqual(response.status_code, 400)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
