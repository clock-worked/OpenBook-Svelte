"""M17 (reader half): v2-era readers and v3 folder readers AGREE.

On the same logical book (the hermetic Book-9 fixture, shared shape with
test_character_folder_migration.py), after migrating the character store to
the v3.0 characters/ folder:

- jev_verify_service.load_character_lookup yields the identical name/alias
  -> canonical map pre/post (the id-derived key is the slug pre-migration
  and the GUID post-migration; names/aliases are the live surfaces);
- the closed-world catalog built from the folder equals the catalog built
  from the v2 file (GUIDs enter surface_to_name only as inert lookup
  surfaces, per docs/schema_characters_v3.md "Reference formats");
- update_character_stats on the migrated tree yields identical per-character
  stats (and the folder write is write-only-on-change).

Run from py_services/ with: python -m unittest openbook_parser.test_character_folder_reader
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from .booknlp_parser_service import build_closed_world_catalog
from .jev_verify_service import load_character_lookup

try:
    from ..character_store import GUID_PATTERN, load_character_folder
except ImportError:  # flat import when openbook_parser is top-level
    from character_store import GUID_PATTERN, load_character_folder

try:
    from ..update_character_stats import update_character_stats
except ImportError:
    from update_character_stats import update_character_stats

REPO_ROOT = Path(__file__).resolve().parents[2]
_AI_DIR = REPO_ROOT / "scripts" / "python" / "ai"
if str(_AI_DIR) not in sys.path:
    sys.path.insert(0, str(_AI_DIR))

import migrate_characters_v3 as migrate  # noqa: E402


BOOK_NAME = "Book-9"
V2_SLUGS = ("catherine", "cat-and-mouse", "the-bishop")


# ---------------------------------------------------------------------------
# Fixture (same logical book as test_character_folder_migration.py)
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_book(parent: Path) -> Path:
    book = parent / BOOK_NAME
    book.mkdir(parents=True, exist_ok=True)
    _write_json(
        book / "characters.json",
        {
            "formatVersion": "2.0",
            "characters": [
                {
                    "id": "catherine",
                    "name": "Catherine",
                    "gender": "Female",
                    "aliases": ["Kathy"],
                    "descriptors": ["tall", "blonde"],
                    "color": "#7C3AED",
                    "notes": "",
                    "firstAppearance": "chapter-1",
                    "stats": {"totalLines": 1, "chapterCount": 1},  # stale on purpose
                    "voice": None,
                    "roleLabels": ["protagonist"],
                },
                {
                    "id": "cat-and-mouse",
                    "name": "Cat and Mouse",
                    "gender": "Male",
                    "aliases": [],
                    "descriptors": ["witty"],
                    "color": None,
                    "notes": "",
                    "firstAppearance": "chapter-2",
                    "stats": {"totalLines": 1, "chapterCount": 1},
                    "voice": None,
                    "roleLabels": ["antagonist"],
                },
                {
                    "id": "the-bishop",
                    "name": "The Bishop",
                    "gender": "Unknown",
                    "aliases": ["Bishop"],
                    "descriptors": [],
                    "color": None,
                    "notes": "",
                    "firstAppearance": None,
                    "stats": {"totalLines": 0, "chapterCount": 0},
                    "voice": None,
                    "roleLabels": [],
                },
            ],
        },
    )
    _write_json(
        book / "chapter-1" / "dialogue.json",
        {
            "formatVersion": "3.2",
            "chapterId": "chapter-1",
            "lines": [
                {
                    "id": 1,
                    "characterId": "catherine",
                    "text": "We're not done.",
                    "candidates": [{"characterId": "cat-and-mouse", "confidence": 0.42}],
                    "attribution": {
                        "candidates": [
                            {"characterId": "catherine", "confidence": 0.9},
                            {"characterId": "the-bishop", "confidence": 0.1},
                        ]
                    },
                },
                {
                    "id": 2,
                    "characterId": "Cat and Mouse",
                    "text": "Try me.",
                    "candidates": [{"characterId": "Kathy", "confidence": 0.3}],
                },
                {"id": 3, "characterId": None, "text": "The room went quiet.", "candidates": []},
            ],
            "stats": {"characterBreakdown": {"catherine": 3, "cat-and-mouse": 1, "narrator": 5}},
        },
    )
    _write_json(
        book / "chapter-1" / "chapter-1.characters.json",
        {"formatVersion": "2.0", "characters": [{"id": "catherine"}, {"id": "cat-and-mouse"}]},
    )
    _write_json(
        book / "chapter-2" / "dialogue.json",
        {
            "formatVersion": "3.2",
            "chapterId": "chapter-2",
            "lines": [
                {
                    "id": 1,
                    "characterId": "the-bishop",
                    "text": "Kneel.",
                    "candidates": [{"characterId": "catherine", "confidence": 0.5}],
                },
                {
                    "id": 2,
                    "characterId": "catherine",
                    "text": "No.",
                    "attribution": {
                        "candidates": [
                            {"characterId": "the-bishop"},
                            {"characterId": "cat-and-mouse"},
                        ]
                    },
                },
            ],
            "stats": {"characterBreakdown": {"the-bishop": 2, "catherine": 1}},
        },
    )
    _write_json(
        book / "chapter-2" / "chapter-2.characters.json",
        {
            "formatVersion": "2.0",
            "characters": [{"id": "the-bishop"}, {"id": "catherine"}, {"id": "cat-and-mouse"}],
        },
    )
    _write_json(
        book / "voices.json",
        {
            "voices": [
                {
                    "id": "vc-1",
                    "name": "Catherine V",
                    "provider": "vibevoice",
                    "metadata": {"usedByCharacters": ["catherine", "cat-and-mouse"]},
                }
            ],
            "assignments": [
                {"characterId": "catherine", "voiceId": "vc-1"},
                {"characterId": "cat-and-mouse", "voiceId": "vc-1"},
            ],
        },
    )
    _write_json(
        book / "chapter-1" / "audio_lines" / "chapter-1" / "Catherine" / "manifest.json",
        {
            "chapterId": "chapter-1",
            "characterId": "catherine",
            "characterName": "Catherine",
            "clips": [
                {"lineId": 1, "characterId": "catherine", "characterName": "Catherine", "file": "0001.wav"}
            ],
        },
    )
    _write_json(book / "notes.json", {"note": "keep me"})
    return book


def _name_alias_surfaces(rows) -> set:
    """Lowercased name/alias display surfaces of a row list."""
    surfaces = set()
    for row in rows:
        for value in [row.get("name"), *(row.get("aliases") or [])]:
            key = str(value or "").strip().lower()
            if key:
                surfaces.add(key)
    return surfaces


def apply_migration(book: Path, parent: Path) -> None:
    backup_dir = parent / "backup"
    backup_dir.mkdir(exist_ok=True)
    plan = migrate.build_plan(book, "v2", book / "characters.json", False)
    if plan.violations:
        raise migrate.MigrationAbort("; ".join(plan.violations))
    migrate.apply_plan(book, plan, book / "characters.json", backup_dir)


def v2_rows(root: Path) -> list:
    """Closed-world catalog rows from the v2 file (parserCatalog.ts mapping)."""
    data = json.loads((root / "characters.json").read_text(encoding="utf-8-sig"))
    return [
        {
            "characterId": c["id"],
            "name": c["name"],
            "aliases": c.get("aliases") or [],
            "descriptors": c.get("descriptors") or [],
            "gender": c.get("gender") or "Unknown",
        }
        for c in data["characters"]
    ]


def folder_rows(root: Path) -> list:
    """Closed-world catalog rows from the v3 folder (same mapping)."""
    return [
        {
            "characterId": record["guid"],
            "name": record["title"],
            "aliases": record.get("aliases") or [],
            "descriptors": record.get("descriptors") or [],
            "gender": record.get("gender") or "Unknown",
        }
        for record in load_character_folder(root)
    ]


class FolderReaderEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_char_reader_test_")
        self.tmp = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_load_character_lookup_identical_pre_post(self):
        book = build_book(self.tmp)
        id_only = set(V2_SLUGS) - _name_alias_surfaces(v2_rows(book))
        before = load_character_lookup(book)
        apply_migration(book, self.tmp)
        after = load_character_lookup(book)

        # The name/alias -> canonical surfaces agree exactly. Keys that exist
        # ONLY as identity (slugs that are not also a display surface pre,
        # GUIDs post) are excluded; lookup keys are lowercased, so the GUID
        # pattern is matched case-insensitively.
        filtered_before = {k: v for k, v in before.items() if k not in id_only}
        filtered_after = {k: v for k, v in after.items() if not GUID_PATTERN.fullmatch(k.upper())}
        self.assertEqual(filtered_before, filtered_after)
        self.assertEqual(before["narrator"], after["narrator"])
        # Every v2 name/alias key survives in the post-migration map.
        for key, canonical in filtered_before.items():
            self.assertEqual(after.get(key), canonical)

    def test_closed_world_catalog_folder_equals_v2_file(self):
        book = build_book(self.tmp)
        rows = v2_rows(book)
        id_only = set(V2_SLUGS) - _name_alias_surfaces(rows)
        catalog_v2 = build_closed_world_catalog(rows)
        apply_migration(book, self.tmp)
        catalog_folder = build_closed_world_catalog(folder_rows(book))

        self.assertEqual(catalog_folder["names"], catalog_v2["names"])
        self.assertEqual(catalog_folder["gender_by_name"], catalog_v2["gender_by_name"])
        self.assertEqual(catalog_folder["descriptor_to_name"], catalog_v2["descriptor_to_name"])
        # Identity-only surfaces (slugs not also a display surface, GUIDs)
        # are excluded; keys are lowercased by the catalog builder.
        surfaces_v2 = {
            key: value for key, value in catalog_v2["surface_to_name"].items() if key not in id_only
        }
        surfaces_folder = {
            key: value
            for key, value in catalog_folder["surface_to_name"].items()
            if not GUID_PATTERN.fullmatch(key.upper())
        }
        self.assertEqual(surfaces_folder, surfaces_v2)

    def test_update_character_stats_identical_pre_post(self):
        book = build_book(self.tmp)

        # Pre-migration: refresh stats into the legacy characters.json.
        pre_result = update_character_stats(book)
        self.assertTrue(pre_result.get("success"))
        legacy_data = json.loads((book / "characters.json").read_text(encoding="utf-8"))
        before = {
            c["name"]: (c["stats"]["totalLines"], c["stats"]["chapterCount"])
            for c in legacy_data["characters"]
        }

        apply_migration(book, self.tmp)

        # Post-migration: the same refresh on the folder store.
        post_result = update_character_stats(book)
        self.assertTrue(post_result.get("success"))
        after = {}
        for path in sorted((book / "characters").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            after[payload["title"]] = (
                payload["stats"]["totalLines"],
                payload["stats"]["chapterCount"],
            )

        self.assertEqual(before, after)
        # The migration carried the refreshed stats verbatim: the folder pass
        # finds everything already current (write-only-on-change).
        self.assertEqual(post_result.get("updatedCharacters"), 0)


if __name__ == "__main__":
    unittest.main()
