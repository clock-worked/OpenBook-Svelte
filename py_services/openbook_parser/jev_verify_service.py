"""JEV cross-verification layer for heuristic dialogue attribution.

exp2 (run query): turn-taking is a RUN property, so gated targets are
grouped into maximal contiguous dialogue runs and each run is verified
with ONE batched call (one Choice question per line in the run, the
speculative fan-out pattern). Tagged lines inside a run act as anchors:
the model sees the full turn structure, but verdicts are applied to the
gated lines only.

Runs the JEV model as an independent verifier over lines the heuristic
parser attributed. Targets are selected by a risk gate, queried blind to
the heuristic's pick (independence keeps disagreement a real signal), and
verdicts are applied additively to each line's attribution.

FP-first policy (no silent flips):
- JEV error/timeout        -> keep heuristic (fail-open)
- JEV confidence < floor   -> record only
- agree, conf >= promote   -> confirm; promote a suggestion to an assert
- disagree, conf >= veto   -> downgrade to unknown-with-guess
- "None" (not dialogue)    -> downgrade to unknown-with-guess
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .dialogue_parser_service import DialogueLine
from .episode_decoder import HARD_REASONS

SCHEMA_VERSION = "jev-verify/1"
JEV_MODEL = "jev-latest"

# Turn-taking inheritance rules: the speaker was carried over from an
# earlier line in the scene, i.e. exactly the failure class JEV should check.
CARRYOVER_RULES = {
    "4b_tag_continuation",
    "4_contiguous_dialogue",
    "4c_carry_across_short_narration",
}

# Local (non-carryover) evidence on the top candidate. A line carrying any
# of these has its own tag/coref/descriptor/gender support and is NOT
# "turn-taking inheritance", so the carryover gate excludes it.
# Note: recent/previous paragraph *mention* cues are deliberately NOT local
# evidence — on a carried-over line the "recent mention" is usually the very
# speaker being inherited, so it must not shield the line from verification.
LOCAL_EVIDENCE_REASONS = HARD_REASONS | {
    "coref_support",
    "alias_strength",
    "learned_descriptor_match",
    "context_gender_match",
}

NOT_DIALOGUE = "None"
UNKNOWN_TOKENS = {"", "unknown", "unassigned", "narrator"}


@dataclass
class VerifyPolicy:
    gate: str = "carryover"  # "none" | "carryover" | "full"
    downgrade_conf: float = 0.60
    promote_conf: float = 0.70
    ignore_below: float = 0.40
    roster_size: int = 6
    window_chars: int = 1500
    max_calls: int = 60
    concurrency: int = 4
    retries: int = 2


# ---------------------------------------------------------------------------
# Line inspection helpers
# ---------------------------------------------------------------------------


def _norm(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _attribution(line: DialogueLine) -> Dict[str, object]:
    value = getattr(line, "attribution", None)
    return value if isinstance(value, dict) else {}


def _legacy_rule(line: DialogueLine) -> Optional[str]:
    rule = _attribution(line).get("legacyRule")
    return str(rule) if rule else None


def _candidate_rows(line: DialogueLine) -> List[dict]:
    rows = _attribution(line).get("candidates")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _top_candidate(line: DialogueLine) -> Optional[dict]:
    rows = _candidate_rows(line)
    return rows[0] if rows else None


def _top_reasons(line: DialogueLine) -> set:
    row = _top_candidate(line)
    if not row:
        return set()
    return {str(reason) for reason in row.get("reasons") or []}


def _top_confidence(line: DialogueLine) -> float:
    row = _top_candidate(line)
    try:
        return float((row or {}).get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _margin_to_second(line: DialogueLine) -> float:
    rows = _candidate_rows(line)
    if len(rows) < 2:
        return 1.0
    try:
        top = float(rows[0].get("confidence") or 0.0)
        second = float(rows[1].get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, top - second)


def _decoder_touched(line: DialogueLine) -> bool:
    trace = _attribution(line).get("decisionTrace")
    if not isinstance(trace, dict):
        return False
    return trace.get("overrideReason") == "episode_joint_decode"


def has_local_evidence(line: DialogueLine) -> bool:
    return bool(_top_reasons(line) & LOCAL_EVIDENCE_REASONS)


def is_unknown_speaker(name: Optional[str]) -> bool:
    return _norm(name) in UNKNOWN_TOKENS


# ---------------------------------------------------------------------------
# Risk gate
# ---------------------------------------------------------------------------


def select_targets(
    lines: Sequence[DialogueLine],
    gate: str = "carryover",
) -> List[Tuple[int, List[str]]]:
    """Return (line_index, risk_reasons) pairs for lines worth a JEV call."""
    if gate == "none":
        return []
    targets: List[Tuple[int, List[str]]] = []
    for index, line in enumerate(lines):
        if getattr(line, "line_type", None) != "dialogue":
            continue
        reasons: List[str] = []
        rule = _legacy_rule(line)
        if rule in CARRYOVER_RULES and not has_local_evidence(line):
            reasons.append("carryover_rule")
        if gate == "full":
            if getattr(line, "is_suggestion", False):
                reasons.append("already_suggestion")
            if _top_confidence(line) < 0.74:
                reasons.append("low_confidence")
            if _margin_to_second(line) < 0.14:
                reasons.append("tight_margin")
            if _decoder_touched(line):
                reasons.append("episode_decoder")
            if rule == "5_suggest_alternatives":
                reasons.append("suggestion_string")
        if reasons:
            targets.append((index, reasons))
    return targets


def group_runs(
    lines: Sequence[DialogueLine],
    target_indices,
) -> List[List[int]]:
    """Group target lines into maximal contiguous dialogue runs.

    A run is the longest stretch of adjacent lines that are all dialogue.
    Only runs containing at least one target are returned. Tagged lines
    inside a run are anchors: the model sees the full turn structure, but
    verdicts are applied to the gated lines only.
    """
    is_dialogue = [getattr(line, "line_type", None) == "dialogue" for line in lines]
    covered: set = set()
    runs: List[List[int]] = []
    for index in sorted(set(target_indices)):
        if index in covered:
            continue
        start = index
        while start - 1 >= 0 and is_dialogue[start - 1]:
            start -= 1
        end = index
        while end + 1 < len(lines) and is_dialogue[end + 1]:
            end += 1
        run = list(range(start, end + 1))
        runs.append(run)
        covered.update(run)
    return runs


def _last_speaker_by_index(lines: Sequence[DialogueLine]) -> Dict[int, Optional[str]]:
    resolved: Dict[int, Optional[str]] = {}
    current: Optional[str] = None
    for index, line in enumerate(lines):
        if getattr(line, "line_type", None) != "dialogue":
            resolved[index] = current
            continue
        name = str(getattr(line, "speaker", None) or "").strip()
        if not is_unknown_speaker(name):
            current = name
        resolved[index] = current
    return resolved


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------


def build_roster(
    line: DialogueLine,
    name_lookup: Dict[str, str],
    last_speaker: Optional[str],
    next_speaker: Optional[str],
    roster_size: int,
) -> "Dict[str, str]":
    """Trimmed roster: heuristic top candidates + neighbours, capped.

    Deliberately does NOT flag which candidate the parser currently asserts;
    JEV answers blind so disagreement stays a real signal.
    """
    ordered: List[str] = []

    def add(raw: Optional[str]) -> None:
        name = str(raw or "").strip()
        if not name or is_unknown_speaker(name):
            return
        canonical = name_lookup.get(_norm(name), name)
        for existing in ordered:
            if _norm(existing) == _norm(canonical):
                return
        ordered.append(canonical)

    for row in _candidate_rows(line)[:3]:
        add(row.get("name"))
    add(last_speaker)
    add(next_speaker)

    roster: "Dict[str, str]" = {}
    for name in ordered[:roster_size]:
        roster[name] = name
    return roster


def build_line_query(
    chapter_text: str,
    line: DialogueLine,
    roster: "Dict[str, str]",
    last_speaker: Optional[str],
    window_chars: int,
) -> Tuple[str, dict]:
    start = int(getattr(line, "span_start", 0) or 0)
    end = int(getattr(line, "span_end", 0) or 0)
    if end <= start or start >= len(chapter_text):
        raise ValueError(f"Invalid source span for line: {start}-{end}")

    context_start = max(0, start - window_chars)
    context_end = min(len(chapter_text), end + window_chars)
    state = (
        chapter_text[context_start:start]
        + "<quote>"
        + chapter_text[start:end]
        + "</quote>"
        + chapter_text[end:context_end]
    ).strip()
    if last_speaker:
        state = f"[Last speaker: {last_speaker}]\n{state}"

    criteria: "Dict[str, str]" = dict(roster)
    criteria[NOT_DIALOGUE] = "Narration / speaker not in list"
    questions = {
        "speaker": {
            "type": "choice",
            "instructions": "Who is the speaker of the dialogue in the <quote> tags?",
            "criteria": criteria,
        }
    }
    return state, questions


def build_run_query(
    chapter_text: str,
    run_lines: Sequence[DialogueLine],
    roster: "Dict[str, str]",
    window_chars: int,
) -> Tuple[str, dict]:
    """One batched query for a whole contiguous dialogue run.

    Every line's quote span is marked <q0>..<qN> in the state and one
    Choice question is asked per line. The instruction states the
    continuation rule literally: an untagged quote continuing the same
    speech as a nearby tagged quote belongs to that speaker.
    """
    if not run_lines:
        raise ValueError("Run query requires at least one line")
    # Bound the window by the FULL run box, not just the first/last line:
    # line spans are not guaranteed monotonic in index order, and a run can
    # be longer than the window itself.
    spans = [
        (
            int(getattr(line, "span_start", 0) or 0),
            int(getattr(line, "span_end", 0) or 0),
        )
        for line in run_lines
    ]
    run_start = min(start for start, _end in spans)
    run_end = max(end for _start, end in spans)
    if run_end <= run_start or run_start >= len(chapter_text):
        raise ValueError(f"Invalid run source span: {run_start}-{run_end}")

    context_start = max(0, run_start - window_chars)
    context_end = min(len(chapter_text), run_end + window_chars)
    state = chapter_text[context_start:context_end]

    marks: List[Tuple[int, int, int]] = []
    for position, line in enumerate(run_lines):
        start = int(getattr(line, "span_start", 0) or 0) - context_start
        end = int(getattr(line, "span_end", 0) or 0) - context_start
        if end <= start or start < 0 or end > len(state):
            raise ValueError(f"Invalid source span for run line {position}: {start}-{end}")
        marks.append((start, end, position))
    for start, end, position in sorted(marks, reverse=True):
        state = (
            state[:start]
            + f"<q{position}>"
            + state[start:end]
            + f"</q{position}>"
            + state[end:]
        )

    criteria: "Dict[str, str]" = dict(roster)
    criteria[NOT_DIALOGUE] = "Narration / speaker not in list"
    questions = {
        f"q{position}": {
            "type": "choice",
            "instructions": (
                f"Who is the speaker of the dialogue in <q{position}> tags? "
                "An untagged quote that continues the same speech as a nearby "
                "tagged quote is spoken by the same speaker."
            ),
            "criteria": criteria,
        }
        for position in range(len(run_lines))
    }
    return state, questions


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def _default_cache_dir() -> Path:
    override = os.getenv("JEV_VERIFY_CACHE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / ".cache" / "jev_verify"


def cache_key(state: str, questions: dict) -> str:
    payload = json.dumps(
        {"v": SCHEMA_VERSION, "model": JEV_MODEL, "state": state, "questions": questions},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class JevVerifyCache:
    def __init__(self, cache_dir: Optional[Path] = None, enabled: bool = True) -> None:
        self._dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self._enabled = enabled

    def get(self, key: str) -> Optional[dict]:
        if not self._enabled:
            return None
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, payload: dict) -> None:
        if not self._enabled:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with (self._dir / f"{key}.json").open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def parse_run_answer(response: Optional[dict], question_id: str) -> Tuple[Optional[str], float]:
    if not isinstance(response, dict):
        return None, 0.0
    answers = response.get("answers")
    answer = answers.get(question_id) if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        return None, 0.0
    choice = answer.get("choice")
    try:
        confidence = float(answer.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return (str(choice) if choice is not None else None), confidence


def parse_answer(response: Optional[dict]) -> Tuple[Optional[str], float]:
    return parse_run_answer(response, "speaker")


def decide_verdict(
    line: DialogueLine,
    choice: Optional[str],
    confidence: float,
    name_lookup: Dict[str, str],
    policy: VerifyPolicy,
) -> str:
    """FP-first decision table. Never flips an assert silently."""
    if choice is None:
        return "skip"
    if confidence < policy.ignore_below:
        return "note"
    if choice == NOT_DIALOGUE:
        return "downgrade"
    jev_name = str(choice).strip()
    heuristic_name = str(getattr(line, "speaker", None) or "").strip()
    same = name_lookup.get(_norm(jev_name), jev_name) == name_lookup.get(
        _norm(heuristic_name), heuristic_name
    )
    if same:
        return "confirm" if confidence >= policy.promote_conf else "note"
    return "downgrade" if confidence >= policy.downgrade_conf else "note"


def _unique_names(*names: Optional[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


def apply_verdict(
    line: DialogueLine,
    action: str,
    choice: Optional[str],
    confidence: float,
    name_lookup: Dict[str, str],
    block: Dict[str, object],
) -> None:
    """Apply a verdict additively; decisionTrace stays intact."""
    attribution = _attribution(line)
    attribution["jevVerification"] = block

    if action == "downgrade":
        previous_speaker = str(getattr(line, "speaker", None) or "").strip()
        jev_name = None
        if choice and choice != NOT_DIALOGUE:
            jev_name = name_lookup.get(_norm(str(choice)), str(choice).strip())
        line.is_suggestion = True
        if jev_name and not is_unknown_speaker(jev_name):
            # The JEV pick is the better guess: it goes first.
            line.speaker = jev_name
            line.suggestions = _unique_names(jev_name, *(line.suggestions or []))
            candidates: List[dict] = [
                {
                    "name": jev_name,
                    "confidence": round(float(confidence), 4),
                    "reasons": ["jev_verification_disagreement"],
                }
            ]
            candidates.extend(
                row
                for row in _candidate_rows(line)
                if _norm(row.get("name")) != _norm(jev_name)
            )
            attribution["candidates"] = candidates
        else:
            # JEV says "not dialogue": keep the heuristic pick as a guess.
            line.speaker = previous_speaker
            line.suggestions = _unique_names(previous_speaker, *(line.suggestions or []))
    elif action == "confirm" and getattr(line, "is_suggestion", False):
        line.is_suggestion = False
        rows = _candidate_rows(line)
        if rows:
            reasons = rows[0].setdefault("reasons", [])
            if "jev_verification_confirmed" not in reasons:
                reasons.append("jev_verification_confirmed")
    line.attribution = attribution


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _ask_with_retries(client, state: str, questions: dict, retries: int) -> dict:
    delay = 1.0
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return client.ask(state, questions)
        except Exception as exc:  # noqa: BLE001 - any transport failure
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(delay)
            delay *= 2
    raise last_error if last_error else RuntimeError("JEV request failed")


def verify_chapter(
    lines: Sequence[DialogueLine],
    chapter_text: str,
    name_lookup: Dict[str, str],
    *,
    policy: Optional[VerifyPolicy] = None,
    client=None,
    cache: Optional[JevVerifyCache] = None,
    log: Callable[[str], None] = print,
) -> Dict[str, object]:
    """Verify risky lines with JEV and apply verdicts in place.

    Returns a summary: gate, targets, modelCalls, cacheHits, errors,
    per-action counts, and the ids of downgraded/promoted lines.
    """
    policy = policy or VerifyPolicy()
    started = time.monotonic()
    summary: Dict[str, object] = {
        "gate": policy.gate,
        "targets": 0,
        "runs": 0,
        "modelCalls": 0,
        "cacheHits": 0,
        "errors": 0,
        "actions": {},
        "downgradedLineIds": [],
        "promotedLineIds": [],
        "elapsedMs": 0,
    }
    if client is None:
        summary["error"] = "no JEV client provided"
        return summary

    targets = select_targets(lines, policy.gate)
    summary["targets"] = len(targets)
    if not targets:
        summary["elapsedMs"] = int((time.monotonic() - started) * 1000)
        return summary

    # One batched call per contiguous dialogue run (fan-out pattern).
    runs = group_runs(lines, [index for index, _ in targets])
    if policy.max_calls > 0:
        runs = runs[: policy.max_calls]
    summary["runs"] = len(runs)

    if cache is None:
        cache = JevVerifyCache()
    last_speakers = _last_speaker_by_index(lines)
    next_speakers: Dict[int, Optional[str]] = {}
    upcoming: Optional[str] = None
    for index in range(len(lines) - 1, -1, -1):
        next_speakers[index] = upcoming
        line = lines[index]
        if getattr(line, "line_type", None) != "dialogue":
            continue
        name = str(getattr(line, "speaker", None) or "").strip()
        if not is_unknown_speaker(name):
            upcoming = name

    target_index_set = {index for index, _ in targets}
    jobs: List[Dict[str, object]] = []
    for run in runs:
        run_lines = [lines[index] for index in run]
        # Roster: union of the gated lines' rosters (anchors contribute no
        # options of their own — the model sees them in the state text).
        roster: "Dict[str, str]" = {}
        for index in run:
            if index not in target_index_set:
                continue
            for name in build_roster(
                lines[index],
                name_lookup,
                last_speakers.get(index),
                next_speakers.get(index),
                policy.roster_size,
            ):
                roster.setdefault(name, name)
        if not roster:
            for name in build_roster(
                run_lines[0],
                name_lookup,
                last_speakers.get(run[0]),
                next_speakers.get(run[0]),
                policy.roster_size,
            ):
                roster.setdefault(name, name)
        roster = dict(list(roster.items())[: policy.roster_size])
        state, questions = build_run_query(chapter_text, run_lines, roster, policy.window_chars)
        jobs.append({"run": run, "state": state, "questions": questions})

    def run_job(
        job: Dict[str, object],
    ) -> Tuple[List[int], Dict[int, Tuple[Optional[str], float]], bool, Optional[str]]:
        run = [int(index) for index in job["run"]]
        key = cache_key(str(job["state"]), job["questions"])
        try:
            cached = cache.get(key)
            if cached is not None:
                response = cached
                cache_hit = True
            else:
                response = _ask_with_retries(client, job["state"], job["questions"], policy.retries)
                cache.put(key, response)
                cache_hit = False
            per_line = {
                index: parse_run_answer(response, f"q{position}")
                for position, index in enumerate(run)
            }
            return run, per_line, cache_hit, None
        except Exception as exc:  # noqa: BLE001 - fail-open per run
            return run, {index: (None, 0.0) for index in run}, False, str(exc)

    outcomes: Dict[int, Tuple[Optional[str], float, bool, Optional[str]]] = {}
    workers = max(1, int(policy.concurrency))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_job, job) for job in jobs]
        for future in futures:
            run, per_line, cache_hit, error = future.result()
            for index in run:
                choice, confidence = per_line[index]
                outcomes[index] = (choice, confidence, cache_hit, error)
            # Call accounting is per run (one model call per run).
            if error is not None:
                summary["errors"] = int(summary["errors"]) + 1
            elif cache_hit:
                summary["cacheHits"] = int(summary["cacheHits"]) + 1
            else:
                summary["modelCalls"] = int(summary["modelCalls"]) + 1

    action_counts: Dict[str, int] = {}
    for index, gate_reasons in targets:
        if index not in outcomes:
            continue
        choice, confidence, cache_hit, error = outcomes[index]
        line = lines[index]
        block: Dict[str, object] = {
            "called": error is None,
            "cacheHit": cache_hit,
            "query": "run",
            "choice": choice,
            "confidence": round(float(confidence), 4),
            "heuristicSpeaker": str(getattr(line, "speaker", None) or "").strip(),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if error is not None:
            action = "skip"
            block["error"] = error
        else:
            action = decide_verdict(line, choice, confidence, name_lookup, policy)
        action_counts[action] = action_counts.get(action, 0) + 1
        line_id = index + 1
        if action == "downgrade":
            summary["downgradedLineIds"].append(line_id)
        elif action == "confirm" and not getattr(line, "is_suggestion", False):
            summary["promotedLineIds"].append(line_id)
        apply_verdict(line, action, choice, confidence, name_lookup, block)
        log(f"[jev-verify] line {line_id}: {action} (choice={choice}, conf={confidence:.2f})")

    summary["actions"] = action_counts
    summary["elapsedMs"] = int((time.monotonic() - started) * 1000)
    return summary
