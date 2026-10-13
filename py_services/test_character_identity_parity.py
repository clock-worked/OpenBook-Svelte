"""Cross-language parity vectors for the character identity primitives.

Reads the shared vector file tests/character_identity_vectors.json and
asserts the Python implementations (character_store.py) match the
TypeScript ones (apps/desktop/src/lib/services/characterIdentity.ts) on
every vector. The TS side asserts the same vectors in
apps/desktop/src/lib/services/__tests__/characterIdentity.test.ts (vitest).
Keep both green when changing either side.

Deliberately absent from the vector file (known TS-only allowances):
padded GUID strings (Python does not trim) and 0/false normalize values
(`or` vs `??`).
"""

import json
import unittest
from pathlib import Path

from character_store import is_valid_guid, sanitize_filename

VECTORS_PATH = Path(__file__).resolve().parent.parent / "tests" / "character_identity_vectors.json"


def load_vectors() -> dict:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def normalize_id(value) -> str:
    """Python-side normalize idiom (mirror of TS normalizeCharacterKey)."""
    return str(value or "").strip().lower()


class CharacterIdentityParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = load_vectors()

    def test_sanitize_vectors(self):
        for vector in self.vectors["sanitize"]:
            with self.subTest(title=vector["title"]):
                self.assertEqual(sanitize_filename(vector["title"]), vector["expected"])

    def test_normalize_vectors(self):
        for vector in self.vectors["normalize"]:
            with self.subTest(value=vector["value"]):
                self.assertEqual(normalize_id(vector["value"]), vector["expected"])

    def test_guid_vectors(self):
        for vector in self.vectors["guids"]:
            with self.subTest(value=vector["value"]):
                self.assertEqual(is_valid_guid(vector["value"]), vector["valid"])


if __name__ == "__main__":
    unittest.main()
