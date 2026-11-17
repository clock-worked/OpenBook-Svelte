"""The TTS engine seam: the one named interface chunk generation talks to.

An :class:`AudioEngine` is a local TTS model behind a single-call
``generate_audio``. It *declares* what the chunk plan needs to know about it:

- ``adapter`` -- the engine adapter name from the provider profile registry
  (``audio/provider_profile.py``: ``ADAPTER_VIBEVOICE``, ``ADAPTER_CHATTERBOX``).
- ``max_chunk_chars`` -- the longest text one ``generate_audio`` call accepts,
  or ``None`` for no limit (the whole text is one call).
- ``normalize(text)`` -- the text cleanup this engine hears. It is the one
  answer to "what text does this engine get"; it must be idempotent (the plan
  normalizes to size chunks honestly, and the engine may normalize again).

The two adapters are ``ChatterboxLocalService`` (short-form: 450 chars) and
``VibeVoiceLocalService`` (no limit). Consistent with ADR-0011: the engines
stay separate integration adapters; this module only names their interface.

Pure module: no numpy / soundfile / model imports.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class AudioEngine(Protocol):
    """A local TTS engine adapter (see the module docstring)."""

    adapter: str
    max_chunk_chars: Optional[int]

    def normalize(self, text: str) -> str:
        """The engine's text cleanup (idempotent)."""
        ...

    def generate_audio(
        self,
        text: str,
        sample_path: str,
        output_path: str,
        seed: Optional[int] = None,
    ) -> None:
        """Generate one clip for ``text`` (at most ``max_chunk_chars``)."""
        ...


__all__ = ["AudioEngine"]
