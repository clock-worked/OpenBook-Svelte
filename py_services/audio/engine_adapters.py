"""Engine adapters built at the composition root (``AudioGenerationService``).

:class:`VoiceBoundEngine` is the :class:`chapter_audio.engine.AudioEngine` the
chunked pipeline drives: one local engine (named by its provider profile's
``adapter``) with a voice, voice-sample root and generation settings bound,
generating through the audio service's cached model. It carries the engine
class's declarations (``max_chunk_chars``, ``normalize``) so the chunk plan
can sub-split for it before any model is loaded.

Built by ``AudioGenerationService.audio_engine``; this module only holds the
adapter shape, so it stays pure (no model / fastapi imports) and testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Tuple

# (text, voice_id, output_path) -> (success, error details)
GenerateToPath = Callable[..., Tuple[bool, Optional[str]]]


class VoiceBoundEngine:
    """An ``AudioEngine`` adapter: a local engine with a voice bound."""

    def __init__(
        self,
        *,
        adapter: str,
        display_name: str,
        max_chunk_chars: Optional[int],
        normalize: Callable[[str], str],
        voice_id: str,
        generate_to_path: GenerateToPath,
    ) -> None:
        self.adapter = adapter
        self.display_name = display_name
        self.max_chunk_chars = max_chunk_chars
        self._normalize = normalize
        self.voice_id = voice_id
        self._generate_to_path = generate_to_path

    def normalize(self, text: str) -> str:
        return self._normalize(text)

    def generate_audio(
        self,
        text: str,
        sample_path: str,
        output_path: str,
        seed: Optional[int] = None,
    ) -> None:
        """Generate one chunk. The bound voice wins over ``sample_path``
        (the pipeline passes the voice id as both); ``seed`` is not used by
        the service-backed engines."""
        _ = seed
        success, error_details = self._generate_to_path(
            text=text,
            voice_id=self.voice_id or str(sample_path),
            output_path=Path(output_path),
        )
        if not success:
            raise RuntimeError(error_details or f"{self.display_name} generation failed.")


__all__ = ["GenerateToPath", "VoiceBoundEngine"]
