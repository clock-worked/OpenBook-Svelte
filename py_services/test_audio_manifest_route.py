"""The audio manifest routes stay inside the audio root.

- ``POST /api/audio-manifest`` reads one stored manifest, addressed by
  chapter + character (never by a file path), and never writes.
- ``POST /api/reconcile-audio-manifest`` refuses names that leave the root.
- ``POST /api/read_file_absolute`` (an unrestricted JSON file read) is gone.
"""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from audio.manifest_store import ManifestPathError, manifest_path_for, reconcile
from audio_router import create_audio_router

CHAPTER = "Chapter 1"
CHARACTER = "Rashid"


class _FakeAudioService:
    """Just the surface the manifest routes use."""

    def __init__(self, audio_root: Path):
        self.audio_root = audio_root
        self.reconciled = []

    def reconcile_character_manifest(self, chapter_title, character_name):
        self.reconciled.append((chapter_title, character_name))
        return reconcile(self.audio_root, chapter_title, character_name)


class AudioManifestRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "audio"
        self.root.mkdir()
        # A JSON file outside the audio root that must stay unreadable.
        self.secret = base / "secret.json"
        self.secret.write_text(json.dumps({"token": "x"}), encoding="utf-8")
        self.service = _FakeAudioService(self.root)
        app = FastAPI()
        app.include_router(
            create_audio_router(
                get_audio_root=lambda: str(self.root),
                get_book_root=lambda: "",
                get_audio_service=lambda: self.service,
                set_audio_service=lambda _root, _service: None,
            )
        )
        self.client = TestClient(app)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_manifest(self, payload):
        path = manifest_path_for(self.root, CHAPTER, CHARACTER)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _read(self, chapter=CHAPTER, character=CHARACTER):
        return self.client.post(
            "/api/audio-manifest",
            json={"chapter_title": chapter, "character_name": character},
        )

    def test_reads_the_stored_manifest(self):
        stored = {"formatVersion": "2.0", "characterName": CHARACTER, "clips": []}
        self._write_manifest(stored)
        response = self._read()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"manifest": stored})

    def test_missing_manifest_is_null_not_404(self):
        response = self._read()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"manifest": None})

    def test_read_never_writes(self):
        # Orphan audio would make reconcile bootstrap a manifest; a read must not.
        character_dir = manifest_path_for(self.root, CHAPTER, CHARACTER).parent
        character_dir.mkdir(parents=True)
        (character_dir / "1-line.wav").write_bytes(b"")
        self.assertEqual(self._read().json(), {"manifest": None})
        self.assertFalse((character_dir / "manifest.json").exists())

    def test_traversal_is_refused(self):
        for chapter, character in (
            ("..", CHARACTER),
            ("../..", "x"),
            (CHAPTER, "../../../secret"),
            (str(self.secret.parent), CHARACTER),  # absolute chapter title
        ):
            with self.subTest(chapter=chapter, character=character):
                self.assertEqual(self._read(chapter, character).status_code, 400)

    def test_reconcile_refuses_traversal(self):
        response = self.client.post(
            "/api/reconcile-audio-manifest",
            json={"chapter_title": "../..", "character_name": "x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_absolute_file_read_route_is_gone(self):
        response = self.client.post("/api/read_file_absolute", json={"file_path": str(self.secret)})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("token", response.text)


class CharacterAudioDirTests(unittest.TestCase):
    def test_plain_names_resolve_under_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = manifest_path_for(tmp, CHAPTER, CHARACTER)
            self.assertEqual(path, Path(tmp) / CHAPTER / "audio_lines" / CHARACTER / "manifest.json")

    def test_names_containing_dots_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path_for(tmp, "Ch. 1... The End", "Mr. Smith")  # must not raise

    def test_escaping_names_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            for chapter, character in (("..", "x"), (CHAPTER, "../../.."), ("/etc", "x")):
                with self.subTest(chapter=chapter, character=character):
                    with self.assertRaises(ManifestPathError):
                        manifest_path_for(tmp, chapter, character)


if __name__ == "__main__":
    unittest.main()
