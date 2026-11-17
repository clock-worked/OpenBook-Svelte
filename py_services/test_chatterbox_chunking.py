"""Regression test: Chatterbox long-text chunking fix.

Chatterbox is a short-form TTS engine. A long line sent in a single call
degrades into garbled/truncated audio (see the 2026-11-05 Chapter 1387
incident: 39/62 production lines collapsed to ~0.1s). Text longer than the
engine's declared ``max_chunk_chars`` is split into sentence/word-aware chunks
*at plan time* and the pieces stitched -- never sent as one oversized call,
and never silently re-chunked inside the engine (the chunk plan's counts are
the true engine-call counts).

Pins: the per-line path (``generate_engine_text``), the chapter/character
pipeline (``plan_engine_chunks`` + ``generate_line_chunked_chapter_audio``),
and that the Chatterbox adapter refuses oversized text.
"""

import tempfile
import unittest
from pathlib import Path

from chatterbox_local_service import ChatterboxLocalService
from chapter_audio.engine import AudioEngine, EngineChunkTooLongError, plan_engine_chunks
from chapter_audio.generation import (
    build_line_chunk_generation_plan,
    generate_engine_text,
    generate_line_chunked_chapter_audio,
    split_text_to_max_chars,
)


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

    def generate(self, text, repetition_penalty=1.2, temperature=0.8, **_kwargs):
        import torch

        self.calls.append(text)
        # 1 sample frame per character -> deterministic, tiny duration
        return torch.zeros(1, max(1, len(text)))


def _make_service(max_chunk_chars=450):
    """Build the Chatterbox adapter through its constructor with a fake model."""
    return ChatterboxLocalService(
        device="cpu",
        max_chunk_chars=max_chunk_chars,
        in_process_model=_FakeModel(),
    )


class _FakeEngine:
    """An ``AudioEngine`` that writes 1 frame per character (no model)."""

    adapter = "fake"

    def __init__(self, max_chunk_chars=None, sr=24000):
        self.max_chunk_chars = max_chunk_chars
        self.sr = sr
        self.calls = []

    @staticmethod
    def normalize(text):
        return " ".join(text.split())

    def generate_audio(self, text, sample_path, output_path, seed=None):
        import numpy as np
        import soundfile as sf

        self.calls.append(text)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(output_path, np.zeros((max(1, len(text)), 1), dtype="float32"), self.sr)


class ChatterboxEngineTests(unittest.TestCase):
    def test_fake_backed_service_is_an_audio_engine(self):
        self.assertIsInstance(_make_service(), AudioEngine)

    def test_short_text_single_call(self):
        svc = _make_service()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            svc.generate_audio("Short line.", "sample.wav", str(out))
            self.assertEqual(svc._in_process_model.calls, ["Short line."])
            self.assertTrue(out.exists())

    def test_long_text_is_refused_not_silently_rechunked(self):
        svc = _make_service(max_chunk_chars=450)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            with self.assertRaises(EngineChunkTooLongError):
                svc.generate_audio("word " * 200, "sample.wav", str(out))
            self.assertEqual(svc._in_process_model.calls, [])
            self.assertFalse(out.exists())


class GenerateEngineTextTests(unittest.TestCase):
    """The single-line path plans the engine calls (incident regression)."""

    def test_long_text_is_planned_and_stitched(self):
        svc = _make_service(max_chunk_chars=450)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            text = "word " * 200  # 1000 chars -> 3 calls of <=450
            calls = generate_engine_text(svc, text=text, sample_path="sample.wav", output_path=out)
            model = svc._in_process_model
            self.assertEqual(calls, len(model.calls))
            self.assertEqual(calls, 3)
            for c in model.calls:
                self.assertLessEqual(len(c), 450)
            self.assertTrue(out.exists())
            # stitched duration ~= sum of the per-chunk durations
            import soundfile as sf

            info = sf.info(str(out))
            expected = sum(len(c) for c in model.calls) / 24000.0
            self.assertAlmostEqual(info.duration, expected, delta=0.01)

    def test_short_text_single_call(self):
        svc = _make_service()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            calls = generate_engine_text(svc, text="Hi there.", sample_path="s.wav", output_path=out)
            self.assertEqual(calls, 1)
            self.assertTrue(out.exists())

    def test_unbounded_engine_is_one_call(self):
        engine = _FakeEngine(max_chunk_chars=None)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            calls = generate_engine_text(engine, text="word " * 400, sample_path="s.wav", output_path=out)
            self.assertEqual(calls, 1)


def _chapter_text(line_count=12, words_per_line=30):
    return "\n".join(
        " ".join(f"w{line}x{word}" for word in range(words_per_line)) + "."
        for line in range(line_count)
    )


class PlanTimeSubSplitPipelineTests(unittest.TestCase):
    """The chunk plan's counts are the engine's true call counts."""

    def _run(self, engine, chunk_plan):
        progress = []
        with tempfile.TemporaryDirectory() as d:
            report = generate_line_chunked_chapter_audio(
                service=engine,
                chunk_plan=chunk_plan,
                sample_path=Path("sample.wav"),
                output_path=Path(d) / "full.wav",
                chunks_dir=Path(d) / "chunks",
                min_chunk_chars=800,
                seed=None,
                join_pause_ms=0,
                on_chunk=lambda index, total: progress.append((index, total)),
            )
        return report, progress

    def test_progress_counts_engine_calls(self):
        alignment_plan = build_line_chunk_generation_plan(
            chapter_text=_chapter_text(), min_chunk_chars=800
        )
        engine = _FakeEngine(max_chunk_chars=450)
        engine_plan = plan_engine_chunks(alignment_plan, engine)
        self.assertGreater(len(engine_plan), len(alignment_plan))

        report, progress = self._run(engine, engine_plan)

        self.assertEqual(len(engine.calls), len(engine_plan))
        self.assertEqual(report["chunkCount"], len(engine.calls))
        self.assertEqual(report["alignmentChunkCount"], len(alignment_plan))
        self.assertEqual(report["engineMaxChunkChars"], 450)
        self.assertEqual(progress[-1], (len(engine.calls), len(engine.calls)))
        self.assertEqual([i for i, _ in progress], list(range(1, len(engine.calls) + 1)))
        for text in engine.calls:
            self.assertLessEqual(len(text), 450)

    def test_unsplit_plan_is_rejected_before_any_call(self):
        alignment_plan = build_line_chunk_generation_plan(
            chapter_text=_chapter_text(), min_chunk_chars=800
        )
        engine = _FakeEngine(max_chunk_chars=450)
        with self.assertRaises(EngineChunkTooLongError):
            self._run(engine, alignment_plan)
        self.assertEqual(engine.calls, [])

    def test_unbounded_engine_keeps_alignment_chunks(self):
        alignment_plan = build_line_chunk_generation_plan(
            chapter_text=_chapter_text(), min_chunk_chars=800
        )
        engine = _FakeEngine(max_chunk_chars=None)
        engine_plan = plan_engine_chunks(alignment_plan, engine)
        report, progress = self._run(engine, engine_plan)
        self.assertEqual(report["chunkCount"], len(alignment_plan))
        self.assertEqual(report["alignmentChunkCount"], len(alignment_plan))
        self.assertEqual(progress[-1], (len(alignment_plan), len(alignment_plan)))


if __name__ == "__main__":
    unittest.main()
