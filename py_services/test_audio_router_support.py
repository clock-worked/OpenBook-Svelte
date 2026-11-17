"""Unit tests for the shared audio-router plumbing (audio/router_support.py):
the cached-service ensurer and the generation-error -> HTTP status mapping.

The status mapping and the ensurer's happy paths are pure. The tests that
assert an HTTPException is raised need fastapi and skip without it.
"""

import os
import unittest

from audio.router_support import (
    AUDIO_ROOT_NOT_CONFIGURED_DETAIL,
    generation_error_status,
    make_audio_service_ensurer,
    raise_generation_failure,
)

try:
    from fastapi import HTTPException
except ImportError:  # pragma: no cover - env-dependent
    HTTPException = None

needs_fastapi = unittest.skipIf(HTTPException is None, "fastapi not installed")


class FakeService:
    def __init__(self, root: str) -> None:
        self.audio_root = root


class FakeRuntime:
    """Stand-in for api_runtime_state's audio-root/service getters+setters."""

    def __init__(self, audio_root: str = "", book_root: str = "", service=None) -> None:
        self.audio_root = audio_root
        self.book_root = book_root
        self.service = service
        self.created: list[str] = []

    def factory(self, root: str) -> FakeService:
        self.created.append(root)
        return FakeService(root)

    def set_service(self, root: str, service) -> None:
        self.audio_root = root
        self.service = service

    def ensurer(self):
        return make_audio_service_ensurer(
            get_audio_root=lambda: self.audio_root,
            get_book_root=lambda: self.book_root,
            get_audio_service=lambda: self.service,
            set_audio_service=self.set_service,
            service_factory=self.factory,
        )


class GenerationErrorStatusTests(unittest.TestCase):
    def test_client_errors_map_to_400(self):
        for message in (
            "Voice sample file could not be found for voice_id 'x'",
            "Sample not found",
            "voice_id is required",
            "Invalid voice name",
            "VibeVoice generation failed: Voice sample file could not be found",
            "NOT FOUND",
        ):
            with self.subTest(message=message):
                self.assertEqual(generation_error_status(message), 400)

    def test_everything_else_maps_to_500(self):
        for message in (
            "Unsupported TTS provider: bogus",
            "Chatterbox generation failed: CUDA out of memory",
            "Failed to generate audio from TTS provider (Unknown error)",
            "",
            None,
        ):
            with self.subTest(message=message):
                self.assertEqual(generation_error_status(message), 500)

    @needs_fastapi
    def test_raise_generation_failure_carries_status_and_detail(self):
        with self.assertRaises(HTTPException) as ctx:
            raise_generation_failure("voice_id is required")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "voice_id is required")

        with self.assertRaises(HTTPException) as ctx:
            raise_generation_failure("boom")
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.detail, "boom")


class EnsureAudioServiceTests(unittest.TestCase):
    def test_returns_the_cached_service_and_ignores_the_override(self):
        cached = FakeService("/cached")
        runtime = FakeRuntime(audio_root="/cached", service=cached)
        self.assertIs(runtime.ensurer()("/override"), cached)
        self.assertEqual(runtime.created, [])

    def test_creates_and_caches_a_service_once(self):
        runtime = FakeRuntime(book_root="/book")
        ensure = runtime.ensurer()
        first = ensure()
        second = ensure("/override")
        self.assertIs(first, second)
        self.assertEqual(runtime.created, [os.path.normpath("/book")])
        self.assertEqual(runtime.audio_root, os.path.normpath("/book"))

    def test_override_beats_book_root_when_creating(self):
        runtime = FakeRuntime(book_root="/book")
        service = runtime.ensurer()("/override/")
        self.assertEqual(service.audio_root, os.path.normpath("/override/"))

    @needs_fastapi
    def test_no_root_raises_400(self):
        runtime = FakeRuntime()
        with self.assertRaises(HTTPException) as ctx:
            runtime.ensurer()(None)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, AUDIO_ROOT_NOT_CONFIGURED_DETAIL)
        self.assertEqual(runtime.created, [])


if __name__ == "__main__":
    unittest.main()
