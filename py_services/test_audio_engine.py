"""Tests for the TTS engine seam (chapter_audio/engine.py).

Pins what each engine adapter declares -- its provider-profile adapter name,
its per-call ``max_chunk_chars`` and the exact normalization it hears -- and
that both adapters satisfy the ``AudioEngine`` protocol through their
constructors (no model load, no ``object.__new__``).

Also pins the plan-time sub-split (``plan_engine_chunks``): its chunk counts
are the engine's true call counts.

Pure: no numpy / soundfile / torch (the Chatterbox adapter is built with a
fake model that is never called here).
"""

import tempfile
import unittest
from unittest import mock

from audio.engine_adapters import VoiceBoundEngine
from audio.provider_profile import (
    ADAPTER_CHATTERBOX,
    ADAPTER_VIBEVOICE,
    PROVIDER_PROFILES,
    UnknownProviderError,
    resolve_provider_profile,
)
from chapter_audio.engine import (
    AudioEngine,
    EngineChunkTooLongError,
    alignment_chunk_count,
    engine_text_pieces,
    plan_engine_chunks,
    require_within_engine_limit,
)
from chapter_audio.text_utils import TYPOGRAPHY_REPLACEMENTS, normalize_typography
from chatterbox_local_service import ChatterboxLocalService
from vibevoice_local_service import VibeVoiceLocalService, normalize_text


class FakeChatterboxModel:
    """Minimal in-process stand-in for the Chatterbox model (no GPU)."""

    def __init__(self, sr=24000):
        self.sr = sr
        self.conds = "prepared"
        self.calls = []

    def prepare_conditionals(self, path):
        pass

    def generate(self, text, repetition_penalty=1.2, temperature=0.8, **_kwargs):
        import torch

        self.calls.append(text)
        # 1 sample frame per character -> deterministic, tiny duration
        return torch.zeros(1, max(1, len(text)))


class _DeclaredEngine:
    """Declarations only (plan tests never call generate_audio)."""

    adapter = "fake"

    def __init__(self, max_chunk_chars):
        self.max_chunk_chars = max_chunk_chars

    @staticmethod
    def normalize(text):
        return ChatterboxLocalService.normalize(text)

    def generate_audio(self, text, sample_path, output_path, seed=None):
        raise AssertionError("not called by plan tests")


def _alignment_chunk(index, text, start_line=1, end_line=3, line_count=3):
    return {
        "chunkIndex": index,
        "startLine": start_line,
        "endLine": end_line,
        "lineCount": line_count,
        "charCount": len(text),
        "text": text,
    }


# Strings that exercise every rule of both normalizers.
NORMALIZER_CORPUS = [
    "“Don’t,” she said — quietly…",
    "He�s here – [???] lvl. 4 +1,000 (bonus): [Skill]",
    "Stats: (+5), plus 3 ... and   more , ok ?",
    "a ... , b",
    "",
    "   ",
]


class TypographyPrimitiveTests(unittest.TestCase):
    def test_replacement_set_is_pinned(self):
        self.assertEqual(
            TYPOGRAPHY_REPLACEMENTS,
            {
                "‘": "'",
                "’": "'",
                "“": '"',
                "”": '"',
                "–": "-",
                "—": "-",
                "…": "...",
                "�": "'",
            },
        )

    def test_whitespace_untouched(self):
        self.assertEqual(normalize_typography("  a’b  \n"), "  a'b  \n")


class ChatterboxNormalizeTests(unittest.TestCase):
    def test_typography_and_whitespace(self):
        self.assertEqual(
            ChatterboxLocalService.normalize("“Don’t”  —  go…\n"),
            "\"Don't\" - go...",
        )

    def test_corrupted_quote_becomes_apostrophe(self):
        self.assertEqual(ChatterboxLocalService.normalize("He�s"), "He's")

    def test_no_vibevoice_rules(self):
        # Brackets, "lvl" and "+N" are VibeVoice rules, not Chatterbox's.
        self.assertEqual(
            ChatterboxLocalService.normalize("[Skill] lvl +5"),
            "[Skill] lvl +5",
        )

    def test_idempotent(self):
        for text in NORMALIZER_CORPUS:
            once = ChatterboxLocalService.normalize(text)
            self.assertEqual(ChatterboxLocalService.normalize(once), once, text)


