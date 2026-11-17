from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

WORD_RE = re.compile(r"[a-z0-9']+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def similarity_ratio(expected: str, actual: str) -> float:
    expected_tokens = " ".join(tokenize(expected))
    actual_tokens = " ".join(tokenize(actual))
    if not expected_tokens and not actual_tokens:
        return 1.0
    return round(
        SequenceMatcher(None, expected_tokens, actual_tokens, autojunk=False).ratio(),
        4,
    )


def token_list_similarity(left_tokens: list[str], right_tokens: list[str]) -> float:
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return round(
        SequenceMatcher(None, left_tokens, right_tokens, autojunk=False).ratio(),
        4,
    )


def normalize_chapter_text(text: str) -> str:
    return " ".join(text.split())


# Typography -> ASCII. The one shared replacement set every TTS engine starts
# from (engine adapters add their own rules on top in ``normalize``).
TYPOGRAPHY_REPLACEMENTS: dict[str, str] = {
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2026": "...",  # ellipsis
    # U+FFFD: a curly quote that was already corrupted upstream; in book
    # text it is almost always an apostrophe.
    "\ufffd": "'",
}


def normalize_typography(text: str) -> str:
    """Replace smart quotes, dashes, ellipses (and U+FFFD) with ASCII.

    Shared primitive for the engine adapters' ``normalize`` and for the
    cloud TTS path. Does not touch whitespace.
    """
    for src, dst in TYPOGRAPHY_REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text


def split_text_to_max_chars(text: str, max_chars: int) -> list[str]:
    """Split ``text`` into pieces of at most ``max_chars`` characters.

    The sentence/word-aware splitter behind both the chapter chunk plan and
    the engine sub-split (``chapter_audio.engine.engine_text_pieces``).
    Pieces break on sentence boundaries first, then word boundaries, and
    small pieces are re-packed so no piece exceeds ``max_chars``.
    """
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue

        words = sentence.split()
        current: list[str] = []
        for word in words:
            candidate = " ".join([*current, word])
            if current and len(candidate) > max_chars:
                pieces.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            pieces.append(" ".join(current))

    packed: list[str] = []
    for piece in pieces:
        candidate = " ".join([*packed[-1:], piece]) if packed else piece
        if packed and len(candidate) <= max_chars:
            packed[-1] = candidate
        else:
            packed.append(piece)
    return packed


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "unknown"


def to_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
