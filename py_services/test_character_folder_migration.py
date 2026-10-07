"""T-MIG migration safety suite for scripts/python/ai/migrate_characters_v3.py.

Implements the M1-M17 table in docs/character_details_test_plan.md plus the
v1 staged path and the R11 case, on a hermetic "Book-9" fixture (stdlib
unittest + tempfile.TemporaryDirectory, house pattern).

Shared fixture: 3 clusters in root characters.json (aliases, gender, color,
descriptors, roleLabels, voice fields, stats), 2 chapter dirs (dialogue.json
populated at ALL FOUR reference sites: lines[].characterId,
lines[].candidates[].characterId, lines[].attribution.candidates[].characterId,
stats.characterBreakdown; plus rosters), voices.json (assignments +
metadata.usedByCharacters), audio_lines/**/manifest.json (top-level +
per-clip characterId), and decoys (root notes.json, one binary file).

GUID source (test plan: "GUID source injectable"): the ops script takes no
injected mint, but its mint is already a pure deterministic function of
(book name, v2 id) - SHA-256("<book> ::v3::<id>") -> first 5 bytes ->
8-char Crockford base32 (docs/schema_characters_v3.md, GUID spec). The tests
therefore compute every expected GUID with that same formula (guid_from_bytes
imported from character_store) instead of a counter. Where the test plan
requires byte-identical output across two fresh trees (M2), the wall clock is
frozen (both the script's datetime and character_store's, since
write_character stamps updatedAt on every write) so the comparison is over
the fully deterministic bytes.

diff_tree(before, after) is shared across the suite: every mutation test
asserts changed paths == the expected set (the test plan's single property).
"""

import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
_AI_DIR = REPO_ROOT / "scripts" / "python" / "ai"
if str(_AI_DIR) not in sys.path:
    sys.path.insert(0, str(_AI_DIR))

import migrate_characters_v3 as migrate  # noqa: E402

from character_store import (  # noqa: E402
    FORMAT_VERSION,
    GUID_PATTERN,
    guid_from_bytes,
    is_valid_guid,
    sanitize_filename,
)


BOOK_NAME = "Book-9"
V2_SLUGS = ("catherine", "cat-and-mouse", "the-bishop")


