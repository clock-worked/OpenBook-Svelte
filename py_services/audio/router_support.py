"""Shared plumbing for the two audio routers (``audio_router.py`` and
``audio/vibevoice.py``).

Both routers call the same ``AudioGenerationService`` and used to carry
their own copies of:

- the lazily-created, cached service (``_ensure_audio_service``), and
- the error-string -> HTTP status sniffing for a failed generation,

and the audio-root fallback existed in two orderings across them. This
module is the one copy of each, including the one BE audio-root resolver
(:func:`resolve_audio_root`; the FE twin is ``audioRootFor`` in
``apps/desktop/src/lib/services/audio.ts``). It imports nothing heavy at module level
(fastapi is imported only when an HTTP error is actually raised), so its
pure parts are testable without fastapi.
"""

from __future__ import annotations

import os
from typing import Any, Callable, NoReturn, Optional, TypeVar

AUDIO_ROOT_NOT_CONFIGURED_DETAIL = (
    "Audio root not configured. Set audio root or provide audio_root in request."
)

# Substrings of a generation error that mean "the request was wrong"
# (a missing voice sample, a missing/invalid field) -> 400. Anything else
# is an engine/server failure -> 500.
_CLIENT_ERROR_MARKERS = ("not found", "could not be found", "required", "invalid")

ServiceT = TypeVar("ServiceT")


def resolve_audio_root(
    *,
    configured_audio_root: Optional[str],
    request_override: Optional[str],
    book_root: Optional[str],
) -> Optional[str]:
    """The effective audio root -- the one canonical ordering:

    1. the explicitly configured audio root (``/api/set-audio-root``),
    2. the per-request override (a request's ``audio_root`` / ``root``),
    3. the book root.

    The per-request override wins over the book root. Returns ``None`` when
    all three are empty.
    """
    return configured_audio_root or request_override or book_root or None


def generation_error_status(error: Any) -> int:
    """HTTP status for a failed ``generate_audio_line`` result's ``error``."""
    error_text = str(error).lower()
    if any(marker in error_text for marker in _CLIENT_ERROR_MARKERS):
        return 400
    return 500


def _http_error(status_code: int, detail: Any) -> Exception:
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


def raise_generation_failure(error: Any) -> NoReturn:
    """Raise the HTTPException for a failed generation, status by
    :func:`generation_error_status`, detail = the error itself."""
    raise _http_error(generation_error_status(error), error)


def make_audio_service_ensurer(
    *,
    get_audio_root: Callable[[], str],
    get_book_root: Callable[[], str],
    get_audio_service: Callable[[], Optional[ServiceT]],
    set_audio_service: Callable[[str, ServiceT], None],
    service_factory: Callable[[str], ServiceT],
) -> Callable[[Optional[str]], ServiceT]:
    """Build the router's ``ensure_audio_service(audio_root_override=None)``.

    Returns the cached service when one exists (the override is then not
    consulted). Otherwise creates one rooted at :func:`resolve_audio_root`;
    caches it through ``set_audio_service``; and returns it. Raises a 400 HTTPException when
    no root is available.
    """

    def ensure_audio_service(audio_root_override: Optional[str] = None) -> ServiceT:
        existing = get_audio_service()
        if existing is not None:
            return existing

        selected_root = resolve_audio_root(
            configured_audio_root=get_audio_root(),
            request_override=audio_root_override,
            book_root=get_book_root(),
        )
        if not selected_root:
            raise _http_error(400, AUDIO_ROOT_NOT_CONFIGURED_DETAIL)

        normalized_root = os.path.normpath(selected_root)
        service = service_factory(normalized_root)
        set_audio_service(normalized_root, service)
        print(f"Audio root path defaulted to: '{normalized_root}'")
        return service

    return ensure_audio_service


__all__ = [
    "AUDIO_ROOT_NOT_CONFIGURED_DETAIL",
    "generation_error_status",
    "make_audio_service_ensurer",
    "raise_generation_failure",
    "resolve_audio_root",
]
