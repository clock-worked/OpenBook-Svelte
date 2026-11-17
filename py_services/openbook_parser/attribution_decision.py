"""Owner of the attribution decision record (ADR-0013).

"Who spoke this line, and why" is recorded on each parser line as the
``attribution`` dict: a ranked candidate list plus a ``decisionTrace``. This
module is the single owner of that record. It owns three things:

1. **The trace contract.** Every writer builds a :class:`Decision` value and
   hands it to :func:`apply_decision`. The stage differences (a ranked initial
   decision, promoting an existing candidate, prepending a synthetic
   candidate, an additive verdict that leaves the trace intact) live in the
   decision *data*, not in per-writer code. ``apply_decision`` is the only
   place the trace invariants are maintained (see
   :func:`trace_invariant_violations`):

   - candidate list order == the decision's ranking (chosen candidate first)
   - ``decisionTrace.selectedCandidate`` == ``line.speaker``
   - ``decisionTrace.selectedReasons`` == the top candidate's reasons
   - override reasons are appended (to the top candidate and the trace)
   - ``genderConflict`` is recomputed from the selected reasons

   A ``verdict`` is the deliberate exception: it is additive (ADR-0006) and
   never rewrites the trace's decision fields.

2. **The rule registry.** Every rule/reason name the attribution pipeline can
   emit (parser sieves, scoring reasons, override reasons, the dialogue-AI
   deterministic reason codes) is registered in :data:`RULE_REGISTRY` and
   belongs to exactly one of four categories (:data:`RULE_CATEGORIES`). The
   consumer gate sets (``HARD_REASONS``, ``CARRYOVER_RULES``,
   ``LOCAL_EVIDENCE_REASONS``, ...) are derived from the registry; no module
   keeps its own rule-name set.

3. **The assert/suggestion policy spec.** One spec (:data:`POLICY_THRESHOLDS`
   + :data:`POLICY_RULES`), applied at each edge by :func:`evaluate_policy`:
   the parser applies its edge-scoped rules in ``rank_candidates``; the
   frontend applies its edge-scoped rules (the TS mirror,
   ``apps/desktop/src/lib/services/attributionPolicy.ts``) to the post-collapse
   candidate set. Both sides are pinned by ``tests/attribution_policy_vectors.json``.

This module has no dependencies so it imports in any context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

#: Explicit, line-local speech-tag evidence that names the speaker. The episode
#: decoder and the JEV verify gate never override a line carrying one.
HARD = "HARD"
#: The speaker was inherited from surrounding turns / context (turn-taking,
#: continuity, recency) rather than evidenced on the line itself.
CARRYOVER = "CARRYOVER"
#: Line-local evidence or adjustment that is not an explicit naming tag
#: (coreference, alias, descriptor, gender cues, penalties, external opinions).
LOCAL_EVIDENCE = "LOCAL_EVIDENCE"
#: The deterministic first-person / protagonist voice rules. The frontend
#: exempts a line decided by one of these from abstaining.
FIRST_PERSON = "FIRST_PERSON"

#: The four exhaustive categories. Every registered rule has exactly one.
RULE_CATEGORIES: Tuple[str, ...] = (HARD, CARRYOVER, LOCAL_EVIDENCE, FIRST_PERSON)

#: Precedence used to collapse a decision's selected reasons into the single
#: ``ruleCategory`` carried on the wire: the strongest category present wins.
#: FIRST_PERSON first keeps ``ruleCategory == FIRST_PERSON`` equivalent to
#: "any selected reason is a first-person rule" (the frontend exemption).
CATEGORY_PRECEDENCE: Tuple[str, ...] = (FIRST_PERSON, HARD, LOCAL_EVIDENCE, CARRYOVER)

# Where a rule name is emitted.
ORIGIN_SIEVE = "sieve"  # legacy parser pass name (attribution["legacyRule"])
ORIGIN_SCORING = "scoring"  # rank_candidates candidate reason
ORIGIN_OVERRIDE = "override"  # a later stage's override / verdict reason
ORIGIN_AI = "ai"  # dialogue-AI deterministic reason code (not on the parser trace)
PARSER_ORIGINS: FrozenSet[str] = frozenset({ORIGIN_SIEVE, ORIGIN_SCORING, ORIGIN_OVERRIDE})

# Consumer gates. A gate is a consumer-side membership the registry also owns,
# for consumers whose gate is narrower than a whole category.
#: JEV verify: a legacy rule whose lines are carry-over targets.
GATE_JEV_CARRYOVER_TARGET = "jev_carryover_target"
#: JEV verify: a top-candidate reason that shields a carry-over line (in
#: addition to every HARD reason).
GATE_JEV_LOCAL_SHIELD = "jev_local_shield"
#: rank_candidates: a top-candidate reason that counts as positive support.
GATE_RANK_SUPPORT = "rank_support"


@dataclass(frozen=True)
class RuleSpec:
    name: str
    category: str
    origin: str
    gates: FrozenSet[str] = frozenset()


def _spec(name: str, category: str, origin: str, *gates: str) -> RuleSpec:
    return RuleSpec(name=name, category=category, origin=origin, gates=frozenset(gates))


# Override / verdict reasons written by the later parser stages. Writers use
# these constants rather than string literals.
SAME_PARAGRAPH_CONTEXT_CONSISTENCY = "same_paragraph_context_consistency"
EPISODE_JOINT_DECODE = "episode_joint_decode"
MODERNBOOKNLP_JOINT_ATTRIBUTION = "modernbooknlp_joint_attribution"
JEV_VERIFICATION_DISAGREEMENT = "jev_verification_disagreement"
JEV_VERIFICATION_CONFIRMED = "jev_verification_confirmed"
CLOSED_WORLD_UNRESOLVED = "closed_world_unresolved"

#: The override reasons the parser stages can write (see the constants above).
OVERRIDE_REASONS: Tuple[str, ...] = (
    SAME_PARAGRAPH_CONTEXT_CONSISTENCY,
    EPISODE_JOINT_DECODE,
    MODERNBOOKNLP_JOINT_ATTRIBUTION,
    JEV_VERIFICATION_DISAGREEMENT,
    JEV_VERIFICATION_CONFIRMED,
    CLOSED_WORLD_UNRESOLVED,
)

_RULE_SPECS: Tuple[RuleSpec, ...] = (
    # --- Legacy parser sieves (dialogue_parser_service) ---
    _spec("0p_protagonist_first_person", FIRST_PERSON, ORIGIN_SIEVE),
    _spec("first_person_override", FIRST_PERSON, ORIGIN_SIEVE),
    _spec("0a_narrator_identity", LOCAL_EVIDENCE, ORIGIN_SIEVE),
    _spec("0b_narrator_vocative", LOCAL_EVIDENCE, ORIGIN_SIEVE),
    _spec("1_coreference", LOCAL_EVIDENCE, ORIGIN_SIEVE),
    _spec("1b_backward_coreference", LOCAL_EVIDENCE, ORIGIN_SIEVE),
    _spec("2_forward_explicit_tags", HARD, ORIGIN_SIEVE),
    _spec("2b_backward_explicit_tags", HARD, ORIGIN_SIEVE),
    _spec("4b_tag_continuation", CARRYOVER, ORIGIN_SIEVE, GATE_JEV_CARRYOVER_TARGET),
    _spec("4_contiguous_dialogue", CARRYOVER, ORIGIN_SIEVE, GATE_JEV_CARRYOVER_TARGET),
    _spec("4c_carry_across_short_narration", CARRYOVER, ORIGIN_SIEVE, GATE_JEV_CARRYOVER_TARGET),
    _spec("5_suggest_alternatives", CARRYOVER, ORIGIN_SIEVE),
    # --- Scoring reasons (attribution_passes.rank_candidates) ---
    _spec("parser_match", LOCAL_EVIDENCE, ORIGIN_SCORING, GATE_RANK_SUPPORT),
    _spec("legacy_match", LOCAL_EVIDENCE, ORIGIN_SCORING, GATE_RANK_SUPPORT),
    _spec("legacy_anchor_boost", LOCAL_EVIDENCE, ORIGIN_SCORING),
    _spec("same_paragraph_continuation", CARRYOVER, ORIGIN_SCORING, GATE_RANK_SUPPORT),
    _spec("prev_continuity", CARRYOVER, ORIGIN_SCORING),
    _spec("next_continuity", CARRYOVER, ORIGIN_SCORING),
    _spec("two_sided_continuity", CARRYOVER, ORIGIN_SCORING),
    _spec("coref_support", LOCAL_EVIDENCE, ORIGIN_SCORING, GATE_RANK_SUPPORT, GATE_JEV_LOCAL_SHIELD),
    _spec("alias_strength", LOCAL_EVIDENCE, ORIGIN_SCORING, GATE_RANK_SUPPORT, GATE_JEV_LOCAL_SHIELD),
    _spec(
        "learned_descriptor_match", LOCAL_EVIDENCE, ORIGIN_SCORING,
        GATE_RANK_SUPPORT, GATE_JEV_LOCAL_SHIELD,
    ),
    _spec("post_quote_attribution", HARD, ORIGIN_SCORING, GATE_RANK_SUPPORT),
    _spec("post_quote_mention", HARD, ORIGIN_SCORING),
    # Recency cues are carry-over, not local evidence: on a carried-over line
    # the "recent mention" is usually the very speaker being inherited, so it
    # must not shield the line from JEV verification.
    _spec("recent_named_mention", CARRYOVER, ORIGIN_SCORING, GATE_RANK_SUPPORT),
    _spec("previous_paragraph_named_mention", CARRYOVER, ORIGIN_SCORING, GATE_RANK_SUPPORT),
    _spec("speech_verb", LOCAL_EVIDENCE, ORIGIN_SCORING, GATE_RANK_SUPPORT),
    _spec("fallback_penalty", LOCAL_EVIDENCE, ORIGIN_SCORING),
    _spec("generic_label_penalty", LOCAL_EVIDENCE, ORIGIN_SCORING),
    _spec("addressed_target_penalty", LOCAL_EVIDENCE, ORIGIN_SCORING),
    _spec("self_name_mention_penalty", LOCAL_EVIDENCE, ORIGIN_SCORING),
    _spec(
        "context_gender_match", LOCAL_EVIDENCE, ORIGIN_SCORING,
        GATE_RANK_SUPPORT, GATE_JEV_LOCAL_SHIELD,
    ),
    _spec("context_gender_conflict", LOCAL_EVIDENCE, ORIGIN_SCORING),
    _spec("question_next_speaker_penalty", LOCAL_EVIDENCE, ORIGIN_SCORING),
    # --- Override / verdict reasons (later parser stages) ---
    _spec(SAME_PARAGRAPH_CONTEXT_CONSISTENCY, CARRYOVER, ORIGIN_OVERRIDE),
    _spec(EPISODE_JOINT_DECODE, CARRYOVER, ORIGIN_OVERRIDE),
    _spec(MODERNBOOKNLP_JOINT_ATTRIBUTION, LOCAL_EVIDENCE, ORIGIN_OVERRIDE),
    _spec(JEV_VERIFICATION_DISAGREEMENT, LOCAL_EVIDENCE, ORIGIN_OVERRIDE),
    _spec(JEV_VERIFICATION_CONFIRMED, LOCAL_EVIDENCE, ORIGIN_OVERRIDE),
    _spec(CLOSED_WORLD_UNRESOLVED, LOCAL_EVIDENCE, ORIGIN_OVERRIDE),
    # --- Dialogue-AI deterministic reason codes (dialogue_ai_service) ---
    # Registered and categorized here; the override chain itself is
    # restructured in candidate 10, not here.
    _spec("explicit_cue_resolution", HARD, ORIGIN_AI),
    _spec("deterministic_override", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("self_identification", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("future_self_identification", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("shared_paragraph_cue", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("generic_surface_anchor", CARRYOVER, ORIGIN_AI),
    _spec("narrative_anchor_resolution", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("question_reply_turn_taking", CARRYOVER, ORIGIN_AI),
    _spec("next_narration_speaker", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("previous_narrator_carryover", CARRYOVER, ORIGIN_AI),
    _spec("adjacent_same_speaker_continuation", CARRYOVER, ORIGIN_AI),
    _spec("previous_explicit_cue", CARRYOVER, ORIGIN_AI),
    _spec("next_narration_attribution", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("continuation_punctuation", CARRYOVER, ORIGIN_AI),
    _spec("question_precedes_explicit_reply", CARRYOVER, ORIGIN_AI),
    _spec("recent_turn_alternation", CARRYOVER, ORIGIN_AI),
    _spec("alignment_uncertain", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("generic_character_guard", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("ambiguous_unknown", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("dry_run", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("model_unconfigured", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("step_limit_reached", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("assist_failed", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("matched_existing_name", LOCAL_EVIDENCE, ORIGIN_AI),
    _spec("resolved_existing_role", LOCAL_EVIDENCE, ORIGIN_AI),
)

#: name -> RuleSpec for every registered rule.
RULE_REGISTRY: Dict[str, RuleSpec] = {spec.name: spec for spec in _RULE_SPECS}

#: Dynamic rule families, matched by prefix (e.g. ``cue_verb_said``).
RULE_PREFIXES: Tuple[RuleSpec, ...] = (
    _spec("cue_verb_", LOCAL_EVIDENCE, ORIGIN_AI),
)


def rule_spec(name: object) -> Optional[RuleSpec]:
    """The registry entry for ``name`` (exact, then prefix), or None."""
    key = str(name or "")
    spec = RULE_REGISTRY.get(key)
    if spec is not None:
        return spec
    for prefix in RULE_PREFIXES:
        if key.startswith(prefix.name) and len(key) > len(prefix.name):
            return prefix
    return None


def rule_category(name: object) -> Optional[str]:
    """Category of one rule name, or None for an unregistered name."""
    spec = rule_spec(name)
    return spec.category if spec else None


def rules_in(category: str, origins: Iterable[str] = PARSER_ORIGINS) -> FrozenSet[str]:
    """Registered rule names in ``category`` emitted from ``origins``."""
    allowed = frozenset(origins)
    return frozenset(
        spec.name
        for spec in _RULE_SPECS
        if spec.category == category and spec.origin in allowed
    )


def rules_gated(gate: str) -> FrozenSet[str]:
    """Registered rule names carrying the consumer ``gate``."""
    return frozenset(spec.name for spec in _RULE_SPECS if gate in spec.gates)


# Derived consumer sets (parser-trace vocabulary only).
HARD_REASONS: FrozenSet[str] = rules_in(HARD)
FIRST_PERSON_RULES: FrozenSet[str] = rules_in(FIRST_PERSON)
#: A line decided by one of these has explicit local attribution: later
#: stages (modernbooknlp, same-paragraph consistency) must not override it.
EXPLICIT_LOCAL_RULES: FrozenSet[str] = HARD_REASONS | FIRST_PERSON_RULES
CARRYOVER_RULES: FrozenSet[str] = rules_gated(GATE_JEV_CARRYOVER_TARGET)
LOCAL_EVIDENCE_REASONS: FrozenSet[str] = HARD_REASONS | rules_gated(GATE_JEV_LOCAL_SHIELD)
RANK_SUPPORT_REASONS: FrozenSet[str] = rules_gated(GATE_RANK_SUPPORT)


def decision_rule_category(reasons: Iterable[object]) -> Optional[str]:
    """Collapse a decision's selected reasons to one category (or None).

    The strongest category present wins, per :data:`CATEGORY_PRECEDENCE`.
    Unregistered reasons are ignored.
    """
    present = {rule_category(reason) for reason in reasons or []}
    for category in CATEGORY_PRECEDENCE:
        if category in present:
            return category
    return None


# ---------------------------------------------------------------------------
# Assert/suggestion policy spec
# ---------------------------------------------------------------------------

EDGE_PARSER = "parser"
EDGE_FRONTEND = "frontend"
POLICY_EDGES: Tuple[str, ...] = (EDGE_PARSER, EDGE_FRONTEND)

#: Thresholds of the shared spec. Mirrored in attributionPolicy.ts and pinned
#: by the ``spec`` block of tests/attribution_policy_vectors.json.
POLICY_THRESHOLDS: Mapping[str, float] = {
    "assertMinTop": 0.74,
    "assertMinMargin": 0.14,
    "unsupportedMinTop": 0.80,
    "contestedMinTop": 0.86,
}


@dataclass(frozen=True)
class PolicyRule:
    """A named reason to keep a decision a suggestion (not an assert)."""

    name: str
    edges: Tuple[str, ...]


#: Every suggestion rule of the spec, each scoped to the edge(s) applying it.
#: A decision is a suggestion at an edge when any of that edge's rules fires,
#: unless one of the edge's exemptions holds.
POLICY_RULES: Tuple[PolicyRule, ...] = (
    PolicyRule("low_top", (EDGE_PARSER, EDGE_FRONTEND)),  # top < assertMinTop
    PolicyRule("tight_margin", (EDGE_PARSER,)),  # top - second < assertMinMargin
    PolicyRule("unsupported_top", (EDGE_PARSER,)),  # no support reason and top < unsupportedMinTop
    PolicyRule("fallback_alignment", (EDGE_PARSER,)),
    PolicyRule("question_next_speaker_conflict", (EDGE_PARSER,)),
    PolicyRule("gender_conflict", (EDGE_PARSER,)),
    PolicyRule("generic_top_choice", (EDGE_PARSER,)),
    # Frontend edge: the FE may un-assert what the parser asserted, on the
    # post-collapse candidate set it sees.
    PolicyRule("parser_suggestion", (EDGE_FRONTEND,)),
    PolicyRule("contested_top", (EDGE_FRONTEND,)),  # >1 candidate and top < contestedMinTop
    PolicyRule("no_named_candidates", (EDGE_FRONTEND,)),
    PolicyRule("speaker_blocked", (EDGE_FRONTEND,)),
)

#: Exemptions per edge: when one holds, the decision asserts regardless.
POLICY_EXEMPTIONS: Mapping[str, Tuple[str, ...]] = {
    EDGE_PARSER: (),
    EDGE_FRONTEND: ("first_person",),
}


@dataclass(frozen=True)
class PolicyFacts:
    """Inputs to the policy at one edge (only the edge's rules read theirs)."""

    top: float = 0.0
    second: float = 0.0
    candidate_count: int = 0
    used_fallback_alignment: bool = False
    question_next_speaker_conflict: bool = False
    gender_conflict: bool = False
    generic_top_choice: bool = False
    has_support_reason: bool = False
    parser_suggestion: bool = False
    has_named_candidates: bool = True
    speaker_blocked: bool = False
    first_person: bool = False

    @classmethod
    def from_confidences(cls, confidences: Sequence[float], **flags: bool) -> "PolicyFacts":
        """Facts for a ranked confidence list (top first)."""
        values = [float(value) for value in confidences]
        return cls(
            top=values[0] if values else 0.0,
            second=values[1] if len(values) > 1 else 0.0,
            candidate_count=len(values),
            **flags,
        )


@dataclass(frozen=True)
class PolicyOutcome:
    is_suggestion: bool
    fired_rules: Tuple[str, ...]
    exempted_by: Optional[str] = None


def _rule_fires(name: str, facts: PolicyFacts) -> bool:
    thresholds = POLICY_THRESHOLDS
    if name == "low_top":
        return facts.top < thresholds["assertMinTop"]
    if name == "tight_margin":
        return (facts.top - facts.second) < thresholds["assertMinMargin"]
    if name == "unsupported_top":
        return not facts.has_support_reason and facts.top < thresholds["unsupportedMinTop"]
    if name == "fallback_alignment":
        return facts.used_fallback_alignment
    if name == "question_next_speaker_conflict":
        return facts.question_next_speaker_conflict
    if name == "gender_conflict":
        return facts.gender_conflict
    if name == "generic_top_choice":
        return facts.generic_top_choice
    if name == "parser_suggestion":
        return facts.parser_suggestion
    if name == "contested_top":
        return facts.candidate_count > 1 and facts.top < thresholds["contestedMinTop"]
    if name == "no_named_candidates":
        return not facts.has_named_candidates
    if name == "speaker_blocked":
        return facts.speaker_blocked
    raise KeyError(f"unknown policy rule: {name}")


def _exemption_holds(name: str, facts: PolicyFacts) -> bool:
    if name == "first_person":
        return facts.first_person
    raise KeyError(f"unknown policy exemption: {name}")


def evaluate_policy(edge: str, facts: PolicyFacts) -> PolicyOutcome:
    """Apply the shared spec's ``edge``-scoped rules to ``facts``."""
    if edge not in POLICY_EDGES:
        raise ValueError(f"unknown policy edge: {edge}")
    fired = tuple(
        rule.name for rule in POLICY_RULES if edge in rule.edges and _rule_fires(rule.name, facts)
    )
    for exemption in POLICY_EXEMPTIONS.get(edge, ()):
        if _exemption_holds(exemption, facts):
            return PolicyOutcome(is_suggestion=False, fired_rules=fired, exempted_by=exemption)
    return PolicyOutcome(is_suggestion=bool(fired), fired_rules=fired)


# ---------------------------------------------------------------------------
# Trace contract
# ---------------------------------------------------------------------------

KIND_INITIAL = "initial"
KIND_OVERRIDE = "override"
KIND_EPISODE_OVERRIDE = "episode_override"
KIND_VERDICT = "verdict"
DECISION_KINDS: Tuple[str, ...] = (KIND_INITIAL, KIND_OVERRIDE, KIND_EPISODE_OVERRIDE, KIND_VERDICT)

#: Candidates are the decision's ranking, as given (initial decisions).
PLACEMENT_RANKED = "ranked"
#: The chosen candidate is an existing row, moved to the front.
PLACEMENT_PROMOTE = "promote"
#: A synthetic row for the chosen candidate is prepended; the chosen name's
#: other rows are dropped.
PLACEMENT_PREPEND = "prepend"
#: The decision's reasons are appended to the existing top row.
PLACEMENT_ANNOTATE_TOP = "annotate_top"
#: The candidate list is left as it is.
PLACEMENT_KEEP = "keep"

#: decisionTrace fields the owner writes as the decision itself (as opposed to
#: per-stage provenance blocks such as ``signals`` or ``episodeDecoder``).
TRACE_DECISION_FIELDS: Tuple[str, ...] = (
    "selectedCandidate",
    "selectedReasons",
    "overrideReason",
)


@dataclass(frozen=True)
class CandidateRow:
    name: str
    confidence: float
    reasons: Tuple[str, ...] = ()

    def to_wire(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "confidence": round(float(self.confidence), 4),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class Decision:
    """One attribution decision, as data. Build with the ``*_decision`` helpers."""

    kind: str
    stage: str
    #: The chosen speaker (None: no decision / keep the line's speaker).
    chosen: Optional[str] = None
    #: The reasons this decision contributes: an override reason, a synthetic
    #: row's reasons, or (initial decision without candidates) the trace's
    #: selected reasons.
    reasons: Tuple[str, ...] = ()
    placement: str = PLACEMENT_KEEP
    #: Initial: the ranked rows. Prepend: the single synthetic row.
    candidates: Tuple[CandidateRow, ...] = ()
    #: New ``line.is_suggestion`` (None: leave it).
    is_suggestion: Optional[bool] = None
    #: Explicit replacement for ``line.suggestions`` (None: see ``promote``).
    suggestions: Optional[Tuple[str, ...]] = None
    #: Put the speaker first in ``line.suggestions`` (deduplicated).
    promote: bool = False
    #: Line speaker when the decision has no chosen candidate.
    fallback_speaker: Optional[str] = None
    #: Top-level attribution fields (parserBackend, legacyRule, contextGender,
    #: jevVerification, ...), written in order.
    attribution_fields: Tuple[Tuple[str, object], ...] = ()
    #: Per-stage provenance written into the trace (signals, rawCandidatePool,
    #: episodeDecoder, modernBookNLP, ...), in order.
    trace_fields: Tuple[Tuple[str, object], ...] = ()
    #: Whether the attribution carries a decisionTrace at all.
    with_trace: bool = True


def _fields(mapping: Optional[Mapping[str, object]]) -> Tuple[Tuple[str, object], ...]:
    return tuple((mapping or {}).items())


def _rows(candidates: Iterable[object]) -> Tuple[CandidateRow, ...]:
    rows: List[CandidateRow] = []
    for candidate in candidates or []:
        if isinstance(candidate, CandidateRow):
            rows.append(candidate)
        elif isinstance(candidate, Mapping):
            rows.append(CandidateRow(
                name=str(candidate.get("name") or ""),
                confidence=float(candidate.get("confidence") or 0.0),
                reasons=tuple(str(reason) for reason in candidate.get("reasons") or []),
            ))
        else:  # (name, confidence[, reasons])
            name, confidence, *rest = candidate
            rows.append(CandidateRow(
                name=str(name),
                confidence=float(confidence),
                reasons=tuple(rest[0]) if rest else (),
            ))
    return tuple(rows)


def legacy_decision(
    rule: Optional[str],
    *,
    speaker: Optional[str],
    is_suggestion: bool,
    suggestions: Sequence[str],
) -> Decision:
    """The legacy sieve's decision: the rule that fired, no trace yet."""
    return Decision(
        kind=KIND_INITIAL,
        stage="legacy_sieve",
        chosen=speaker,
        placement=PLACEMENT_KEEP,
        is_suggestion=is_suggestion,
        suggestions=tuple(suggestions),
        attribution_fields=(("legacyRule", rule),),
        with_trace=False,
    )


def initial_decision(
    stage: str,
    candidates: Iterable[object],
    *,
    is_suggestion: bool,
    reasons: Sequence[str] = (),
    fallback_speaker: Optional[str] = None,
    suggestions: Optional[Sequence[str]] = None,
    attribution_fields: Optional[Mapping[str, object]] = None,
    trace_fields: Optional[Mapping[str, object]] = None,
) -> Decision:
    """A ranked decision (rows top first) or, without rows, an unresolved one.

    With rows, the chosen speaker is the top row and the selected reasons are
    its reasons. Without rows there is no selected candidate; ``reasons``
    become the selected reasons and ``fallback_speaker`` stays the speaker.
    """
    rows = _rows(candidates)
    return Decision(
        kind=KIND_INITIAL,
        stage=stage,
        chosen=rows[0].name if rows else None,
        reasons=tuple(reasons),
        placement=PLACEMENT_RANKED,
        candidates=rows,
        is_suggestion=is_suggestion,
        suggestions=(
            tuple(suggestions)
            if suggestions is not None
            else tuple(row.name for row in rows)
        ),
        fallback_speaker=fallback_speaker,
        attribution_fields=_fields(attribution_fields),
        trace_fields=_fields(trace_fields),
    )


def override_decision(
    stage: str,
    chosen: str,
    reason: str,
    *,
    is_suggestion: Optional[bool] = None,
    trace_fields: Optional[Mapping[str, object]] = None,
) -> Decision:
    """A later stage promotes an existing candidate to the decision."""
    return Decision(
        kind=KIND_OVERRIDE,
        stage=stage,
        chosen=chosen,
        reasons=(reason,),
        placement=PLACEMENT_PROMOTE,
        is_suggestion=is_suggestion,
        promote=True,
        trace_fields=_fields(trace_fields),
    )


def synthetic_override_decision(
    stage: str,
    chosen: str,
    confidence: float,
    reason: str,
    *,
    is_suggestion: Optional[bool] = None,
    attribution_fields: Optional[Mapping[str, object]] = None,
    trace_fields: Optional[Mapping[str, object]] = None,
) -> Decision:
    """An external stage asserts its own candidate (prepended as a new row)."""
    return Decision(
        kind=KIND_OVERRIDE,
        stage=stage,
        chosen=chosen,
        reasons=(reason,),
        placement=PLACEMENT_PREPEND,
        candidates=(CandidateRow(chosen, confidence, (reason,)),),
        is_suggestion=is_suggestion,
        promote=True,
        attribution_fields=_fields(attribution_fields),
        trace_fields=_fields(trace_fields),
    )


def episode_override_decision(
    chosen: str,
    *,
    trace_fields: Optional[Mapping[str, object]] = None,
) -> Decision:
    """The episode joint decoder re-chooses an uncertain turn (stays a suggestion)."""
    return Decision(
        kind=KIND_EPISODE_OVERRIDE,
        stage="episode_decoder",
        chosen=chosen,
        reasons=(EPISODE_JOINT_DECODE,),
        placement=PLACEMENT_PROMOTE,
        is_suggestion=True,
        promote=True,
        trace_fields=_fields(trace_fields),
    )


def verdict_decision(
    stage: str,
    *,
    chosen: Optional[str] = None,
    confidence: float = 0.0,
    reasons: Sequence[str] = (),
    placement: str = PLACEMENT_KEEP,
    is_suggestion: Optional[bool] = None,
    promote: bool = False,
    attribution_fields: Optional[Mapping[str, object]] = None,
    trace_fields: Optional[Mapping[str, object]] = None,
) -> Decision:
    """An additive verdict: never rewrites the trace's decision fields.

    ``placement`` is PREPEND (the verdict's pick becomes the top row),
    ANNOTATE_TOP (``reasons`` are appended to the top row) or KEEP.
    """
    rows: Tuple[CandidateRow, ...] = ()
    if placement == PLACEMENT_PREPEND:
        if not chosen:
            raise ValueError("a prepend verdict needs a chosen candidate")
        rows = (CandidateRow(chosen, confidence, tuple(reasons)),)
    return Decision(
        kind=KIND_VERDICT,
        stage=stage,
        chosen=chosen,
        reasons=tuple(reasons),
        placement=placement,
        candidates=rows,
        is_suggestion=is_suggestion,
        promote=promote,
        attribution_fields=_fields(attribution_fields),
        trace_fields=_fields(trace_fields),
    )


def name_key(value: object) -> str:
    """Candidate-name matching key (case, quote and spacing insensitive)."""
    text = str(value or "").lower()
    text = (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    text = re.sub(r"(?<=\w)\s*'\s*(?=\w)", "'", text)
    text = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"^[\s\"'`“”‘’]+|[\s\"'`“”‘’]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def unique_names(*names: Optional[str]) -> List[str]:
    """Names in order, blanks dropped, deduplicated case-insensitively."""
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


def _apply_line_fields(line, decision: Decision) -> None:
    if decision.chosen is not None:
        line.speaker = decision.chosen
    elif decision.fallback_speaker is not None:
        line.speaker = decision.fallback_speaker
    if decision.is_suggestion is not None:
        line.is_suggestion = decision.is_suggestion
    if decision.suggestions is not None:
        line.suggestions = list(decision.suggestions)
    elif decision.promote:
        line.suggestions = unique_names(
            getattr(line, "speaker", None),
            *(getattr(line, "suggestions", None) or []),
        )


def _write_selection(attribution: dict, trace: dict, chosen: Optional[str], reasons: List[str]) -> None:
    """The owner's one write of the decision fields (keeps them consistent)."""
    attribution["genderConflict"] = "context_gender_conflict" in reasons
    trace["selectedCandidate"] = chosen
    trace["selectedReasons"] = reasons


def _apply_initial(line, decision: Decision) -> None:
    attribution: Dict[str, object] = dict(decision.attribution_fields)
    if decision.with_trace:
        rows = decision.candidates
        if rows:
            attribution["genderConflict"] = "context_gender_conflict" in rows[0].reasons
        attribution["candidates"] = [row.to_wire() for row in rows]
        trace: Dict[str, object] = {
            "selectedCandidate": decision.chosen,
            "selectedReasons": list(rows[0].reasons) if rows else list(decision.reasons),
        }
        trace.update(decision.trace_fields)
        attribution["decisionTrace"] = trace
    line.attribution = attribution


def _promote_row(rows: list, chosen: str) -> bool:
    chosen_key = name_key(chosen)
    index = next(
        (
            position
            for position, row in enumerate(rows)
            if isinstance(row, dict) and name_key(row.get("name")) == chosen_key
        ),
        None,
    )
    if index is None:
        return False
    if index > 0:
        rows.insert(0, rows.pop(index))
    return True


def _apply_override(line, decision: Decision) -> None:
    attribution = getattr(line, "attribution", None)
    if not isinstance(attribution, dict):
        attribution = {}
        line.attribution = attribution
    for key, value in decision.attribution_fields:
        attribution[key] = value

    if decision.placement == PLACEMENT_PREPEND:
        chosen_key = name_key(decision.chosen)
        attribution["candidates"] = [
            decision.candidates[0].to_wire(),
            *[
                row
                for row in attribution.get("candidates") or []
                if isinstance(row, dict) and name_key(row.get("name")) != chosen_key
            ],
        ]
    else:
        rows = attribution.get("candidates")
        if not isinstance(rows, list) or not _promote_row(rows, decision.chosen):
            # The chosen speaker has no candidate row: no decision to record.
            # Unreachable from the current writers (they only choose among
            # the line's candidates); the trace is left untouched.
            _apply_line_fields(line, decision)
            return

    top = attribution["candidates"][0]
    selected_reasons = list(top.get("reasons") or [])
    for reason in decision.reasons:
        if reason not in selected_reasons:
            selected_reasons.append(reason)
    top["reasons"] = selected_reasons

    trace = attribution.get("decisionTrace")
    if not isinstance(trace, dict):
        trace = {}
        attribution["decisionTrace"] = trace
    # The trace gets its own copy, so a later additive verdict annotating the
    # top row (JEV confirm) leaves the recorded decision intact.
    _write_selection(attribution, trace, decision.chosen, list(selected_reasons))
    trace["overrideReason"] = decision.reasons[0] if decision.reasons else None
    for key, value in decision.trace_fields:
        trace[key] = value
    _apply_line_fields(line, decision)


def _apply_verdict(line, decision: Decision) -> None:
    attribution = getattr(line, "attribution", None)
    attribution = attribution if isinstance(attribution, dict) else {}
    for key, value in decision.attribution_fields:
        attribution[key] = value
    if decision.trace_fields:
        trace = attribution.setdefault("decisionTrace", {})
        for key, value in decision.trace_fields:
            trace[key] = value

    existing = attribution.get("candidates")
    rows = [row for row in existing if isinstance(row, dict)] if isinstance(existing, list) else []
    if decision.placement == PLACEMENT_PREPEND:
        chosen_key = name_key(decision.chosen)
        attribution["candidates"] = [
            decision.candidates[0].to_wire(),
            *[row for row in rows if name_key(row.get("name")) != chosen_key],
        ]
    elif decision.placement == PLACEMENT_ANNOTATE_TOP and rows:
        reasons = rows[0].setdefault("reasons", [])
        for reason in decision.reasons:
            if reason not in reasons:
                reasons.append(reason)
    _apply_line_fields(line, decision)
    line.attribution = attribution


def apply_decision(line, decision: Decision) -> None:
    """Record ``decision`` on ``line``: the one writer of the attribution record."""
    if decision.kind == KIND_INITIAL:
        _apply_line_fields(line, decision)
        _apply_initial(line, decision)
    elif decision.kind in (KIND_OVERRIDE, KIND_EPISODE_OVERRIDE):
        _apply_override(line, decision)
    elif decision.kind == KIND_VERDICT:
        _apply_verdict(line, decision)
    else:
        raise ValueError(f"unknown decision kind: {decision.kind}")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def _attribution_of(line) -> Dict[str, object]:
    attribution = getattr(line, "attribution", None)
    return attribution if isinstance(attribution, dict) else {}


def _trace_of(line) -> Dict[str, object]:
    trace = _attribution_of(line).get("decisionTrace")
    return trace if isinstance(trace, dict) else {}


def selected_reasons(line) -> FrozenSet[str]:
    return frozenset(str(reason) for reason in _trace_of(line).get("selectedReasons") or [])


def has_hard_attribution(line) -> bool:
    """The line's decision rests on explicit HARD evidence (selected reason or legacy rule)."""
    legacy_rule = str(_attribution_of(line).get("legacyRule") or "")
    return bool(selected_reasons(line) & HARD_REASONS) or legacy_rule in HARD_REASONS


def has_explicit_local_attribution(line) -> bool:
    """The line's decision rests on a HARD or FIRST_PERSON selected reason."""
    return bool(selected_reasons(line) & EXPLICIT_LOCAL_RULES)


def trace_invariant_violations(line) -> List[str]:
    """Trace invariants the owner maintains; empty when ``line`` conforms.

    Lines without a decision trace (narration, legacy-only lines) and
    unresolved decisions (no selected candidate) have no selection to check.
    """
    attribution = _attribution_of(line)
    trace = _trace_of(line)
    if not trace or trace.get("selectedCandidate") is None:
        return []
    violations: List[str] = []
    rows = [row for row in attribution.get("candidates") or [] if isinstance(row, dict)]
    chosen = trace.get("selectedCandidate")
    reasons = list(trace.get("selectedReasons") or [])
    if not rows:
        violations.append("selected candidate without candidate rows")
        return violations
    if name_key(rows[0].get("name")) != name_key(chosen):
        violations.append("top candidate is not the selected candidate")
    if name_key(getattr(line, "speaker", None)) != name_key(chosen):
        violations.append("selectedCandidate != line.speaker")
    if list(rows[0].get("reasons") or []) != reasons:
        violations.append("selectedReasons != top candidate reasons")
    if bool(attribution.get("genderConflict")) != ("context_gender_conflict" in reasons):
        violations.append("genderConflict not recomputed from selectedReasons")
    override_reason = trace.get("overrideReason")
    if override_reason and override_reason not in reasons:
        violations.append("override reason not appended to selectedReasons")
    if not override_reason:
        confidences = [float(row.get("confidence") or 0.0) for row in rows]
        if confidences != sorted(confidences, reverse=True):
            violations.append("candidate order != ranking")
    return violations