def expected_guid(v2_id: str) -> str:
    """The script's deterministic mint, recomputed for expectations."""
    digest = hashlib.sha256(f"{BOOK_NAME}::v3::{v2_id}".encode("utf-8")).digest()
    return guid_from_bytes(digest[:5])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def tree_map(root: Path, exclude_dirs=()) -> dict:
    """path (relative) -> sha256 for every file under root."""
    mapping = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in exclude_dirs for part in rel.parts):
            continue
        mapping[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return mapping


def diff_tree(before: dict, after: dict) -> dict:
    """path -> (before_sha, after_sha) for every path that changed membership
    or bytes. Empty dict == byte-identical trees."""
    changed = {}
    for path in set(before) | set(after):
        before_hash, after_hash = before.get(path), after.get(path)
        if before_hash != after_hash:
            changed[path] = (before_hash, after_hash)
    return changed


def collect_strings(node, out: list) -> None:
    """Every string value and dict key in a decoded JSON document."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            collect_strings(key, out)
            collect_strings(value, out)
    elif isinstance(node, list):
        for item in node:
            collect_strings(item, out)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class FrozenDatetime:
    """Wall-clock stand-in so M2 compares fully deterministic bytes
    (write_character stamps updatedAt from its own datetime import, so both
    the script's and the store's datetime must be frozen)."""

    @staticmethod
    def now(tz=None):
        return datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def apply_migration(book_root: Path, backup_dir: Path, drop_unresolved: bool = False,
                    mode: str = "v2") -> "migrate.MigrationPlan":
    """Run stages 1-5 directly (stages 0 is the CLI's job)."""
    legacy = book_root / (migrate.LEGACY_V2_FILENAME if mode == "v2" else migrate.LEGACY_V1_FILENAME)
    plan = migrate.build_plan(book_root, mode, legacy, drop_unresolved)
    if plan.violations:
        raise migrate.MigrationAbort("; ".join(plan.violations))
    migrate.apply_plan(book_root, plan, legacy, backup_dir)
    return plan


def _name_alias_surfaces(rows) -> set:
    """Lowercased name/alias display surfaces of a row list."""
    surfaces = set()
    for row in rows:
        for value in [row.get("name"), *(row.get("aliases") or [])]:
            key = str(value or "").strip().lower()
            if key:
                surfaces.add(key)
    return surfaces


def run_cli(book_root: Path, *extra: str):
    """Invoke the script's main() with a synthetic argv. Returns (exit_code, stdout)."""
    argv = ["migrate_characters_v3.py", str(book_root), *extra]
    out = io.StringIO()
    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
        try:
            migrate.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def v2_characters_doc() -> dict:
    return {
        "formatVersion": "2.0",
        "characters": [
            {
                "id": "catherine",
                "name": "Catherine",
                "gender": "Female",
                "aliases": ["Kathy"],
                "descriptors": ["tall", "blonde"],
                "race": "human",
                "color": "#7C3AED",
                "notes": "protagonist notes",
                "firstAppearance": "chapter-1",
                "stats": {"totalLines": 1, "chapterCount": 1},  # stale on purpose (M17)
                "voice": "catherine-voice",
                "provider": "vibevoice",
                "voiceId": "vc-1",
                "voiceMeta": {"sampleRate": 24000},
                "manifestStats": {"clips": 4},
                "roleLabels": ["protagonist"],
            },
            {
                "id": "cat-and-mouse",
                "name": "Cat and Mouse",
                "gender": "Male",
                "aliases": [],
                "descriptors": ["witty"],
                "race": None,
                "color": None,
                "notes": "",
                "firstAppearance": "chapter-2",
                "stats": {"totalLines": 1, "chapterCount": 1},
                "voice": None,
                "provider": None,
                "voiceId": None,
                "voiceMeta": None,
                "manifestStats": None,
                "roleLabels": ["antagonist"],
            },
            {
                "id": "the-bishop",
                "name": "The Bishop",
                "gender": "Unknown",
                "aliases": ["Bishop"],
                "descriptors": [],
                "race": None,
                "color": None,
                "notes": "",
                "firstAppearance": None,
                "stats": {"totalLines": 0, "chapterCount": 0},
                "voice": None,
                "provider": None,
                "voiceId": None,
                "voiceMeta": None,
                "manifestStats": None,
                "roleLabels": [],
            },
        ],
    }


def _write_json(path: Path, payload, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if bom:
        text = "\ufeff" + text
    path.write_text(text, encoding="utf-8")


def chapter1_dialogue() -> dict:
    # Populated at all four reference sites; sites resolve by slug, by name,
    # and by alias; null and narrator refs must stay untouched.
    return {
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
                "characterId": "Cat and Mouse",  # name, not slug
                "text": "Try me.",
                "candidates": [{"characterId": "Kathy", "confidence": 0.3}],  # alias
            },
            {"id": 3, "characterId": None, "text": "The room went quiet.", "candidates": []},
            {
                "id": 4,
                "characterId": "narrator",
                "text": "It should not have.",
                "attribution": {"candidates": []},
            },
        ],
        "stats": {"characterBreakdown": {"catherine": 3, "cat-and-mouse": 1, "narrator": 5}},
    }


def chapter2_dialogue() -> dict:
    return {
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
    }


def build_book(parent: Path, characters_doc: dict | None = None, bom: bool = False,
               refs: bool = True) -> Path:
    """Hermetic Book-9. Returns the book root (named Book-9 - the mint inputs
    on the book name). refs=False omits the reference files (dialogues,
    rosters, voices, manifests) for store-only tests."""
    book = parent / BOOK_NAME
    book.mkdir(parents=True, exist_ok=True)
    _write_json(book / "characters.json", characters_doc or v2_characters_doc(), bom=bom)
    if not refs:
        _write_json(book / "notes.json", {"note": "keep me"})
        return book
    _write_json(book / "chapter-1" / "dialogue.json", chapter1_dialogue())
    _write_json(
        book / "chapter-1" / "chapter-1.characters.json",
        {"formatVersion": "2.0", "characters": [{"id": "catherine"}, {"id": "cat-and-mouse"}]},
    )
    _write_json(book / "chapter-2" / "dialogue.json", chapter2_dialogue())
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
                {"lineId": 1, "characterId": "catherine", "characterName": "Catherine", "file": "0001.wav"},
                {"lineId": 4, "characterId": "narrator", "characterName": "narrator", "file": "0004.wav"},
            ],
        },
    )
    # Decoys: must never change.
    _write_json(book / "notes.json", {"note": "keep me"})
    (book / "cover.bin").write_bytes(b"\x89PNG\r\n\x00binary-decoy")
    return book


# The exact allowlist of paths one migration of the shared fixture may touch
# (backup dir kept outside the book root in these tests).
EXPECTED_CHANGED = {
    "characters.json",  # retired (deleted)
    "characters/Catherine.json",
    "characters/Cat and Mouse.json",
    "characters/The Bishop.json",
    "chapter-1/dialogue.json",
    "chapter-2/dialogue.json",
    "chapter-1/chapter-1.characters.json",
    "chapter-2/chapter-2.characters.json",
    "voices.json",
    "chapter-1/audio_lines/chapter-1/Catherine/manifest.json",
}


