"""Shared run-status management for the AI-provider routes.

The three AI services (Gemini dialogue assist, local LLM, JEV) all expose the
same request-id -> status poll pattern: a route calls ``prepare_status`` when
a run is queued, the worker thread updates progress as it goes, and a status
route polls the latest snapshot. The status bookkeeping (a lock, a dict, and
a 64-entry cap that evicts the oldest entry) used to be copy-pasted into every
service; this is the one shared implementation.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from api_models import DialogueAiAssistStatus


class RunStatusTracker:
    """Thread-safe store of per-request-id run statuses, capped at 64 entries.

    Behaviour matches the per-service clones it replaces exactly:
    - ``update`` is a no-op for a blank request id;
    - progress is clamped to ``[0, total]``;
    - a blank id, chapter path, log directory, or started-at inherits the
      previous value;
    - when the store exceeds 64 entries the oldest (insertion-order) entry is
      evicted.
    """

    MAX_ENTRIES = 64

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._statuses: Dict[str, Dict[str, Any]] = {}

    def prepare(
        self,
        request_id: Optional[str],
        *,
        total_lines: int,
        chapter_path: Optional[str] = None,
    ) -> None:
        """Record a freshly-queued run. No-op when ``request_id`` is blank."""
        self.update(
            request_id,
            status="queued",
            processed_lines=0,
            total_lines=total_lines,
            chapter_path=chapter_path,
        )

    def get(self, request_id: Optional[str]) -> Optional[DialogueAiAssistStatus]:
        """Return the latest status for ``request_id``, or None if unknown."""
        with self._lock:
            payload = self._statuses.get(str(request_id or "").strip())
            return DialogueAiAssistStatus(**payload) if payload else None

    def update(
        self,
        request_id: Optional[str],
        *,
        status: str,
        processed_lines: int,
        total_lines: int,
        chapter_path: Optional[str] = None,
        error: Optional[str] = None,
        log_directory: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> None:
        """Upsert the status for ``request_id``. No-op when the id is blank."""
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return

        now = datetime.now(timezone.utc).isoformat()
        normalized_total = max(int(total_lines or 0), 0)
        normalized_processed = max(int(processed_lines or 0), 0)

        if normalized_total > 0:
            normalized_processed = min(normalized_processed, normalized_total)
            progress_ratio = normalized_processed / normalized_total
        else:
            progress_ratio = 1.0 if status == "completed" else 0.0

        with self._lock:
            previous = self._statuses.get(normalized_request_id, {})
            self._statuses[normalized_request_id] = {
                "requestId": normalized_request_id,
                "status": status,
                "processedLines": normalized_processed,
                "totalLines": normalized_total,
                "progressRatio": progress_ratio,
                "startedAt": started_at or previous.get("startedAt") or now,
                "updatedAt": now,
                "chapterPath": chapter_path or previous.get("chapterPath"),
                "error": error,
                "logDirectory": log_directory or previous.get("logDirectory"),
            }

            while len(self._statuses) > self.MAX_ENTRIES:
                oldest_request_id = next(iter(self._statuses))
                self._statuses.pop(oldest_request_id, None)
