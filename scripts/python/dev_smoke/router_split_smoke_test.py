#!/usr/bin/env python3
"""
Smoke test for Lane 5 router split.

Validates that parser/audio/vibevoice routers are registered and that key
lightweight endpoints still work after splitting `api_server.py`.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

from _path_setup import bootstrap_python_script_paths

bootstrap_python_script_paths(__file__)

from fastapi.testclient import TestClient

from py_services.api_runtime_state import runtime_state
from py_services.api_server import app


EXPECTED_ROUTES = {
    ("POST", "/api/set-book-root"),
    ("POST", "/api/set-audio-root"),
    ("GET", "/api/list-chapters"),
    ("POST", "/api/save"),
    ("POST", "/api/update-character-stats"),
    ("POST", "/api/parse"),
    ("POST", "/api/coref-health"),
    ("POST", "/api/list-voice-samples"),
    ("POST", "/api/read_file_absolute"),
    ("POST", "/api/generate_audio_line"),
    ("POST", "/api/delete_character_audio"),
    ("POST", "/api/delete_audio_line"),
    ("GET", "/api/audio/{chapter_title}/{character_name}/{line_id}"),
    ("POST", "/api/scan-voice-manifests"),
    ("POST", "/api/vibevoice/line"),
    ("POST", "/api/vibevoice/character"),
    ("POST", "/api/vibevoice/chapter"),
}


def assert_status(response, expected: int, context: str) -> None:
    if response.status_code != expected:
        raise AssertionError(
            f"{context} failed. Expected {expected}, got {response.status_code}. Body={response.text}"
        )


def assert_has_routes() -> None:
    actual = {
        (method, route.path)
        for route in app.routes
        for method in (route.methods or set())
        if method in {"GET", "POST"}
    }
    missing = EXPECTED_ROUTES - actual
    if missing:
        raise AssertionError(f"Missing expected routes: {sorted(missing)}")


def run_smoke() -> None:
    runtime_state.reset()
    assert_has_routes()

    try:
        with tempfile.TemporaryDirectory(prefix="openbook_lane5_smoke_") as temp_dir:
            root = Path(temp_dir)
            chapter_dir = root / "01-Chapter-1-Test"
            chapter_dir.mkdir(parents=True, exist_ok=True)
            (chapter_dir / "chapter.txt").write_text("Hello there.", encoding="utf-8")

            sample_root = root / "Audio Samples"
            sample_root.mkdir(parents=True, exist_ok=True)
            (sample_root / "narrator.wav").write_bytes(b"RIFF")

            manifest_path = root / "manifest.json"
            manifest_payload = {"formatVersion": "2.0", "ok": True}
            manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

            with TestClient(app) as client:
                response = client.get("/api/audio/TestChapter/Narrator/1")
                assert_status(response, 400, "audio-without-root")

                response = client.post("/api/set-book-root", json={"root_path": str(root)})
                assert_status(response, 200, "set-book-root")

                response = client.post("/api/set-audio-root", json={"audio_root": str(root)})
                assert_status(response, 200, "set-audio-root")

                response = client.get("/api/list-chapters")
                assert_status(response, 200, "list-chapters")
                payload = response.json()
                if payload.get("count") != 1:
                    raise AssertionError(f"Expected 1 chapter, got payload={payload}")

                response = client.post("/api/list-voice-samples", json={"samples_root": str(sample_root)})
                assert_status(response, 200, "list-voice-samples")
                if "narrator.wav" not in (response.json().get("samples") or []):
                    raise AssertionError(f"Expected narrator.wav in samples, got payload={response.json()}")

                response = client.post("/api/read_file_absolute", json={"file_path": str(manifest_path)})
                assert_status(response, 200, "read_file_absolute")
                if response.json().get("ok") is not True:
                    raise AssertionError(f"Expected manifest payload, got={response.json()}")

                response = client.get("/api/audio/TestChapter/Narrator/999")
                assert_status(response, 404, "audio-missing-file")

                response = client.get("/api/audio/%2E%2E/Narrator/1")
                assert_status(response, 403, "audio-path-traversal-guard")

                response = client.post("/api/vibevoice/line", json={})
                if response.status_code != 422:
                    raise AssertionError(
                        "Expected 422 validation response from /api/vibevoice/line for empty request body. "
                        f"Got {response.status_code} body={response.text}"
                    )
    finally:
        runtime_state.reset()


if __name__ == "__main__":
    run_smoke()
    print("Lane 5 router split smoke test passed")
