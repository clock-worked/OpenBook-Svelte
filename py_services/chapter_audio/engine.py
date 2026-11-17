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

The chunk plan accounts for the declared limit *at plan time*
(:func:`plan_engine_chunks`, :func:`engine_text_pieces`): an engine is handed
text that already fits, and refuses text that doesn't
(:class:`EngineChunkTooLongError`) -- no engine silently re-chunks, so the
plan's chunk count is the number of engine calls.

Pure module: no numpy / soundfile / model imports.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from .text_utils import split_text_to_max_chars


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


class EngineChunkTooLongError(ValueError):
    """Text handed to an engine exceeds its declared ``max_chunk_chars``.

    Means the caller skipped the plan-time sub-split
    (:func:`plan_engine_chunks` / :func:`engine_text_pieces`)."""

    def __init__(self, adapter: str, length: int, max_chunk_chars: int) -> None:
        self.adapter = adapter
        self.length = length
        self.max_chunk_chars = max_chunk_chars
        super().__init__(
            f"{adapter} engine accepts at most {max_chunk_chars} characters per call; "
            f"got {length}. Sub-split the text at plan time "
            "(chapter_audio.engine.plan_engine_chunks / engine_text_pieces)."
        )


def _engine_limit(engine: Any) -> Optional[int]:
    limit = getattr(engine, "max_chunk_chars", None)
    return int(limit) if limit is not None else None


def _engine_normalize(engine: Any, text: str) -> str:
    normalize = getattr(engine, "normalize", None)
    return normalize(text) if callable(normalize) else text


def require_within_engine_limit(engine: Any, text: str) -> None:
    """Raise :class:`EngineChunkTooLongError` if ``engine.normalize(text)``
    exceeds the engine's declared limit. No-op for an unbounded engine."""
    limit = _engine_limit(engine)
    if limit is None:
        return
    length = len(_engine_normalize(engine, text))
    if length > limit:
        raise EngineChunkTooLongError(
            str(getattr(engine, "adapter", type(engine).__name__)), length, limit
        )


def engine_text_pieces(engine: Any, text: str) -> list[str]:
    """The engine calls for one text: ``engine.normalize(text)`` split to the
    engine's ``max_chunk_chars`` (sentence/word-aware), or one piece for an
    unbounded engine. Each piece is what one ``generate_audio`` call gets."""
    normalized = _engine_normalize(engine, text)
    limit = _engine_limit(engine)
    if limit is None:
        return [normalized]
    return split_text_to_max_chars(normalized, limit)


def plan_engine_chunks(
    alignment_plan: list[dict[str, Any]],
    engine: Any,
) -> list[dict[str, Any]]:
    """Sub-split an alignment chunk plan into the engine's true chunks.

    ``alignment_plan`` is what ``build_line_chunk_generation_plan`` returns
    (chunks sized for the pipeline / WhisperX alignment, e.g. >= 800 chars).
    Each alignment chunk becomes one or more *engine chunks*, one per
    ``generate_audio`` call. Every returned entry has:

    - ``chunkIndex`` -- 1..N over engine chunks (N = true engine-call count,
      what progress reports as ``chunkTotal``);
    - ``text`` / ``charCount`` -- the normalized text of that one call;
    - ``startLine`` / ``endLine`` / ``lineCount`` -- copied from the parent
      alignment chunk (a sub-chunk does not know its own line span);
    - ``alignmentChunkIndex`` -- the parent's ``chunkIndex``;
    - ``subChunkIndex`` / ``subChunkCount`` -- position within the parent.

    For an unbounded engine (VibeVoice) the plan keeps its shape: one engine
    chunk per alignment chunk, text normalized.
    """
    engine_plan: list[dict[str, Any]] = []
    for alignment_chunk in alignment_plan:
        pieces = engine_text_pieces(engine, str(alignment_chunk["text"]))
        for sub_index, piece in enumerate(pieces, start=1):
            engine_plan.append(
                {
                    "chunkIndex": len(engine_plan) + 1,
                    "startLine": alignment_chunk["startLine"],
                    "endLine": alignment_chunk["endLine"],
                    "lineCount": alignment_chunk["lineCount"],
                    "charCount": len(piece),
                    "text": piece,
                    "alignmentChunkIndex": alignment_chunk["chunkIndex"],
                    "subChunkIndex": sub_index,
                    "subChunkCount": len(pieces),
                }
            )
    return engine_plan


def alignment_chunk_count(chunk_plan: list[dict[str, Any]]) -> int:
    """Number of alignment chunks behind a plan (``len`` for a plan that was
    never sub-split)."""
    parents = {chunk.get("alignmentChunkIndex") for chunk in chunk_plan}
    if None in parents:
        return len(chunk_plan)
    return len(parents)


__all__ = [
    "AudioEngine",
    "EngineChunkTooLongError",
    "alignment_chunk_count",
    "engine_text_pieces",
    "plan_engine_chunks",
    "require_within_engine_limit",
]