class VibeVoiceNormalizeTests(unittest.TestCase):
    def test_adapter_normalize_is_module_normalize_text(self):
        self.assertIs(VibeVoiceLocalService.normalize, normalize_text)

    def test_rules_pinned(self):
        cases = {
            "“Don’t,” she said": "\"Don't.\" she said",
            "He�s here": "He's here",
            "a [???] b": "a unknown b",
            "lvl 4": "level 4",
            "+1,000 gold": "plus 1,000 gold",
            "Skill: [Fire]": "Skill ... Fire ...",
            "a - b": "a ... b",
            "1, plus 2": "1 ... plus 2",
            "wait , what ?": "wait, what?",
        }
        for source, expected in cases.items():
            self.assertEqual(VibeVoiceLocalService.normalize(source), expected, source)

    def test_idempotent(self):
        for text in NORMALIZER_CORPUS:
            once = VibeVoiceLocalService.normalize(text)
            self.assertEqual(VibeVoiceLocalService.normalize(once), once, text)


class AdapterDeclarationTests(unittest.TestCase):
    def test_adapters_are_named_by_the_provider_profile(self):
        self.assertEqual(ChatterboxLocalService.adapter, ADAPTER_CHATTERBOX)
        self.assertEqual(VibeVoiceLocalService.adapter, ADAPTER_VIBEVOICE)
        self.assertEqual(
            resolve_provider_profile("chatterbox").adapter, ChatterboxLocalService.adapter
        )
        self.assertEqual(
            resolve_provider_profile("vibevoice_local").adapter, VibeVoiceLocalService.adapter
        )

    def test_every_local_chunked_profile_has_an_engine_adapter(self):
        engine_adapters = {ChatterboxLocalService.adapter, VibeVoiceLocalService.adapter}
        local = {p.adapter for p in PROVIDER_PROFILES if p.is_local_chunked}
        self.assertEqual(local, engine_adapters)

    def test_declared_limits(self):
        self.assertEqual(ChatterboxLocalService.DEFAULT_MAX_CHUNK_CHARS, 450)
        svc = ChatterboxLocalService(in_process_model=FakeChatterboxModel())
        self.assertEqual(svc.max_chunk_chars, 450)
        self.assertIsNone(VibeVoiceLocalService.max_chunk_chars)

    def test_both_services_satisfy_the_protocol(self):
        chatterbox = ChatterboxLocalService(in_process_model=FakeChatterboxModel())
        vibevoice = VibeVoiceLocalService()  # lazy: no model load until generate
        self.assertIsInstance(chatterbox, AudioEngine)
        self.assertIsInstance(vibevoice, AudioEngine)


class EngineTextPiecesTests(unittest.TestCase):
    def test_normalizes_then_splits_to_the_declared_limit(self):
        engine = _DeclaredEngine(max_chunk_chars=50)
        text = "\u201cOne.\u201d   " + "word " * 30
        pieces = engine_text_pieces(engine, text)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece), 50)
            self.assertNotIn("\u201c", piece)
        self.assertEqual(" ".join(pieces).split(), engine.normalize(text).split())

    def test_unbounded_engine_is_one_normalized_piece(self):
        engine = _DeclaredEngine(max_chunk_chars=None)
        self.assertEqual(engine_text_pieces(engine, "a\u2019b   " * 300), [engine.normalize("a\u2019b   " * 300)])

    def test_limit_is_checked_on_normalized_length(self):
        engine = _DeclaredEngine(max_chunk_chars=5)
        require_within_engine_limit(engine, "ab    cd")  # "ab cd" fits
        with self.assertRaises(EngineChunkTooLongError) as ctx:
            require_within_engine_limit(engine, "abcdef")
        self.assertEqual((ctx.exception.length, ctx.exception.max_chunk_chars), (6, 5))
        require_within_engine_limit(_DeclaredEngine(None), "x" * 10_000)


