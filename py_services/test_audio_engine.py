"""Tests for the TTS engine seam (chapter_audio/engine.py).

Pins what each engine adapter declares -- its provider-profile adapter name,
its per-call ``max_chunk_chars`` and the exact normalization it hears -- and
that both adapters satisfy the ``AudioEngine`` protocol through their
constructors (no model load, no ``object.__new__``).

Needs the chunk-generation deps (numpy, soundfile) because
``chatterbox_local_service`` imports ``chapter_audio.generation``.
"""

import unittest

from audio.provider_profile import (
    ADAPTER_CHATTERBOX,
    ADAPTER_VIBEVOICE,
    PROVIDER_PROFILES,
    resolve_provider_profile,
)
from chapter_audio.engine import AudioEngine
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


if __name__ == "__main__":
    unittest.main()
