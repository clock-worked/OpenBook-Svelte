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
