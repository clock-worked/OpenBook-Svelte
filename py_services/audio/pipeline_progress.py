"""Pipeline progress record: the one BE answer to "what is the long run doing".

The chunked character pipeline (``audio/vibevoice.py``) runs TTS + WhisperX +
alignment + split + manifest + validation inside ONE synchronous request, so
the UI learns what is happening only by polling a ``progress.json`` the
pipeline writes at each phase boundary. This module owns everything about
that file:

- **Where it lives** -- :func:`resolve_run_location` is the single derivation
  of the run's work dir and its :data:`Scope` (``single`` = one chapter,
  written under that chapter's ``audio_lines``; ``cross`` = a book batch,
  written under ``_book_batch``). The pipeline, the failure path and the
  progress route all go through it; nothing else re-derives it.
- **What it says** -- the record schema: ``phase`` (a :class:`Phase`),
  ``scope``, ``detail``, ``lineCount``, ``chunkIndex``/``chunkTotal`` (true
  engine-chunk counts from the candidate-6 plan), ``startedAt``,
  ``updatedAt``. The record is *self-describing*: the FE reads ``scope``
  from it and never derives single-vs-cross itself.
- **How it is written** -- :func:`write_progress`: atomic tmp+replace, one
  stable ``startedAt`` per run, ``lineCount``/``chunk*`` carried over from
  the previous write, corrupt-file tolerant, never raises.
- **How it is found** -- :func:`read_progress` probes both candidate
  locations for a character and returns the most recently updated record,
  so the poller needs only the character (and the chapter it asked for).

Phase and scope names are pinned for both languages by
``tests/pipeline_progress_vectors.json`` (py: ``test_pipeline_progress.py``;
TS: ``__tests__/pipelineProgress.test.ts``).

Pure module: no fastapi / model imports, safe to import from tests.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Optional

from chapter_audio.text_utils import utc_now_iso

PIPELINE_PROGRESS_FILENAME = "progress.json"
CHUNK_PIPELINE_DIRNAME = "_chunk_pipeline"
BOOK_BATCH_DIRNAME = "_book_batch"
UNKNOWN_CHAPTER = "unknown"

Scope = Literal["single", "cross"]
SCOPES: tuple[Scope, ...] = ("single", "cross")

_logger = logging.getLogger(__name__)


class Phase(str, Enum):
    """Pipeline phases, in run order (``failed``/``unknown`` are terminal/absent)."""

    STARTING = "starting"
    GENERATING = "generating"
    TRANSCRIBING = "transcribing"
    ALIGNING = "aligning"
    SPLITTING = "splitting"
    FINALIZING = "finalizing"
    VALIDATING = "validating"
    DONE = "done"
    FAILED = "failed"
    UNKNOWN = "unknown"


RECORD_KEYS: tuple[str, ...] = (
    "phase",
    "scope",
    "detail",
    "lineCount",
    "chunkIndex",
    "chunkTotal",
    "startedAt",
    "updatedAt",
)

_CARRIED_INT_KEYS: tuple[str, ...] = ("lineCount", "chunkIndex", "chunkTotal")


@dataclass(frozen=True)
class RunLocation:
    """Where one character run keeps its artifacts and its progress record."""

    work_root: Path
    scope: Scope

    @property
    def work_dir(self) -> Path:
        return self.work_root / CHUNK_PIPELINE_DIRNAME

    @property
    def progress_path(self) -> Path:
        return self.work_dir / PIPELINE_PROGRESS_FILENAME


def location_for(
    audio_root: Path,
    character_name: str,
    *,
    scope: Scope,
    chapter_title: str = UNKNOWN_CHAPTER,
) -> RunLocation:
    """The layout rule: single runs under the chapter, cross runs under _book_batch."""
    if scope == "single":
        work_root = audio_root / (chapter_title or UNKNOWN_CHAPTER) / "audio_lines" / character_name
    else:
        work_root = audio_root / BOOK_BATCH_DIRNAME / character_name
    return RunLocation(work_root=work_root, scope=scope)


def resolve_run_location(
    audio_root: Path,
    character_name: str,
    line_chapter_titles: Iterable[Optional[str]],
    *,
    default_chapter_title: str = UNKNOWN_CHAPTER,
) -> RunLocation:
    """The run's location from the chapters its (normalized) lines belong to.

    One distinct chapter -> ``single`` under that chapter; more -> ``cross``.
    Blank titles fall back to ``default_chapter_title``.
    """
    fallback = str(default_chapter_title or "").strip() or UNKNOWN_CHAPTER
    chapters = sorted(
        {str(title or "").strip() or fallback for title in line_chapter_titles} or {fallback}
    )
    if len(chapters) == 1:
        return location_for(audio_root, character_name, scope="single", chapter_title=chapters[0])
    return location_for(audio_root, character_name, scope="cross")


def _read_record(progress_path: Path) -> Optional[Dict[str, Any]]:
    if not progress_path.exists():
        return None
    try:
        loaded = json.loads(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def write_progress(
    location: RunLocation,
    phase: Phase,
    *,
    detail: Optional[str] = None,
    line_count: Optional[int] = None,
    chunk_index: Optional[int] = None,
    chunk_total: Optional[int] = None,
) -> None:
    """Atomically record pipeline progress so the UI can poll it.

    Never raises: progress bookkeeping must not break generation.
    ``startedAt`` is preserved from the previous entry so the whole run
    carries one stable start timestamp; ``lineCount``/``chunkIndex``/
    ``chunkTotal`` carry over when not given. :attr:`Phase.STARTING`
    starts a fresh record (no carry-over from an earlier run).
    """
    try:
        work_dir = location.work_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        progress_path = location.progress_path
        phase = Phase(phase)
        # STARTING opens a new run: nothing carries over from an earlier
        # run in the same work dir (its startedAt would make the poller
        # discard this run as stale).
        previous = {} if phase is Phase.STARTING else (_read_record(progress_path) or {})
        record: Dict[str, Any] = {
            "phase": phase.value,
            "scope": location.scope,
            "detail": detail,
            "lineCount": line_count,
            "chunkIndex": chunk_index,
            "chunkTotal": chunk_total,
            "startedAt": previous.get("startedAt") or utc_now_iso(),
            "updatedAt": utc_now_iso(),
        }
        for key in _CARRIED_INT_KEYS:
            if record[key] is None and isinstance(previous.get(key), int):
                record[key] = previous[key]
        tmp_path = progress_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, progress_path)
    except Exception as exc:
        _logger.warning("Failed to write pipeline progress: %s", exc)


def empty_record() -> Dict[str, Any]:
    """The record served when no run has written progress yet."""
    return {key: None for key in RECORD_KEYS} | {"phase": Phase.UNKNOWN.value}


def read_progress(
    audio_root: Path,
    character_name: str,
    *,
    chapter_title: str = UNKNOWN_CHAPTER,
) -> Dict[str, Any]:
    """The most recent progress record for a character, from either location.

    Probes the single-chapter location for ``chapter_title`` and the book
    batch location; the newer ``updatedAt`` wins. Records written before
    ``scope`` existed get it filled in from where they were found.
    """
    best: Optional[Dict[str, Any]] = None
    for scope in SCOPES:
        location = location_for(audio_root, character_name, scope=scope, chapter_title=chapter_title)
        record = _read_record(location.progress_path)
        if record is None:
            continue
        record.setdefault("scope", location.scope)
        if best is None or str(record.get("updatedAt") or "") > str(best.get("updatedAt") or ""):
            best = record
    if best is None:
        return empty_record()
    return empty_record() | best


class ProgressTracker:
    """A run's handle on its progress record.

    Created before the run starts and bound once the pipeline knows its
    location, so the failure path can record ``failed`` at the same place
    without re-deriving it. Writes before binding are dropped.
    """

    def __init__(self) -> None:
        self.location: Optional[RunLocation] = None

    def bind(self, location: RunLocation) -> None:
        self.location = location

    def write(self, phase: Phase, **fields: Any) -> None:
        if self.location is not None:
            write_progress(self.location, phase, **fields)

    def fail(self, detail: str) -> None:
        self.write(Phase.FAILED, detail=str(detail)[:500])
