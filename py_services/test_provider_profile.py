"""Unit tests for the provider profile registry (audio/provider_profile.py).

Pure: imports only the registry, so it runs without fastapi or model deps.
The cross-language parity vectors live in test_provider_profile_parity.py.
"""

import unittest

from audio.provider_profile import (
    ADAPTERS,
    ENDPOINT_BY_ENGINE,
    ENGINE_CLOUD,
    ENGINE_KINDS,
    ENGINE_LOCAL_CHUNKED,
    PROVIDER_PROFILES,
    UnknownProviderError,
    find_provider_profile,
    is_chatterbox_provider,
    is_local_chunked_provider,
    known_provider_keys,
    resolve_provider_profile,
)

# Every provider string the pre-registry code accepted, by where it routed.
# (audio_generation_service dispatch + _get_audio_extension, and
# CHATTERBOX_PROVIDER_ALIASES.)
LEGACY_ROUTING = {
    "vibevoice_local": ("vibevoice", ".wav"),
    "vibevoice-local": ("vibevoice", ".wav"),
    "vibevoice": ("vibevoice", ".wav"),
    "chatterbox": ("chatterbox", ".wav"),
    "chatterbox_local": ("chatterbox", ".wav"),
    "chatterbox-local": ("chatterbox", ".wav"),
    "google_tts": ("google_tts", ".mp3"),
    "chirp3": ("google_tts", ".mp3"),
    "google": ("google_tts", ".mp3"),
    "chirp": ("google_tts", ".mp3"),
    "elevenlabs": ("elevenlabs", ".mp3"),
}


class ProviderProfileRegistryTests(unittest.TestCase):
    def test_every_legacy_alias_resolves_to_its_old_route(self):
        for provider, (adapter, extension) in LEGACY_ROUTING.items():
            with self.subTest(provider=provider):
                profile = resolve_provider_profile(provider)
                self.assertEqual(profile.adapter, adapter)
                self.assertEqual(profile.file_extension, extension)

    def test_registry_accepts_exactly_the_legacy_set(self):
        self.assertEqual(set(known_provider_keys()), set(LEGACY_ROUTING))

    def test_lookup_is_case_and_whitespace_insensitive(self):
        self.assertEqual(resolve_provider_profile("  VibeVoice_Local ").name, "vibevoice_local")
        self.assertEqual(resolve_provider_profile("CHATTERBOX").name, "chatterbox")

    def test_unknown_provider_raises_a_clear_error(self):
        for provider in ("", None, "openai", "vibevoice-1.5b", "chatterbox turbo"):
            with self.subTest(provider=provider):
                with self.assertRaises(UnknownProviderError) as ctx:
                    resolve_provider_profile(provider)
                self.assertEqual(str(ctx.exception), f"Unsupported TTS provider: {provider}")
                self.assertIsNone(find_provider_profile(provider))
        self.assertTrue(issubclass(UnknownProviderError, ValueError))

    def test_engine_kinds_and_endpoints(self):
        for profile in PROVIDER_PROFILES:
            with self.subTest(profile=profile.name):
                self.assertIn(profile.engine, ENGINE_KINDS)
                self.assertIn(profile.adapter, ADAPTERS)
                self.assertEqual(profile.endpoint, ENDPOINT_BY_ENGINE[profile.engine])
        self.assertEqual(resolve_provider_profile("vibevoice").engine, ENGINE_LOCAL_CHUNKED)
        self.assertEqual(resolve_provider_profile("chatterbox").engine, ENGINE_LOCAL_CHUNKED)
        self.assertEqual(resolve_provider_profile("google").engine, ENGINE_CLOUD)
        self.assertEqual(resolve_provider_profile("elevenlabs").engine, ENGINE_CLOUD)
        self.assertEqual(resolve_provider_profile("chatterbox").endpoint, "/api/vibevoice/line")
        self.assertEqual(resolve_provider_profile("elevenlabs").endpoint, "/api/generate_audio_line")

    def test_classification_predicates(self):
        self.assertTrue(is_local_chunked_provider("vibevoice_local"))
        self.assertTrue(is_local_chunked_provider("chatterbox-local"))
        self.assertFalse(is_local_chunked_provider("google_tts"))
        self.assertFalse(is_local_chunked_provider("unknown"))
        self.assertFalse(is_local_chunked_provider(None))
        self.assertTrue(is_chatterbox_provider(" Chatterbox_Local "))
        self.assertFalse(is_chatterbox_provider("vibevoice"))
        self.assertFalse(is_chatterbox_provider(None))

    def test_one_profile_per_adapter(self):
        adapters = [profile.adapter for profile in PROVIDER_PROFILES]
        self.assertEqual(sorted(adapters), sorted(ADAPTERS))


class AudioServiceDispatchTests(unittest.TestCase):
    """The service's adapter table covers every registry adapter.

    Needs the service's runtime deps (httpx, google-auth, model services);
    skipped where those are not installed.
    """

    def test_every_adapter_has_a_line_method(self):
        try:
            from audio_generation_service import AudioGenerationService
        except ImportError as exc:  # pragma: no cover - env-dependent
            self.skipTest(f"audio_generation_service deps unavailable: {exc}")
        self.assertEqual(set(AudioGenerationService._LINE_ADAPTER_METHODS), set(ADAPTERS))
        for method_name in AudioGenerationService._LINE_ADAPTER_METHODS.values():
            self.assertTrue(callable(getattr(AudioGenerationService, method_name)))


if __name__ == "__main__":
    unittest.main()