class PlanEngineChunksTests(unittest.TestCase):
    def test_sub_split_gives_true_counts_and_parent_links(self):
        engine = _DeclaredEngine(max_chunk_chars=450)
        long_text = " ".join(f"Sentence number {i} is here." for i in range(40))  # ~1100 chars
        plan = [
            _alignment_chunk(1, long_text, 1, 4, 4),
            _alignment_chunk(2, "Short tail.", 5, 5, 1),
        ]
        engine_plan = plan_engine_chunks(plan, engine)

        expected_first = engine_text_pieces(engine, long_text)
        self.assertEqual(len(engine_plan), len(expected_first) + 1)
        self.assertEqual([c["chunkIndex"] for c in engine_plan], list(range(1, len(engine_plan) + 1)))
        for chunk in engine_plan:
            self.assertLessEqual(chunk["charCount"], 450)
            self.assertEqual(chunk["charCount"], len(chunk["text"]))
        first = [c for c in engine_plan if c["alignmentChunkIndex"] == 1]
        self.assertEqual([c["text"] for c in first], expected_first)
        self.assertEqual([c["subChunkIndex"] for c in first], list(range(1, len(first) + 1)))
        self.assertTrue(all(c["subChunkCount"] == len(first) for c in first))
        self.assertTrue(all((c["startLine"], c["endLine"], c["lineCount"]) == (1, 4, 4) for c in first))
        self.assertEqual(
            engine_plan[-1],
            {
                "chunkIndex": len(engine_plan),
                "startLine": 5,
                "endLine": 5,
                "lineCount": 1,
                "charCount": len("Short tail."),
                "text": "Short tail.",
                "alignmentChunkIndex": 2,
                "subChunkIndex": 1,
                "subChunkCount": 1,
            },
        )
        self.assertEqual(alignment_chunk_count(engine_plan), 2)

    def test_unbounded_engine_keeps_one_chunk_per_alignment_chunk(self):
        engine = _DeclaredEngine(max_chunk_chars=None)
        plan = [_alignment_chunk(1, "x " * 900), _alignment_chunk(2, "y " * 900)]
        engine_plan = plan_engine_chunks(plan, engine)
        self.assertEqual(len(engine_plan), 2)
        self.assertEqual([c["text"] for c in engine_plan], [engine.normalize(c["text"]) for c in plan])

    def test_alignment_chunk_count_of_an_unsplit_plan(self):
        plan = [_alignment_chunk(1, "a"), _alignment_chunk(2, "b")]
        self.assertEqual(alignment_chunk_count(plan), 2)

    def test_chatterbox_adapter_plans_to_its_declared_limit(self):
        svc = ChatterboxLocalService(in_process_model=FakeChatterboxModel())
        plan = [_alignment_chunk(1, "word " * 200)]
        engine_plan = plan_engine_chunks(plan, svc)
        self.assertEqual(len(engine_plan), 3)
        self.assertTrue(all(c["charCount"] <= 450 for c in engine_plan))


