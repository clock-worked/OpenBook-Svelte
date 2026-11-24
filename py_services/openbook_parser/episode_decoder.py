"""Explainable episode-level joint decoding for uncertain dialogue turns."""

from __future__ import annotations

from dataclasses import dataclass
from math import log
from typing import Iterable, Optional

from .attribution_decision import (
    apply_decision,
    episode_override_decision,
    has_hard_attribution,
)

EPSILON = 1e-6


@dataclass(frozen=True)
class DecodeState:
    score: float
    path: tuple[str, ...]


def _norm(value: object) -> str:
    return str(value or "").strip().lower()


def _is_hard(line) -> bool:
    return has_hard_attribution(line)


def _candidate_scores(line) -> dict[str, tuple[str, float]]:
    attribution = getattr(line, "attribution", None)
    rows = attribution.get("candidates") if isinstance(attribution, dict) else None
    result: dict[str, tuple[str, float]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            key = _norm(name)
            confidence = max(EPSILON, min(1.0, float(row.get("confidence") or 0.0)))
            current = result.get(key)
            if current is None or confidence > current[1]:
                result[key] = (name, confidence)
    if not result:
        name = str(getattr(line, "speaker", None) or "").strip()
        if name:
            result[_norm(name)] = (name, 0.5)
    return result


def _paragraph(line) -> Optional[int]:
    value = getattr(line, "paragraph_index", None)
    return value if isinstance(value, int) and value >= 0 else None


def _dialogue_episodes(lines: list) -> list[list]:
    dialogue = [line for line in lines if getattr(line, "line_type", None) == "dialogue"]
    if not dialogue:
        return []
    episodes: list[list] = [[dialogue[0]]]
    for line in dialogue[1:]:
        previous = episodes[-1][-1]
        left = _paragraph(previous)
        right = _paragraph(line)
        if left is not None and right is not None and right - left > 1:
            episodes.append([line])
        else:
            episodes[-1].append(line)
    return episodes


def _participant_set(episode: list) -> set[str]:
    participants: set[str] = set()
    for line in episode:
        if _is_hard(line):
            key = _norm(getattr(line, "speaker", None))
            if key:
                participants.add(key)
    for line in episode:
        participants.update(_candidate_scores(line).keys())
    return participants


def _decode_two_person_episode(episode: list, participants: set[str]) -> tuple[str, ...]:
    states: dict[str, DecodeState] = {}
    display: dict[str, str] = {}
    for index, line in enumerate(episode):
        candidates = _candidate_scores(line)
        for key, (name, _confidence) in candidates.items():
            display.setdefault(key, name)
        if _is_hard(line) or getattr(line, "is_suggestion", False) is not True:
            hard_key = _norm(getattr(line, "speaker", None))
            candidates = {hard_key: (str(getattr(line, "speaker")), 1.0)} if hard_key else candidates
        candidates = {key: row for key, row in candidates.items() if key in participants}
        if not candidates:
            return tuple(_norm(getattr(line, "speaker", None)) for line in episode)

        next_states: dict[str, DecodeState] = {}
        for key, (_name, confidence) in candidates.items():
            emission = log(max(EPSILON, confidence))
            if index == 0:
                next_states[key] = DecodeState(emission, (key,))
                continue
            best: Optional[DecodeState] = None
            for previous_key, previous in states.items():
                # Soft alternation, never a hard constraint.
                transition = 0.28 if previous_key != key else -0.28
                proposal = DecodeState(previous.score + emission + transition, previous.path + (key,))
                if best is None or proposal.score > best.score:
                    best = proposal
            if best is not None:
                next_states[key] = best
        states = next_states
    if not states:
        return tuple(_norm(getattr(line, "speaker", None)) for line in episode)
    return max(states.values(), key=lambda item: item.score).path


def _apply_override(line, chosen_key: str, *, participants: set[str]) -> bool:
    if getattr(line, "is_suggestion", False) is not True:
        return False
    candidates = _candidate_scores(line)
    selected = candidates.get(chosen_key)
    if selected is None:
        return False
    chosen_name = selected[0]
    if _norm(getattr(line, "speaker", None)) == chosen_key:
        return False
    original_name = str(getattr(line, "speaker", None) or "").strip()
    apply_decision(line, episode_override_decision(
        chosen_name,
        trace_fields={
            "episodeDecoder": {
                "algorithm": "two_participant_viterbi",
                "transitionDifferent": 0.28,
                "transitionSame": -0.28,
                "originalCandidate": original_name,
                "selectedCandidate": chosen_name,
                "participants": sorted(participants),
            },
        },
    ))
    return True


def decode_dialogue_episodes(lines: Iterable) -> int:
    """Jointly decode uncertain turns in strict two-participant episodes.

    Episodes with more or fewer than two candidate participants are left alone.
    Hard explicit attributions are immutable and every change stores provenance.
    """
    changes = 0
    for episode in _dialogue_episodes(list(lines)):
        hard_anchor_count = sum(1 for line in episode if _is_hard(line))
        if hard_anchor_count < 2:
            continue
        participants = _participant_set(episode)
        if len(participants) != 2:
            continue
        path = _decode_two_person_episode(episode, participants)
        for line, chosen_key in zip(episode, path):
            if _is_hard(line):
                continue
            if _apply_override(
                line,
                chosen_key,
                participants=participants,
            ):
                changes += 1
    return changes
