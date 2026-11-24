"""Unit tests for the character audio manifest store (audio/manifest_store.py):
upsert, remove, reconcile (incl. orphan bootstrap) and corrupt-file tolerance.

Pure: needs no fastapi / soundfile.
"""

import json
import tempfile
import unittest
from pathlib import Path

from audio.manifest_store import (
    FORMAT_VERSION,
    build_clip,
    manifest_path_for,
    read_manifest,
    reconcile,
    recompute_metadata,
    remove_clip,
    upsert_clip,
    upsert_clips,
)

CHAPTER = "Chapter 1"
CHARACTER = "Alice Liddell"


def make_clip(line_id, *, voice="v1", chapter=CHAPTER, source="ch1.txt", **extra):
    return build_clip(
        line_id=line_id,
        character_id="alice",
        character_name=CHARACTER,
        text=f"line {line_id}",
        audio_file=f"{line_id}-{CHARACTER}.wav",
        chapter=chapter,
        source_file=source,
        voice_id=voice,
        provider="vibevoice_local",
        duration=1.5,
        **extra,
    )


class ManifestStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = manifest_path_for(self.root, CHAPTER, CHARACTER)
        self.char_dir = self.path.parent

    def tearDown(self):
        self._tmp.cleanup()

    def touch_audio(self, name):
        self.char_dir.mkdir(parents=True, exist_ok=True)
        (self.char_dir / name).write_bytes(b"\0")

    def on_disk(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


class BuildClipTests(unittest.TestCase):
    def test_generator_fields_are_always_present(self):
        clip = make_clip(1)
        for key in ("variant", "temperature", "cfgWeight", "exaggeration"):
            self.assertIn(key, clip)
            self.assertIsNone(clip[key])

    def test_generator_info_and_timing_are_recorded(self):
        clip = make_clip(
            1,
            generator_info={"variant": "turbo", "temperature": 0.7, "cfgWeight": None, "exaggeration": 0.5},
            timing={"startSec": 1.0, "endSec": 2.5},
        )
        self.assertEqual(clip["variant"], "turbo")
        self.assertEqual(clip["temperature"], 0.7)
        self.assertEqual(clip["metadata"]["startSec"], 1.0)
        self.assertEqual(clip["metadata"]["endSec"], 2.5)
        self.assertEqual(clip["metadata"]["duration"], 1.5)
        self.assertTrue(clip["metadata"]["generatedAt"].endswith("Z"))


class UpsertTests(ManifestStoreTestCase):
    def test_creates_manifest_with_v2_schema(self):
        manifest = upsert_clip(self.path, make_clip(3))
        self.assertEqual(manifest, self.on_disk())
        self.assertEqual(manifest["formatVersion"], FORMAT_VERSION)
        self.assertEqual(manifest["characterId"], "alice")
        self.assertEqual(manifest["characterName"], CHARACTER)
        meta = manifest["metadata"]
        self.assertEqual(meta["totalClips"], 1)
        self.assertEqual(meta["chapters"], [CHAPTER])
        self.assertEqual(meta["sources"], {"ch1.txt": 1})
        self.assertEqual(meta["voiceIds"], {"v1": 1})
        self.assertEqual(meta["primaryVoiceId"], "v1")
        self.assertTrue(meta["lastUpdated"])

    def test_replaces_same_id_without_double_counting(self):
        upsert_clip(self.path, make_clip(1))
        upsert_clip(self.path, make_clip(2))
        manifest = upsert_clip(self.path, make_clip(1, voice="v2"))
        self.assertEqual([c["id"] for c in manifest["clips"]], [1, 2])
        self.assertEqual(manifest["metadata"]["totalClips"], 2)
        self.assertEqual(manifest["metadata"]["sources"], {"ch1.txt": 2})
        self.assertEqual(manifest["metadata"]["voiceIds"], {"v1": 1, "v2": 1})
        self.assertEqual(manifest["clips"][0]["voiceId"], "v2")

    def test_batch_upsert_sorts_by_chapter_then_id(self):
        upsert_clips(
            self.path,
            [make_clip(5), make_clip(2), make_clip(9, chapter="A chapter")],
            character_id="alice",
            character_name=CHARACTER,
            voice_id="v1",
        )
        clips = self.on_disk()["clips"]
        self.assertEqual([(c["chapter"], c["id"]) for c in clips], [("A chapter", 9), (CHAPTER, 2), (CHAPTER, 5)])
        self.assertEqual(self.on_disk()["metadata"]["chapters"], ["A chapter", CHAPTER])

    def test_primary_voice_is_kept_while_still_used(self):
        upsert_clip(self.path, make_clip(1, voice="v1"))
        upsert_clips(
            self.path,
            [make_clip(2, voice="v2"), make_clip(3, voice="v2")],
            character_id="alice",
            character_name=CHARACTER,
            voice_id="v2",
        )
        self.assertEqual(self.on_disk()["metadata"]["primaryVoiceId"], "v1")
        # Once no clip uses v1 the most-used voice takes over.
        manifest = upsert_clip(self.path, make_clip(1, voice="v2"))
        self.assertEqual(manifest["metadata"]["primaryVoiceId"], "v2")

    def test_corrupt_manifest_is_replaced_not_fatal(self):
        self.char_dir.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        manifest = upsert_clip(self.path, make_clip(4))
        self.assertEqual([c["id"] for c in manifest["clips"]], [4])
        self.assertEqual(self.on_disk()["metadata"]["totalClips"], 1)

    def test_non_object_manifest_is_replaced(self):
        self.char_dir.mkdir(parents=True)
        self.path.write_text("[1, 2]", encoding="utf-8")
        manifest = upsert_clip(self.path, make_clip(4))
        self.assertEqual(manifest["formatVersion"], FORMAT_VERSION)


class ReadTests(ManifestStoreTestCase):
    def test_missing_corrupt_and_non_object_read_as_none(self):
        self.assertIsNone(read_manifest(self.path))
        self.char_dir.mkdir(parents=True)
        self.path.write_text("{oops", encoding="utf-8")
        self.assertIsNone(read_manifest(self.path))
        self.path.write_text('"text"', encoding="utf-8")
        self.assertIsNone(read_manifest(self.path))

    def test_recompute_metadata_tolerates_missing_sections(self):
        manifest = {"clips": "bogus"}
        meta = recompute_metadata(manifest, fallback_primary_voice_id="fallback")
        self.assertEqual(manifest["clips"], [])
        self.assertEqual(meta["totalClips"], 0)
        self.assertEqual(meta["primaryVoiceId"], "fallback")


class RemoveTests(ManifestStoreTestCase):
    def test_remove_recomputes_metadata(self):
        upsert_clip(self.path, make_clip(1, voice="v1"))
        upsert_clip(self.path, make_clip(2, voice="v2", source="other.txt"))
        self.assertTrue(remove_clip(self.path, 2))
        meta = self.on_disk()["metadata"]
        self.assertEqual(meta["totalClips"], 1)
        self.assertEqual(meta["sources"], {"ch1.txt": 1})
        self.assertEqual(meta["voiceIds"], {"v1": 1})

    def test_remove_unknown_id_or_missing_manifest_is_noop(self):
        self.assertFalse(remove_clip(self.path, 1))
        upsert_clip(self.path, make_clip(1))
        before = self.path.read_text(encoding="utf-8")
        self.assertFalse(remove_clip(self.path, 99))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)