class MigrationV2Tests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_char_mig_test_")
        self.tmp = Path(self._temp_dir.name)
        self.backup_dir = self.tmp / "backup"  # outside the book root
        self.backup_dir.mkdir()

    def tearDown(self):
        self._temp_dir.cleanup()

    # -- M1 ----------------------------------------------------------------

    def test_m1_split_preserves_every_v2_field(self):
        book = build_book(self.tmp)
        before = tree_map(book)
        plan = apply_migration(book, self.backup_dir)

        folder = book / "characters"
        self.assertEqual(
            sorted(p.name for p in folder.glob("*.json")),
            ["Cat and Mouse.json", "Catherine.json", "The Bishop.json"],
        )
        self.assertFalse((book / "characters.json").exists())

        # Every v2 field present with unchanged value (iterated, not spot-checked).
        for record in v2_characters_doc()["characters"]:
            payload = read_json(folder / f"{sanitize_filename(record['name'])}.json")
            self.assertEqual(payload["formatVersion"], FORMAT_VERSION)
            self.assertEqual(payload["guid"], expected_guid(record["id"]))
            self.assertNotIn("id", payload)  # v2 slug retired
            for key, value in record.items():
                if key == "id":
                    continue
                target = "title" if key == "name" else key
                self.assertEqual(payload.get(target), value, f"{key} -> {target}")

        # Mint order: firstAppearance (nulls last) then title.
        self.assertEqual(
            [guid for _record, guid, _name in plan.clusters],
            [expected_guid("catherine"), expected_guid("cat-and-mouse"), expected_guid("the-bishop")],
        )

        # No stray files: changed paths are exactly the allowlist.
        changed = diff_tree(before, tree_map(book))
        self.assertEqual(set(changed), EXPECTED_CHANGED)

    # -- M2 ----------------------------------------------------------------

    def test_m2_deterministic_output(self):
        book_a = build_book(self.tmp / "a")
        book_b = build_book(self.tmp / "b")
        store_pkg = sys.modules["py_services.character_store"]
        with mock.patch.object(migrate, "datetime", FrozenDatetime), \
                mock.patch.object(store_pkg, "datetime", FrozenDatetime):
            apply_migration(book_a, self.tmp / "backup_a")
            apply_migration(book_b, self.tmp / "backup_b")

        # Same logical book, same (deterministic) GUID sequence -> identical bytes.
        self.assertEqual(diff_tree(tree_map(book_a), tree_map(book_b)), {})
        expected = {
            "Catherine.json": ("Catherine", expected_guid("catherine")),
            "Cat and Mouse.json": ("Cat and Mouse", expected_guid("cat-and-mouse")),
            "The Bishop.json": ("The Bishop", expected_guid("the-bishop")),
        }
        for name, (title, guid) in expected.items():
            payload = read_json(book_a / "characters" / name)
            self.assertEqual((payload["title"], payload["guid"]), (title, guid))

    # -- M3 ----------------------------------------------------------------

    def test_m3_mint_properties_1000_draws(self):
        from character_store import mint_guid

        draws = [mint_guid() for _ in range(1000)]
        self.assertEqual(len(set(draws)), 1000)
        for guid in draws:
            self.assertTrue(is_valid_guid(guid))

    # -- M4 ----------------------------------------------------------------

    def test_m4_guids_stable_across_runs(self):
        book = build_book(self.tmp)
        apply_migration(book, self.backup_dir)
        first = {
            read_json(p)["title"]: read_json(p)["guid"]
            for p in sorted((book / "characters").glob("*.json"))
        }

        code, out = run_cli(book, "--apply")  # second run on the migrated tree

        self.assertEqual(code, 0)
        self.assertIn("Already migrated", out)
        second = {
            read_json(p)["title"]: read_json(p)["guid"]
            for p in sorted((book / "characters").glob("*.json"))
        }
        self.assertEqual(first, second)
        self.assertEqual(second, {
            "Catherine": expected_guid("catherine"),
            "Cat and Mouse": expected_guid("cat-and-mouse"),
            "The Bishop": expected_guid("the-bishop"),
        })

    # -- M5 ----------------------------------------------------------------

    def test_m5_remap_dialogue_sites(self):
        book = build_book(self.tmp)
        apply_migration(book, self.backup_dir)
        g = {slug: expected_guid(slug) for slug in V2_SLUGS}

        d1 = read_json(book / "chapter-1" / "dialogue.json")
        self.assertEqual(d1["lines"][0]["characterId"], g["catherine"])
        self.assertEqual(d1["lines"][0]["candidates"][0]["characterId"], g["cat-and-mouse"])
        self.assertEqual(
            [c["characterId"] for c in d1["lines"][0]["attribution"]["candidates"]],
            [g["catherine"], g["the-bishop"]],
        )
        self.assertEqual(d1["lines"][1]["characterId"], g["cat-and-mouse"])  # was a name
        self.assertEqual(d1["lines"][1]["candidates"][0]["characterId"], g["catherine"])  # was an alias
        self.assertIsNone(d1["lines"][2]["characterId"])  # null stays null
        self.assertEqual(d1["lines"][3]["characterId"], "narrator")  # sentinel stays
        self.assertEqual(d1["stats"]["characterBreakdown"],
                         {g["catherine"]: 3, g["cat-and-mouse"]: 1, "narrator": 5})

        d2 = read_json(book / "chapter-2" / "dialogue.json")
        self.assertEqual(d2["lines"][0]["characterId"], g["the-bishop"])
        self.assertEqual(d2["lines"][1]["characterId"], g["catherine"])
        self.assertEqual(
            [c["characterId"] for c in d2["lines"][1]["attribution"]["candidates"]],
            [g["the-bishop"], g["cat-and-mouse"]],
        )
        self.assertEqual(set(d2["stats"]["characterBreakdown"]),
                         {g["the-bishop"], g["catherine"]})

        # Zero slugs at every dialogue site.
        for doc in (d1, d2):
            strings: list = []
            collect_strings(doc, strings)
            for slug in V2_SLUGS:
                self.assertNotIn(slug, strings)

    # -- M6 ----------------------------------------------------------------

    def test_m6_remap_rosters(self):
        book = build_book(self.tmp)
        apply_migration(book, self.backup_dir)
        g = {slug: expected_guid(slug) for slug in V2_SLUGS}

        r1 = read_json(book / "chapter-1" / "chapter-1.characters.json")
        self.assertEqual(r1["formatVersion"], FORMAT_VERSION)
        self.assertEqual(r1["characters"], [{"id": g["catherine"]}, {"id": g["cat-and-mouse"]}])

        r2 = read_json(book / "chapter-2" / "chapter-2.characters.json")
        self.assertEqual(r2["formatVersion"], FORMAT_VERSION)
        # Every entry a GUID; membership unchanged.
        self.assertEqual({e["id"] for e in r2["characters"]},
                         {g["catherine"], g["cat-and-mouse"], g["the-bishop"]})
        for entry in r2["characters"]:
            self.assertTrue(is_valid_guid(entry["id"]))

    # -- M7 ----------------------------------------------------------------

    def test_m7_remap_voices(self):
        book = build_book(self.tmp)
        apply_migration(book, self.backup_dir)
        g = {slug: expected_guid(slug) for slug in V2_SLUGS}

        voices = read_json(book / "voices.json")
        self.assertEqual([a["characterId"] for a in voices["assignments"]],
                         [g["catherine"], g["cat-and-mouse"]])
        # Assignment fields untouched.
        self.assertEqual([a["voiceId"] for a in voices["assignments"]], ["vc-1", "vc-1"])
        self.assertEqual(voices["voices"][0]["metadata"]["usedByCharacters"],
                         [g["catherine"], g["cat-and-mouse"]])
        self.assertEqual(voices["voices"][0]["id"], "vc-1")

    # -- M8 ----------------------------------------------------------------

    def test_m8_remap_manifests_dirs_unchanged(self):
        book = build_book(self.tmp)
        apply_migration(book, self.backup_dir)
        g = {slug: expected_guid(slug) for slug in V2_SLUGS}

        manifest = read_json(book / "chapter-1" / "audio_lines" / "chapter-1" / "Catherine" / "manifest.json")
        self.assertEqual(manifest["characterId"], g["catherine"])
        self.assertEqual(manifest["clips"][0]["characterId"], g["catherine"])
        self.assertEqual(manifest["clips"][1]["characterId"], "narrator")
        # characterName stays a name (R8).
        self.assertEqual(manifest["characterName"], "Catherine")
        self.assertEqual(manifest["clips"][0]["characterName"], "Catherine")
        # Directory names unchanged (R8).
        self.assertTrue((book / "chapter-1" / "audio_lines" / "chapter-1" / "Catherine").is_dir())

    # -- M9 ----------------------------------------------------------------

    def test_m9_zero_stale_refs_tree_wide(self):
        book = build_book(self.tmp)
        plan = apply_migration(book, self.backup_dir)

        # (a) Reference-site scan: zero old slugs in every inventoried file.
        for path in sorted(plan.inventory):
            strings: list = []
            collect_strings(read_json(path), strings)
            for slug in V2_SLUGS:
                self.assertNotIn(slug, strings, f"{path}: stale slug {slug}")

        # (b) Stronger: recursive walk of ALL string values in the tree
        #     (excl. _backups): zero exact-equal to any old slug.
        for rel, _hash in tree_map(book, exclude_dirs=("_backups",)).items():
            path = book / rel
            try:
                payload = json.loads(path.read_bytes())
            except (ValueError, UnicodeDecodeError):
                continue  # binary decoy
            strings: list = []
            collect_strings(payload, strings)
            for slug in V2_SLUGS:
                self.assertNotIn(slug, strings, f"{rel}: stale slug {slug}")

    # -- M10 ---------------------------------------------------------------

    def test_m10_second_run_is_noop(self):
        book = build_book(self.tmp)
        in_book_backup = book / "_backups" / "character-v3" / "run1"
        apply_migration(book, in_book_backup)
        after_first = tree_map(book)

        code, out = run_cli(book, "--apply")  # second run, default backup dir

        self.assertEqual(code, 0)
        self.assertIn("Already migrated", out)
        self.assertEqual(diff_tree(after_first, tree_map(book)), {})
        # No second backup.
        self.assertEqual([p.name for p in (book / "_backups" / "character-v3").iterdir()], ["run1"])

    # -- M11 ---------------------------------------------------------------

    def test_m11_backup_exists_and_matches(self):
        book = build_book(self.tmp)
        before = tree_map(book)
        apply_migration(book, self.backup_dir)
        after = tree_map(book)

        for rel, (before_hash, _after_hash) in diff_tree(before, after).items():
            if before_hash is None:
                continue  # new character file: no pre-image by definition
            pre_image = self.backup_dir / rel
            self.assertTrue(pre_image.is_file(), f"missing backup pre-image for {rel}")
            self.assertEqual(hashlib.sha256(pre_image.read_bytes()).hexdigest(), before_hash, rel)
        # The retired characters.json is included.
        self.assertTrue((self.backup_dir / "characters.json").is_file())

    # -- M12 ---------------------------------------------------------------

    def test_m12_unrelated_files_untouched(self):
        book = build_book(self.tmp)
        before = tree_map(book)
        apply_migration(book, self.backup_dir)
        after = tree_map(book)

        # Changed paths == exact allowlist (decoys and everything else absent).
        self.assertEqual(set(diff_tree(before, after)), EXPECTED_CHANGED)
        # Decoys byte-identical.
        self.assertEqual(before["notes.json"], after["notes.json"])
        self.assertEqual(before["cover.bin"], after["cover.bin"])

    # -- M13 ---------------------------------------------------------------

    def test_m13_kill_mid_migration_is_recoverable(self):
        book = build_book(self.tmp)
        before = tree_map(book)
        real_write = migrate.write_character
        calls = {"n": 0}

        def exploding_writer(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:  # kill after the 2nd of 3 character writes
                raise OSError("injected kill mid-migration")
            return real_write(*args, **kwargs)

        with mock.patch.object(migrate, "write_character", exploding_writer):
            with self.assertRaises(OSError):
                apply_migration(book, self.backup_dir)

        # No torn file: every JSON in the tree parses.
        for path in book.rglob("*.json"):
            with self.subTest(path=str(path)):
                json.loads(path.read_bytes())
        # Legacy store intact (kill happened before stage 5c); 2 of 3 files written.
        self.assertTrue((book / "characters.json").is_file())
        self.assertEqual(len(list((book / "characters").glob("*.json"))), 2)

        # Backup-restore reproduces the pre-migration tree byte-for-byte.
        restore_from_backup(book, self.backup_dir)
        self.assertEqual(diff_tree(before, tree_map(book)), {})

    # -- M14 ---------------------------------------------------------------

    def test_m14_dry_run_is_the_default(self):
        book = build_book(self.tmp)
        before = tree_map(book)

        code, out = run_cli(book)  # no --apply

        self.assertEqual(code, 0)
        self.assertIn("Dry run complete", out)
        self.assertIn("characters/Catherine.json", out)  # report returned
        self.assertEqual(diff_tree(before, tree_map(book)), {})
        self.assertFalse((book / "_backups").exists())  # zero writes incl. no backup dir

    # -- M15 ---------------------------------------------------------------

    def test_m15_duplicate_exact_titles_aborts_with_zero_writes(self):
        doc = v2_characters_doc()
        dupe = dict(doc["characters"][0])
        dupe["id"] = "catherine-2"
        doc["characters"].append(dupe)
        book = build_book(self.tmp, characters_doc=doc)
        before = tree_map(book)

        code, out = run_cli(book, "--apply", "--backup-dir", str(self.backup_dir))

        self.assertNotEqual(code, 0)
        self.assertIn("Filename collision", out)
        self.assertIn("Catherine", out)  # names the collision
        self.assertEqual(diff_tree(before, tree_map(book)), {})
        self.assertFalse((book / "characters").exists())

    def test_m15_case_variant_titles_aborts_with_zero_writes(self):
        doc = v2_characters_doc()
        variant = dict(doc["characters"][0])
        variant["id"] = "catherine-2"
        variant["name"] = "catherine"  # case variant
        doc["characters"].append(variant)
        book = build_book(self.tmp, characters_doc=doc)
        before = tree_map(book)

        code, out = run_cli(book, "--apply", "--backup-dir", str(self.backup_dir))

        self.assertNotEqual(code, 0)
        self.assertIn("Filename collision", out)
        self.assertIn("'Catherine'", out)
        self.assertIn("'catherine'", out)
        self.assertEqual(diff_tree(before, tree_map(book)), {})

    def test_m15_unresolvable_dialogue_ref_aborts_with_zero_writes(self):
        book = build_book(self.tmp)
        dialogue = read_json(book / "chapter-1" / "dialogue.json")
        dialogue["lines"].append({"id": 99, "characterId": "ghost", "text": "???"})
        _write_json(book / "chapter-1" / "dialogue.json", dialogue)
        before = tree_map(book)

        code, out = run_cli(book, "--apply", "--backup-dir", str(self.backup_dir))

        self.assertNotEqual(code, 0)
        self.assertIn("Validation failed", out)
        self.assertIn("ghost", out)
        self.assertEqual(diff_tree(before, tree_map(book)), {})

    def test_m15_unresolvable_roster_aborts_unless_drop_flag(self):
        book = build_book(self.tmp)
        roster = read_json(book / "chapter-1" / "chapter-1.characters.json")
        roster["characters"].append({"id": "ghost"})
        _write_json(book / "chapter-1" / "chapter-1.characters.json", roster)
        before = tree_map(book)

        code, out = run_cli(book, "--apply", "--backup-dir", str(self.backup_dir))
        self.assertNotEqual(code, 0)
        self.assertIn("ghost", out)
        self.assertEqual(diff_tree(before, tree_map(book)), {})

        # With --drop-unresolved the entry is dropped and migration proceeds.
        apply_migration(book, self.backup_dir, drop_unresolved=True)
        g = {slug: expected_guid(slug) for slug in V2_SLUGS}
        roster = read_json(book / "chapter-1" / "chapter-1.characters.json")
        self.assertEqual([e["id"] for e in roster["characters"]], [g["catherine"], g["cat-and-mouse"]])

    # -- M16 ---------------------------------------------------------------

    def test_m16_bom_and_non_ascii_titles_round_trip(self):
        doc = {
            "formatVersion": "2.0",
            "characters": [
                {"id": "cafe", "name": "Caf\u00e9", "gender": "Female", "aliases": []},
                {"id": "em-dash", "name": "Em \u2014 Dash", "gender": "Male", "aliases": []},
                {"id": "wang-mei", "name": "\u738b\u6885", "gender": "Female", "aliases": []},
            ],
        }
        book = build_book(self.tmp, characters_doc=doc, bom=True, refs=False)
        apply_migration(book, self.backup_dir)

        folder = book / "characters"
        self.assertEqual(
            sorted(p.name for p in folder.glob("*.json")),
            ["Caf\u00e9.json", "Em \u2014 Dash.json", "\u738b\u6885.json"],
        )
        for path in sorted(folder.glob("*.json")):
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM in output {path.name}")
            payload = json.loads(raw)  # UTF-8, no BOM
            self.assertTrue(is_valid_guid(payload["guid"]))
        self.assertEqual(read_json(folder / "Caf\u00e9.json")["title"], "Caf\u00e9")
        self.assertEqual(read_json(folder / "Em \u2014 Dash.json")["title"], "Em \u2014 Dash")
        self.assertEqual(read_json(folder / "\u738b\u6885.json")["title"], "\u738b\u6885")

    # -- M17 ---------------------------------------------------------------

    def test_m17_consumer_equivalence_lookup(self):
        from openbook_parser.jev_verify_service import load_character_lookup

        book = build_book(self.tmp)
        before = load_character_lookup(book)
        apply_migration(book, self.backup_dir)
        after = load_character_lookup(book)

        # name/alias -> canonical surfaces agree exactly. Keys that exist
        # ONLY as identity (slugs that are not also a display surface pre,
        # GUIDs post) are excluded; lookup keys are lowercased, so the GUID
        # pattern is matched case-insensitively.
        rows = v2_characters_doc()["characters"]
        id_only = set(V2_SLUGS) - _name_alias_surfaces(rows)
        filtered_before = {k: v for k, v in before.items() if k not in id_only}
        filtered_after = {k: v for k, v in after.items() if not GUID_PATTERN.fullmatch(k.upper())}
        self.assertEqual(filtered_before, filtered_after)
        self.assertEqual(before["narrator"], after["narrator"])

    def test_m17_consumer_equivalence_closed_world_catalog(self):
        from openbook_parser.booknlp_parser_service import build_closed_world_catalog
        from openbook_parser.knowledge_store import load_character_folder

        def v2_rows(root: Path):
            data = read_json(root / "characters.json")
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

        def folder_rows(root: Path):
            return [
                {
                    "characterId": r["guid"],
                    "name": r["title"],
                    "aliases": r.get("aliases") or [],
                    "descriptors": r.get("descriptors") or [],
                    "gender": r.get("gender") or "Unknown",
                }
                for r in load_character_folder(root)
            ]

        book = build_book(self.tmp)
        rows = v2_rows(book)
        id_only = set(V2_SLUGS) - _name_alias_surfaces(rows)
        catalog_v2 = build_closed_world_catalog(rows)
        apply_migration(book, self.backup_dir)
        catalog_folder = build_closed_world_catalog(folder_rows(book))

        self.assertEqual(catalog_folder["names"], catalog_v2["names"])
        self.assertEqual(catalog_folder["gender_by_name"], catalog_v2["gender_by_name"])
        self.assertEqual(catalog_folder["descriptor_to_name"], catalog_v2["descriptor_to_name"])
        # Identity-only surfaces (slugs not also a display surface, GUIDs)
        # are excluded; keys are lowercased by the catalog builder.
        surfaces_v2 = {
            k: v for k, v in catalog_v2["surface_to_name"].items() if k not in id_only
        }
        surfaces_folder = {
            k: v
            for k, v in catalog_folder["surface_to_name"].items()
            if not GUID_PATTERN.fullmatch(k.upper())
        }
        self.assertEqual(surfaces_folder, surfaces_v2)

    def test_m17_consumer_equivalence_stats(self):
        from update_character_stats import update_character_stats

        book = build_book(self.tmp)
        pre_result = update_character_stats(book)
        self.assertTrue(pre_result.get("success"))

        def legacy_stats(root: Path):
            data = read_json(root / "characters.json")
            return {
                c["name"]: (c["stats"]["totalLines"], c["stats"]["chapterCount"])
                for c in data["characters"]
            }

        before = legacy_stats(book)
        apply_migration(book, self.backup_dir)

        post_result = update_character_stats(book)
        self.assertTrue(post_result.get("success"))

        def folder_stats(root: Path):
            stats = {}
            for path in sorted((root / "characters").glob("*.json")):
                payload = read_json(path)
                stats[payload["title"]] = (
                    payload["stats"]["totalLines"],
                    payload["stats"]["chapterCount"],
                )
            return stats

        after = folder_stats(book)
        self.assertEqual(before, after)
        # The migrated stats already match the dialogue: write-only-on-change.
        self.assertEqual(post_result.get("updatedCharacters"), 0)


def restore_from_backup(book_root: Path, backup_dir: Path) -> None:
    """Copy every backed-up pre-image back over the book and drop partial
    writes that have no pre-image (new character files)."""
    for path in sorted(backup_dir.rglob("*")):
        if not path.is_file():
            continue
        target = book_root / path.relative_to(backup_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    folder = book_root / "characters"
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            if not (backup_dir / path.relative_to(book_root)).is_file():
                path.unlink()


# ---------------------------------------------------------------------------
# v1 staged path + R11
# ---------------------------------------------------------------------------


def v1_speakers() -> list:
    return [
        {"name": "Mara", "gender": "f", "aliases": ["M."], "color": "#FF0000",
         "voice": None, "first_seen_chapter": "chapter-1"},
        {"name": "Dorian", "gender": "m", "aliases": [], "color": None,
         "voice": None, "first_seen_chapter": None},
        {"name": "Rook", "gender": "u", "aliases": [], "color": None,
         "voice": None, "first_seen_chapter": None},
    ]


V1_SLUGS = ("mara", "dorian", "rook")


def build_v1_book(parent: Path, script_chapter: bool = False, ghost_dialogue: bool = False) -> Path:
    book = parent / BOOK_NAME
    book.mkdir(parents=True, exist_ok=True)
    _write_json(book / "book.characters.json", {"characters": v1_speakers()})
    dialogue = {
        "formatVersion": "3.2",
        "chapterId": "chapter-1",
        "lines": [
            {"id": 1, "characterId": "mara", "text": "Hello."},
            {"id": 2, "characterId": "Dorian", "text": "Hi."},
            {"id": 3, "characterId": None, "text": "Narration."},
        ],
        "stats": {"characterBreakdown": {"mara": 2, "Dorian": 1}},
    }
    if ghost_dialogue:
        dialogue["lines"].append({"id": 99, "characterId": "Phantom", "text": "??"})
    _write_json(book / "chapter-1" / "dialogue.json", dialogue)
    _write_json(
        book / "chapter-1" / "chapter-1.characters.json",
        {"formatVersion": "2.0", "characters": [{"id": "mara"}]},
    )
    if script_chapter:
        # R11: v1-script-only chapter (name-based; no characterId slot).
        _write_json(
            book / "chapter-2" / "chapter-2.script.json",
            {"lines": [{"chosenSpeaker": "Mara", "text": "Script line."}]},
        )
    _write_json(book / "notes.json", {"note": "keep me"})
    return book


class MigrationV1Tests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_char_mig_v1_test_")
        self.tmp = Path(self._temp_dir.name)
        self.backup_dir = self.tmp / "backup"
        self.backup_dir.mkdir()

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_v1_speakers_become_one_cluster_each(self):
        book = build_v1_book(self.tmp)
        plan = apply_migration(book, self.backup_dir, mode="v1")

        folder = book / "characters"
        self.assertEqual(
            sorted(p.name for p in folder.glob("*.json")),
            ["Dorian.json", "Mara.json", "Rook.json"],
        )
        self.assertFalse((book / "book.characters.json").exists())
        # One cluster per speaker, slug id per id_candidates() (candidates[0]).
        self.assertEqual(set(plan.remap), set(V1_SLUGS))
        for slug, title in (("mara", "Mara"), ("dorian", "Dorian"), ("rook", "Rook")):
            payload = read_json(folder / f"{title}.json")
            self.assertEqual(payload["formatVersion"], FORMAT_VERSION)
            self.assertEqual(payload["guid"], expected_guid(slug))
            self.assertEqual(payload["title"], title)

    def test_v1_gender_mapping_bijective(self):
        book = build_v1_book(self.tmp)
        apply_migration(book, self.backup_dir, mode="v1")

        genders = {
            read_json(p)["title"]: read_json(p)["gender"]
            for p in sorted((book / "characters").glob("*.json"))
        }
        self.assertEqual(genders, {"Mara": "Female", "Dorian": "Male", "Rook": "Unknown"})

    def test_v1_full_pipeline_invariants(self):
        # M1/M9/M12 invariants hold on the v1 staged path.
        book = build_v1_book(self.tmp)
        before = tree_map(book)
        apply_migration(book, self.backup_dir, mode="v1")
        after = tree_map(book)

        expected_changed = {
            "book.characters.json",
            "characters/Mara.json",
            "characters/Dorian.json",
            "characters/Rook.json",
            "chapter-1/dialogue.json",
            "chapter-1/chapter-1.characters.json",
        }
        self.assertEqual(set(diff_tree(before, after)), expected_changed)
        self.assertEqual(before["notes.json"], after["notes.json"])

        # Zero stale slugs tree-wide (exact string values).
        for rel, _hash in tree_map(book, exclude_dirs=("_backups",)).items():
            try:
                payload = json.loads((book / rel).read_bytes())
            except (ValueError, UnicodeDecodeError):
                continue
            strings: list = []
            collect_strings(payload, strings)
            for slug in V1_SLUGS:
                self.assertNotIn(slug, strings, f"{rel}: stale slug {slug}")

    def test_v1_unresolved_reference_aborts_by_default(self):
        book = build_v1_book(self.tmp, ghost_dialogue=True)
        before = tree_map(book)

        code, out = run_cli(book, "--apply", "--backup-dir", str(self.backup_dir))

        self.assertNotEqual(code, 0)
        self.assertIn("Phantom", out)
        self.assertEqual(diff_tree(before, tree_map(book)), {})
        self.assertFalse((book / "characters").exists())

    def test_v1_second_run_is_byte_identical_noop(self):
        book = build_v1_book(self.tmp)
        in_book_backup = book / "_backups" / "character-v3" / "run1"
        apply_migration(book, in_book_backup, mode="v1")
        after_first = tree_map(book)

        code, out = run_cli(book, "--apply")

        self.assertEqual(code, 0)
        self.assertIn("Already migrated", out)
        self.assertEqual(diff_tree(after_first, tree_map(book)), {})

    def test_v1_script_only_chapters_flagged_and_left_name_based(self):
        # R11: the store migrates; v1 script files stay name-based; the dry
        # run flags each such chapter.
        book = build_v1_book(self.tmp, script_chapter=True)
        script_before = (book / "chapter-2" / "chapter-2.script.json").read_bytes()

        code, out = run_cli(book)  # dry run

        self.assertEqual(code, 0)
        self.assertIn("v1-script-only", out)
        self.assertIn("chapter-2", out)
        self.assertFalse((book / "characters").exists())  # dry run wrote nothing

        apply_migration(book, self.backup_dir, mode="v1")
        self.assertEqual(
            (book / "chapter-2" / "chapter-2.script.json").read_bytes(), script_before
        )
        self.assertEqual(
            sorted(p.name for p in (book / "characters").glob("*.json")),
            ["Dorian.json", "Mara.json", "Rook.json"],
        )


if __name__ == "__main__":
    unittest.main()
