"""Regression test: Chatterbox long-text chunking fix.

Chatterbox is a short-form TTS engine. A long line sent in a single call
degrades into garbled/truncated audio (see the 2026-11-05 Chapter 1387
incident: 39/62 production lines collapsed to ~0.1s). ``generate_audio`` must
split text longer than ``max_chunk_chars`` into sentence/word-aware chunks and
stitch them, instead of making one oversized call.

The chapter benchmark pipeline (min=1, max=450) produces clean long-form
output; this test pins that behaviour for the production per-line path.
"""

import tempfile
import unittest
from pathlib import Path

from chatterbox_local_service import ChatterboxLocalService
from chapter_audio_test.generation import split_text_to_max_chars


class SplitTextTests(unittest.TestCase):
    def test_short_text_unsplit(self):
        self.assertEqual(split_text_to_max_chars("Hello world.", 450), ["Hello world."])

    def test_long_text_all_pieces_under_max(self):
        text = "word " * 200  # 1000 chars, no sentence breaks
        pieces = split_text_to_max_chars(text, 450)
        self.assertGreater(len(pieces), 1)
        for p in pieces:
            self.assertLessEqual(len(p), 450)
        # re-joined, whitespace-normalised words are preserved
        self.assertEqual(" ".join(pieces).split(), text.split())

    def test_pieces_respect_max_after_packing(self):
        text = "First sentence ends. " * 20  # 420 chars
        for p in split_text_to_max_chars(text, 100):
            self.assertLessEqual(len(p), 100)


class _FakeModel:
    """Minimal in-process stand-in for the Chatterbox model (no GPU)."""

    def __init__(self, sr=24000):
        self.sr = sr
        self.conds = "prepared"
        self.calls = []

    def prepare_conditionals(self, path):
        pass

    def generate(self, text, repetition_penalty=1.2, temperature=0.8):
        import torch

        self.calls.append(text)
        # 1 sample frame per character -> deterministic, tiny duration
        return torch.zeros(1, max(1, len(text)))


def _make_service(max_chunk_chars=450, join_pause_ms=0):
    """Build a service with a fake model, bypassing __init__ (no model load)."""
    svc = object.__new__(ChatterboxLocalService)
    svc.variant = "turbo"
    svc.device = "cpu"
    svc.temperature = 0.8
    svc.repetition_penalty = 1.2
    svc.max_chunk_chars = max_chunk_chars
    svc.join_pause_ms = join_pause_ms
    svc.worker_process = None
    svc._in_process_model = _FakeModel()
    svc._cached_sample_path = None
    svc.stats = []
    return svc


class GenerateAudioChunkingTests(unittest.TestCase):
    def test_short_text_single_call(self):
        svc = _make_service()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            svc.generate_audio("Short line.", "sample.wav", str(out))
            self.assertEqual(len(svc._in_process_model.calls), 1)
            self.assertTrue(out.exists())

    def test_long_text_is_chunked_and_stitched(self):
        svc = _make_service(max_chunk_chars=450)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            text = "word " * 200  # 1000 chars -> 3 chunks of <=450
            svc.generate_audio(text, "sample.wav", str(out))
            model = svc._in_process_model
            self.assertGreater(len(model.calls), 1)
            for c in model.calls:
                self.assertLessEqual(len(c), 450)
            self.assertTrue(out.exists())
            # stitched duration ~= sum of the per-chunk durations
            import soundfile as sf

            info = sf.info(str(out))
            expected = sum(len(c) for c in model.calls) / 24000.0
            self.assertAlmostEqual(info.duration, expected, delta=0.01)


if __name__ == "__main__":
    unittest.main()
