"""Shared private helpers for the dialogue-AI services.

The Gemini (dialogue_ai), local-LLM (local_dialogue_ai), and JEV (jev)
services all need the same small set of building blocks. They used to be
copy-pasted into each service — and ``jev_service`` imported five of them
straight from ``local_dialogue_ai_service`` (a sibling-to-sibling *private*
dependency). This module is the single home for those helpers; every service
imports them from here.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from api_models import DialogueAiCharacterRef

# All three services live in this same directory, so they share one .env file.
ENV_FILE_PATH = Path(__file__).with_name(".env")


def load_local_env_file() -> None:
    """Populate ``os.environ`` from ``py_services/.env`` (never overrides).

    Lines are ``KEY=VALUE``; blank lines and ``#`` comments are skipped and
    surrounding quotes on the value are stripped. Existing environment values
    always win (``os.environ.setdefault``).
    """
    if not ENV_FILE_PATH.exists():
        return

    for raw_line in ENV_FILE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def normalize_character(value: Any) -> str:
    """Normalize a character id/name/alias to a comparable key.

    Lowercases, turns ``_``/``-`` into spaces, strips punctuation, and collapses
    whitespace. This is the single normalization used for character matching
    across all three services.
    """
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def character_name(character_id: Optional[str], characters: List[DialogueAiCharacterRef]) -> Optional[str]:
    """Resolve a (possibly non-canonical) character id to a display name.

    Matches on normalized id / name / alias; falls back to a title-cased form
    of the raw id when nothing matches.
    """
    normalized = normalize_character(character_id)
    if not normalized:
        return None
    for character in characters:
        if normalized in {
            normalize_character(character.characterId),
            normalize_character(character.name),
            *(normalize_character(alias) for alias in character.aliases),
        }:
            return character.name
    return str(character_id).replace("_", " ").replace("-", " ").title()


def character_lookup(
    chapter_characters: List[DialogueAiCharacterRef],
    book_characters: List[DialogueAiCharacterRef],
) -> Dict[str, DialogueAiCharacterRef]:
    """Build a normalized-key -> character ref map (book first, chapter wins)."""
    lookup: Dict[str, DialogueAiCharacterRef] = {}
    for character in [*book_characters, *chapter_characters]:
        for value in [character.characterId, character.name, *character.aliases]:
            normalized = normalize_character(value)
            if normalized:
                lookup[normalized] = character
    return lookup


def normalize_model_answer(raw_output: str) -> str:
    """Strip markdown fences / labels / surrounding punctuation from a raw answer."""
    answer = str(raw_output or "").strip()
    answer = re.sub(r"^```(?:text)?\s*", "", answer, flags=re.IGNORECASE)
    answer = re.sub(r"\s*```$", "", answer)
    answer = answer.splitlines()[0].strip() if answer else ""
    answer = re.sub(r"^(?:character(?:id)?|speaker)\s*:\s*", "", answer, flags=re.IGNORECASE)
    return answer.strip(" `\"'.,;:")


def count_target_lines(lines: List[Any], max_lines: Optional[int] = None) -> int:
    """Count dialogue lines that the AI will process (non-narrator).

    Optionally clamped to ``max_lines`` when it is a non-negative bound. The
    local and JEV services clamp to ``request.max_lines``; the Gemini service
    does not (it passes no bound), so behaviour is preserved per service.
    """
    total = sum(1 for line in lines if line.characterId not in (None, "", "narrator"))
    if max_lines is not None and max_lines >= 0:
        total = min(total, max_lines)
    return total