class VoiceBoundEngineTests(unittest.TestCase):
    def _engine(self, result=(True, None), voice_id="narrator.wav"):
        calls = []

        def generate_to_path(*, text, voice_id, output_path):
            calls.append((text, voice_id, output_path))
            return result

        engine = VoiceBoundEngine(
            adapter=ADAPTER_CHATTERBOX,
            display_name="Chatterbox",
            max_chunk_chars=450,
            normalize=ChatterboxLocalService.normalize,
            voice_id=voice_id,
            generate_to_path=generate_to_path,
        )
        return engine, calls

    def test_is_an_audio_engine_with_the_engine_declarations(self):
        engine, _ = self._engine()
        self.assertIsInstance(engine, AudioEngine)
        self.assertEqual(engine.adapter, ADAPTER_CHATTERBOX)
        self.assertEqual(engine.max_chunk_chars, 450)
        self.assertEqual(engine.normalize("a\u2019b  c"), "a'b c")

    def test_bound_voice_wins_over_sample_path(self):
        engine, calls = self._engine()
        engine.generate_audio(text="Hi.", sample_path="other.wav", output_path="out/1.wav", seed=7)
        self.assertEqual(len(calls), 1)
        text, voice_id, output_path = calls[0]
        self.assertEqual((text, voice_id, output_path.as_posix()), ("Hi.", "narrator.wav", "out/1.wav"))

    def test_sample_path_used_when_no_voice_bound(self):
        engine, calls = self._engine(voice_id="")
        engine.generate_audio(text="Hi.", sample_path="sample.wav", output_path="o.wav")
        self.assertEqual(calls[0][1], "sample.wav")

    def test_failure_raises_with_details_or_fallback(self):
        engine, _ = self._engine(result=(False, "voice sample not found"))
        with self.assertRaisesRegex(RuntimeError, "voice sample not found"):
            engine.generate_audio(text="Hi.", sample_path="s", output_path="o.wav")
        engine, _ = self._engine(result=(False, None))
        with self.assertRaisesRegex(RuntimeError, "^Chatterbox generation failed.$"):
            engine.generate_audio(text="Hi.", sample_path="s", output_path="o.wav")


try:  # the composition root needs httpx + google-auth
    from audio_generation_service import AudioGenerationService
except ImportError:  # pragma: no cover - depends on the environment
    AudioGenerationService = None


@unittest.skipIf(AudioGenerationService is None, "audio_generation_service deps not installed")
class CompositionRootWiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        with mock.patch.object(AudioGenerationService, "_load_google_credentials"):
            self.service = AudioGenerationService(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_local_provider_gets_its_profile_named_adapter(self):
        chatterbox = self.service.audio_engine("chatterbox_local", voice_id="v", voice_sample_root=None)
        vibevoice = self.service.audio_engine("vibevoice", voice_id="v", voice_sample_root=None)
        self.assertEqual((chatterbox.adapter, chatterbox.max_chunk_chars), (ADAPTER_CHATTERBOX, 450))
        self.assertEqual((vibevoice.adapter, vibevoice.max_chunk_chars), (ADAPTER_VIBEVOICE, None))
        self.assertIsInstance(chatterbox, AudioEngine)
        self.assertIsInstance(vibevoice, AudioEngine)

    def test_cloud_and_unknown_providers_have_no_engine(self):
        with self.assertRaises(ValueError):
            self.service.audio_engine("google_tts", voice_id="v", voice_sample_root=None)
        with self.assertRaises(UnknownProviderError):
            self.service.audio_engine("not-a-provider", voice_id="v", voice_sample_root=None)

    def test_chatterbox_engine_routes_to_the_chatterbox_path_with_settings(self):
        settings = {"variant": "turbo", "temperature": 0.5}
        engine = self.service.audio_engine(
            "chatterbox", voice_id="v.wav", voice_sample_root="/samples", chatterbox_settings=settings
        )
        with mock.patch.object(
            self.service, "generate_chatterbox_text_to_path", return_value=(True, None)
        ) as chatterbox_path, mock.patch.object(
            self.service, "generate_vibevoice_text_to_path"
        ) as vibevoice_path:
            engine.generate_audio(text="Hi.", sample_path="v.wav", output_path="o.wav")
        vibevoice_path.assert_not_called()
        kwargs = chatterbox_path.call_args.kwargs
        self.assertEqual(kwargs["voice_sample_root"], "/samples")
        self.assertEqual(kwargs["settings"], settings)
        self.assertEqual(kwargs["voice_id"], "v.wav")

    def test_vibevoice_engine_routes_to_the_vibevoice_path(self):
        engine = self.service.audio_engine("vibevoice_local", voice_id="v.wav", voice_sample_root=None)
        with mock.patch.object(
            self.service, "generate_vibevoice_text_to_path", return_value=(False, "boom")
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                engine.generate_audio(text="Hi.", sample_path="v.wav", output_path="o.wav")


if __name__ == "__main__":
    unittest.main()