class ReconcileTests(ManifestStoreTestCase):
    def test_missing_character_dir(self):
        result = reconcile(self.root, CHAPTER, CHARACTER)
        self.assertEqual(
            result,
            {"updated": False, "added_count": 0, "removed_count": 0, "clip_count": 0, "manifest": None},
        )

    def test_empty_dir_without_manifest_writes_nothing(self):
        self.char_dir.mkdir(parents=True)
        result = reconcile(self.root, CHAPTER, CHARACTER)
        self.assertFalse(result["updated"])
        self.assertIsNone(result["manifest"])
        self.assertFalse(self.path.exists())

    def test_bootstraps_manifest_from_orphan_files(self):
        self.touch_audio(f"2-{CHARACTER}.mp3")
        self.touch_audio(f"2-{CHARACTER}.wav")
        self.touch_audio(f"10-{CHARACTER}.mp3")
        self.touch_audio("notes.wav")  # no line-id prefix: ignored
        result = reconcile(self.root, CHAPTER, CHARACTER)
        self.assertTrue(result["updated"])
        self.assertEqual(result["added_count"], 2)
        self.assertEqual(result["clip_count"], 2)
        manifest = self.on_disk()
        self.assertEqual(result["manifest"], manifest)
        self.assertEqual([c["id"] for c in manifest["clips"]], [2, 10])
        self.assertEqual(manifest["clips"][0]["audioFile"], f"2-{CHARACTER}.wav")  # wav preferred
        self.assertEqual(manifest["characterId"], "alice_liddell")
        self.assertEqual(manifest["clips"][0]["provider"], "vibevoice_local")
        self.assertEqual(manifest["metadata"]["sources"], {CHAPTER: 2})
        self.assertEqual(manifest["metadata"]["voiceIds"], {})
        self.assertIsNone(manifest["metadata"]["primaryVoiceId"])

    def test_corrupt_manifest_is_rebuilt_from_files(self):
        self.touch_audio(f"1-{CHARACTER}.wav")
        self.path.write_text("{broken", encoding="utf-8")
        result = reconcile(self.root, CHAPTER, CHARACTER)
        self.assertTrue(result["updated"])
        self.assertEqual([c["id"] for c in self.on_disk()["clips"]], [1])

    def test_drops_missing_and_invalid_clips_and_adds_orphans(self):
        self.touch_audio(f"1-{CHARACTER}.wav")
        self.touch_audio(f"3-{CHARACTER}.wav")
        upsert_clips(
            self.path,
            [make_clip(1), make_clip(2)],  # 2 has no audio on disk
            character_id="alice",
            character_name=CHARACTER,
            voice_id="v1",
        )
        manifest = self.on_disk()
        manifest["clips"].append({"id": "x", "audioFile": "1-x.wav"})  # invalid id
        self.path.write_text(json.dumps(manifest), encoding="utf-8")

        result = reconcile(self.root, CHAPTER, CHARACTER)
        self.assertTrue(result["updated"])
        self.assertEqual(result["removed_count"], 2)
        self.assertEqual(result["added_count"], 1)
        clips = self.on_disk()["clips"]
        self.assertEqual([c["id"] for c in clips], [1, 3])
        self.assertEqual(clips[0]["text"], "line 1")  # kept clip untouched
        self.assertEqual(clips[1]["text"], "")  # bootstrapped placeholder
        self.assertEqual(self.on_disk()["metadata"]["totalClips"], 2)

    def test_in_sync_manifest_is_not_rewritten(self):
        self.touch_audio(f"1-{CHARACTER}.wav")
        upsert_clip(self.path, make_clip(1))
        before = self.path.read_text(encoding="utf-8")
        result = reconcile(self.root, CHAPTER, CHARACTER)
        self.assertFalse(result["updated"])
        self.assertEqual(result["clip_count"], 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_follows_wav_when_both_extensions_exist(self):
        self.touch_audio(f"1-{CHARACTER}.mp3")
        mp3_clip = make_clip(1)
        mp3_clip["audioFile"] = f"1-{CHARACTER}.mp3"
        upsert_clip(self.path, mp3_clip)
        self.touch_audio(f"1-{CHARACTER}.wav")
        result = reconcile(self.root, CHAPTER, CHARACTER)
        self.assertEqual(result["manifest"]["clips"][0]["audioFile"], f"1-{CHARACTER}.wav")


if __name__ == "__main__":
    unittest.main()
