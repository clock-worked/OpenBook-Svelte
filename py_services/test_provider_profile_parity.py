"""Cross-language parity vectors for TTS provider classification.

Reads the shared vector file tests/provider_profile_vectors.json and
asserts the Python registry (audio/provider_profile.py) matches it. The TS
side (classifyProvider in apps/desktop/src/lib/services/audioGenerator.ts)
asserts the same vectors in
apps/desktop/src/lib/services/__tests__/providerProfile.test.ts (vitest).
Keep both green when changing either side.
"""

import json
import unittest
from pathlib import Path

from audio.provider_profile import (
    UnknownProviderError,
    find_provider_profile,
    known_provider_keys,
    resolve_provider_profile,
)

VECTORS_PATH = Path(__file__).resolve().parent.parent / "tests" / "provider_profile_vectors.json"


def load_vectors() -> dict:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


class ProviderProfileParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = load_vectors()

    def test_registered_set_matches(self):
        self.assertEqual(sorted(known_provider_keys()), sorted(self.vectors["registered"]))

    def test_provider_vectors(self):
        for vector in self.vectors["providers"]:
            with self.subTest(provider=vector["provider"]):
                profile = resolve_provider_profile(vector["provider"])
                self.assertEqual(profile.name, vector["canonical"])
                self.assertEqual(profile.engine, vector["endpointKind"])
                self.assertEqual(profile.file_extension, vector["fileExtension"])

    def test_unknown_vectors(self):
        for provider in self.vectors["unknown"]:
            with self.subTest(provider=provider):
                self.assertIsNone(find_provider_profile(provider))
                with self.assertRaises(UnknownProviderError):
                    resolve_provider_profile(provider)

    def test_every_registered_key_has_a_vector(self):
        covered = {vector["provider"] for vector in self.vectors["providers"]}
        self.assertTrue(set(self.vectors["registered"]) <= covered)


if __name__ == "__main__":
    unittest.main()
