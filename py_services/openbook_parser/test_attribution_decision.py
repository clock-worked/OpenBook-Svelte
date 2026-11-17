"""Tests for the attribution decision owner (attribution_decision.py, ADR-0013).

Pure logic: no fastapi / spaCy needed. Lines are duck-typed stand-ins for
``DialogueLine`` (the owner only reads/writes speaker, is_suggestion,
suggestions and attribution).
"""

import ast
import copy
import itertools
import unittest
from pathlib import Path
from types import SimpleNamespace

from .attribution_decision import (
    CARRYOVER,
    CARRYOVER_RULES,
    CLOSED_WORLD_UNRESOLVED,
    EDGE_FRONTEND,
    EDGE_PARSER,
    EPISODE_JOINT_DECODE,
    EXPLICIT_LOCAL_RULES,
    FIRST_PERSON,
    FIRST_PERSON_RULES,
    GATE_JEV_CARRYOVER_TARGET,
    GATE_JEV_LOCAL_SHIELD,
    GATE_RANK_SUPPORT,
    HARD,
    HARD_REASONS,
    LOCAL_EVIDENCE,
    LOCAL_EVIDENCE_REASONS,
    OVERRIDE_REASONS,
    PLACEMENT_ANNOTATE_TOP,
    PLACEMENT_PREPEND,
    RANK_SUPPORT_REASONS,
    RULE_CATEGORIES,
    RULE_PREFIXES,
    RULE_REGISTRY,
    PolicyFacts,
    apply_decision,
    decision_rule_category,
    episode_override_decision,
    evaluate_policy,
    has_explicit_local_attribution,
    has_hard_attribution,
    initial_decision,
    legacy_decision,
    override_decision,
    rule_category,
    rule_spec,
    synthetic_override_decision,
    trace_invariant_violations,
    verdict_decision,
)

PARSER_DIR = Path(__file__).resolve().parent
PY_SERVICES_DIR = PARSER_DIR.parent


def make_line(speaker=None, *, suggestions=None, is_suggestion=False, attribution=None):
    return SimpleNamespace(
        speaker=speaker,
        is_suggestion=is_suggestion,
        suggestions=list(suggestions or []),
        attribution=attribution,
    )


# ---------------------------------------------------------------------------
# Emitted-name scan (registry exhaustiveness)
# ---------------------------------------------------------------------------


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _str_constants(node) -> list:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [
            item.value
            for item in node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
    return []


def scoring_reason_names() -> set:
    """``reasons.append("...")`` literals in attribution_passes.py."""
    names = set()
    for node in ast.walk(_tree(PARSER_DIR / "attribution_passes.py")):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "reasons"
            and node.args
        ):
            names.update(_str_constants(node.args[0]))
    return names


def legacy_sieve_names() -> set:
    """``self._rule("...")`` and ``self._last_rule_hit = "..."`` literals."""
    names = set()
    for node in ast.walk(_tree(PARSER_DIR / "dialogue_parser_service.py")):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_rule"
            and node.args
        ):
            names.update(_str_constants(node.args[0]))
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "_last_rule_hit":
                    names.update(_str_constants(node.value))
    return names


def dialogue_ai_reason_names() -> tuple:
    """Reason-code literals (and f-string prefixes) in dialogue_ai_service.py."""
    names, prefixes = set(), set()
    code_lists = {"reason_codes", "normalized_reason_codes"}
    for node in ast.walk(_tree(PY_SERVICES_DIR / "dialogue_ai_service.py")):
        if isinstance(node, ast.keyword) and node.arg in {"reason_codes", "reasonCodes"}:
            names.update(_str_constants(node.value))
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in code_lists:
                    names.update(_str_constants(node.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and node.args
        ):
            owner = node.func.value
            is_code_list = (
                (isinstance(owner, ast.Name) and owner.id in code_lists)
                or (
                    isinstance(owner, ast.Subscript)
                    and isinstance(owner.slice, ast.Constant)
                    and owner.slice.value == "reasonCodes"
                )
            )
            if not is_code_list:
                continue
            argument = node.args[0]
            names.update(_str_constants(argument))
            if (
                isinstance(argument, ast.JoinedStr)
                and argument.values
                and isinstance(argument.values[0], ast.Constant)
            ):
                prefixes.add(argument.values[0].value)
    return names, prefixes


class RuleRegistryTests(unittest.TestCase):
    def test_every_rule_has_exactly_one_of_the_four_categories(self):
        self.assertEqual(RULE_CATEGORIES, (HARD, CARRYOVER, LOCAL_EVIDENCE, FIRST_PERSON))
        for spec in list(RULE_REGISTRY.values()) + list(RULE_PREFIXES):
            with self.subTest(rule=spec.name):
                self.assertIn(spec.category, RULE_CATEGORIES)

    def test_every_emitted_rule_name_is_registered(self):
        ai_names, ai_prefixes = dialogue_ai_reason_names()
        emitted = (
            scoring_reason_names()
            | legacy_sieve_names()
            | ai_names
            | set(OVERRIDE_REASONS)
        )
        # The scan must actually see each source.
        self.assertIn("post_quote_attribution", emitted)
        self.assertIn("4_contiguous_dialogue", emitted)
        self.assertIn("first_person_override", emitted)
        self.assertIn("explicit_cue_resolution", emitted)
        for name in sorted(emitted):
            with self.subTest(rule=name):
                self.assertIsNotNone(rule_spec(name), f"unregistered rule name: {name}")
        registered_prefixes = {prefix.name for prefix in RULE_PREFIXES}
        self.assertEqual(ai_prefixes, registered_prefixes)
        self.assertEqual(rule_category("cue_verb_said"), LOCAL_EVIDENCE)

    def test_no_stale_registry_entries(self):
        ai_names, _ = dialogue_ai_reason_names()
        emitted = (
            scoring_reason_names()
            | legacy_sieve_names()
            | ai_names
            | set(OVERRIDE_REASONS)
        )
        self.assertEqual(set(RULE_REGISTRY) - emitted, set())

    def test_unregistered_name_has_no_category(self):
        self.assertIsNone(rule_category("model_free_form_reason"))
        self.assertIsNone(rule_category("cue_verb_"))
        self.assertIsNone(rule_category(None))

    def test_derived_consumer_sets_match_the_sets_they_replace(self):
        self.assertEqual(HARD_REASONS, {
            "post_quote_attribution",
            "post_quote_mention",
            "2_forward_explicit_tags",
            "2b_backward_explicit_tags",
        })
        self.assertEqual(FIRST_PERSON_RULES, {"0p_protagonist_first_person", "first_person_override"})
        self.assertEqual(EXPLICIT_LOCAL_RULES, HARD_REASONS | FIRST_PERSON_RULES)
        self.assertEqual(CARRYOVER_RULES, {
            "4b_tag_continuation",
            "4_contiguous_dialogue",
            "4c_carry_across_short_narration",
        })
        self.assertEqual(LOCAL_EVIDENCE_REASONS, HARD_REASONS | {
            "coref_support",
            "alias_strength",
            "learned_descriptor_match",
            "context_gender_match",
        })
        self.assertEqual(RANK_SUPPORT_REASONS, {
            "parser_match",
            "coref_support",
            "legacy_match",
            "post_quote_attribution",
            "recent_named_mention",
            "previous_paragraph_named_mention",
            "same_paragraph_continuation",
            "speech_verb",
            "alias_strength",
            "learned_descriptor_match",
            "context_gender_match",
        })

    def test_gates_are_consistent_with_categories(self):
        for spec in RULE_REGISTRY.values():
            with self.subTest(rule=spec.name):
                if GATE_JEV_CARRYOVER_TARGET in spec.gates:
                    self.assertEqual(spec.category, CARRYOVER)
                if GATE_JEV_LOCAL_SHIELD in spec.gates:
                    self.assertEqual(spec.category, LOCAL_EVIDENCE)
                if GATE_RANK_SUPPORT in spec.gates:
                    self.assertFalse(spec.name.endswith(("_penalty", "_conflict")))

    def test_decision_rule_category_takes_the_strongest_present(self):
        self.assertEqual(decision_rule_category(["legacy_match", "first_person_override"]), FIRST_PERSON)
        self.assertEqual(decision_rule_category(["recent_named_mention", "post_quote_mention"]), HARD)
        self.assertEqual(decision_rule_category(["prev_continuity", "speech_verb"]), LOCAL_EVIDENCE)
        self.assertEqual(decision_rule_category(["prev_continuity"]), CARRYOVER)
        self.assertIsNone(decision_rule_category(["unregistered"]))
        self.assertIsNone(decision_rule_category([]))


# ---------------------------------------------------------------------------
# Policy spec
# ---------------------------------------------------------------------------


def legacy_parser_is_suggestion(top, second, fallback, high_conflict, gender, generic, support):
    """The rank_candidates expression the parser edge replaces."""
    margin = top - second
    return bool(
        fallback
        or top < 0.74
        or margin < 0.14
        or high_conflict
        or gender
        or generic
        or (not support and top < 0.8)
    )


def legacy_frontend_should_abstain(top, count, parser_suggestion, named, blocked, first_person):
    """The parser.ts shouldAbstain expression the frontend edge replaces."""
    return (not first_person) and (
        parser_suggestion
        or top < 0.74
        or (count > 1 and top < 0.86)
        or not named
        or blocked
    )


class PolicySpecTests(unittest.TestCase):
    SCORES = (0.0, 0.5, 0.6, 0.7399, 0.74, 0.79, 0.8, 0.85, 0.86, 0.9, 0.98)

    def test_parser_edge_matches_the_legacy_expression(self):
        flags = list(itertools.product((False, True), repeat=5))
        for top, second in itertools.product(self.SCORES, self.SCORES):
            if second > top:
                continue
            for fallback, high_conflict, gender, generic, support in flags:
                facts = PolicyFacts(
                    top=top,
                    second=second,
                    candidate_count=2,
                    used_fallback_alignment=fallback,
                    question_next_speaker_conflict=high_conflict,
                    gender_conflict=gender,
                    generic_top_choice=generic,
                    has_support_reason=support,
                )
                self.assertEqual(
                    evaluate_policy(EDGE_PARSER, facts).is_suggestion,
                    legacy_parser_is_suggestion(top, second, fallback, high_conflict, gender, generic, support),
                    facts,
                )

    def test_frontend_edge_matches_the_legacy_expression(self):
        for top, count in itertools.product(self.SCORES, (0, 1, 2, 3)):
            for parser_suggestion, named, blocked, first_person in itertools.product((False, True), repeat=4):
                facts = PolicyFacts(
                    top=top,
                    candidate_count=count,
                    parser_suggestion=parser_suggestion,
                    has_named_candidates=named,
                    speaker_blocked=blocked,
                    first_person=first_person,
                )
                self.assertEqual(
                    evaluate_policy(EDGE_FRONTEND, facts).is_suggestion,
                    legacy_frontend_should_abstain(top, count, parser_suggestion, named, blocked, first_person),
                    facts,
                )

    def test_outcome_names_the_fired_rules_and_exemption(self):
        outcome = evaluate_policy(
            EDGE_FRONTEND,
            PolicyFacts(top=0.5, candidate_count=2, first_person=True),
        )
        self.assertFalse(outcome.is_suggestion)
        self.assertEqual(outcome.fired_rules, ("low_top", "contested_top"))
        self.assertEqual(outcome.exempted_by, "first_person")

    def test_edges_are_scoped(self):
        # tight_margin is parser-only; contested_top is frontend-only.
        facts = PolicyFacts.from_confidences([0.9, 0.85], has_support_reason=True)
        self.assertEqual(evaluate_policy(EDGE_PARSER, facts).fired_rules, ("tight_margin",))
        self.assertEqual(evaluate_policy(EDGE_FRONTEND, facts).fired_rules, ())
        with self.assertRaises(ValueError):
            evaluate_policy("elsewhere", facts)


# ---------------------------------------------------------------------------
# Trace contract
# ---------------------------------------------------------------------------


def ranked_line(candidates, *, is_suggestion=False, legacy_rule=None):
    line = make_line()
    apply_decision(line, initial_decision(
        "parser_rank",
        candidates,
        is_suggestion=is_suggestion,
        attribution_fields={"parserBackend": "legacy", "legacyRule": legacy_rule},
        trace_fields={"rawCandidatePool": [row[0] for row in candidates]},
    ))
    return line


class ApplyDecisionTests(unittest.TestCase):
    def test_initial_decision_builds_a_conforming_record(self):
        line = ranked_line([
            ("Jake", 0.9, ["post_quote_attribution", "context_gender_conflict"]),
            ("Viper", 0.4, ["legacy_match"]),
        ])
        self.assertEqual(line.speaker, "Jake")
        self.assertEqual(line.suggestions, ["Jake", "Viper"])
        self.assertFalse(line.is_suggestion)
        attribution = line.attribution
        self.assertEqual(
            list(attribution),
            ["parserBackend", "legacyRule", "genderConflict", "candidates", "decisionTrace"],
        )
        self.assertTrue(attribution["genderConflict"])
        trace = attribution["decisionTrace"]
        self.assertEqual(trace["selectedCandidate"], "Jake")
        self.assertEqual(trace["selectedReasons"], ["post_quote_attribution", "context_gender_conflict"])
        self.assertIsNot(trace["selectedReasons"], attribution["candidates"][0]["reasons"])
        self.assertEqual(trace["rawCandidatePool"], ["Jake", "Viper"])
        self.assertEqual(trace_invariant_violations(line), [])
        self.assertTrue(has_hard_attribution(line))
        self.assertTrue(has_explicit_local_attribution(line))

    def test_unresolved_initial_decision_has_no_selection(self):
        line = make_line("Somebody", suggestions=["Somebody", "Else"])
        apply_decision(line, initial_decision(
            "parser_rank",
            [],
            is_suggestion=True,
            reasons=[CLOSED_WORLD_UNRESOLVED],
            fallback_speaker="Somebody",
            suggestions=["Somebody"],
            attribution_fields={"parserBackend": "legacy"},
        ))
        self.assertEqual(line.speaker, "Somebody")
        self.assertEqual(line.suggestions, ["Somebody"])
        self.assertTrue(line.is_suggestion)
        self.assertEqual(list(line.attribution), ["parserBackend", "candidates", "decisionTrace"])
        self.assertIsNone(line.attribution["decisionTrace"]["selectedCandidate"])
        self.assertEqual(line.attribution["decisionTrace"]["selectedReasons"], [CLOSED_WORLD_UNRESOLVED])
        self.assertEqual(trace_invariant_violations(line), [])

    def test_legacy_decision_records_only_the_rule(self):
        line = make_line()
        apply_decision(line, legacy_decision(
            "4_contiguous_dialogue", speaker="Jake", is_suggestion=False, suggestions=[],
        ))
        self.assertEqual(line.attribution, {"legacyRule": "4_contiguous_dialogue"})
        self.assertEqual(line.speaker, "Jake")
        self.assertEqual(trace_invariant_violations(line), [])

    def test_override_promotes_and_appends_reason(self):
        line = ranked_line(
            [("Jake", 0.6, ["legacy_match"]), ("Viper", 0.5, ["context_gender_conflict"])],
            is_suggestion=True,
        )
        apply_decision(line, override_decision("paragraph_consistency", "Viper", "same_paragraph_context_consistency"))
        self.assertEqual(line.speaker, "Viper")
        self.assertTrue(line.is_suggestion)  # left as it was
        self.assertEqual(line.suggestions, ["Viper", "Jake"])
        self.assertEqual([row["name"] for row in line.attribution["candidates"]], ["Viper", "Jake"])
        trace = line.attribution["decisionTrace"]
        self.assertEqual(trace["selectedCandidate"], "Viper")
        self.assertEqual(trace["selectedReasons"], ["context_gender_conflict", "same_paragraph_context_consistency"])
        self.assertEqual(trace["overrideReason"], "same_paragraph_context_consistency")
        self.assertTrue(line.attribution["genderConflict"])
        self.assertEqual(trace_invariant_violations(line), [])

    def test_override_without_a_matching_row_leaves_the_trace(self):
        line = ranked_line([("Jake", 0.9, ["legacy_match"])])
        before = copy.deepcopy(line.attribution)
        apply_decision(line, override_decision("first_person", "Me", "first_person_override", is_suggestion=False))
        self.assertEqual(line.speaker, "Me")
        self.assertEqual(line.attribution, before)

    def test_synthetic_override_prepends_its_own_row(self):
        line = ranked_line([("Jake", 0.7, ["legacy_match"]), ("Viper", 0.5, [])], is_suggestion=True)
        apply_decision(line, synthetic_override_decision(
            "modernbooknlp", "Viper", 0.9, "modernbooknlp_joint_attribution",
            is_suggestion=False,
            attribution_fields={"parserBackend": "modernbooknlp"},
            trace_fields={"modernBookNLPDecision": "accepted"},
        ))
        rows = line.attribution["candidates"]
        self.assertEqual(rows[0], {"name": "Viper", "confidence": 0.9, "reasons": ["modernbooknlp_joint_attribution"]})
        self.assertEqual([row["name"] for row in rows], ["Viper", "Jake"])
        self.assertEqual(line.attribution["parserBackend"], "modernbooknlp")
        trace = line.attribution["decisionTrace"]
        self.assertEqual(trace["selectedReasons"], ["modernbooknlp_joint_attribution"])
        self.assertEqual(trace["modernBookNLPDecision"], "accepted")
        self.assertFalse(line.is_suggestion)
        self.assertEqual(trace_invariant_violations(line), [])

    def test_episode_override_stays_a_suggestion_with_provenance(self):
        line = ranked_line([("Jake", 0.55, ["legacy_match"]), ("Viper", 0.52, ["legacy_match"])], is_suggestion=True)
        apply_decision(line, episode_override_decision("Viper", trace_fields={"episodeDecoder": {"x": 1}}))
        trace = line.attribution["decisionTrace"]
        self.assertEqual(line.speaker, "Viper")
        self.assertTrue(line.is_suggestion)
        self.assertEqual(trace["overrideReason"], EPISODE_JOINT_DECODE)
        self.assertEqual(trace["selectedReasons"], ["legacy_match", EPISODE_JOINT_DECODE])
        self.assertEqual(trace["episodeDecoder"], {"x": 1})
        self.assertEqual(trace_invariant_violations(line), [])

    def test_verdict_never_rewrites_the_trace(self):
        line = ranked_line([("Jake", 0.8, ["legacy_match"]), ("Viper", 0.5, [])])
        trace_before = copy.deepcopy(line.attribution["decisionTrace"])
        apply_decision(line, verdict_decision(
            "jev_verify",
            chosen="Viper",
            confidence=0.81234,
            reasons=["jev_verification_disagreement"],
            placement=PLACEMENT_PREPEND,
            is_suggestion=True,
            promote=True,
            attribution_fields={"jevVerification": {"called": True}},
        ))
        self.assertEqual(line.attribution["decisionTrace"], trace_before)
        self.assertEqual(line.speaker, "Viper")
        self.assertTrue(line.is_suggestion)
        self.assertEqual(line.suggestions, ["Viper", "Jake"])
        self.assertEqual(line.attribution["candidates"][0]["confidence"], 0.8123)
        self.assertEqual(line.attribution["jevVerification"], {"called": True})

    def test_annotating_verdict_touches_only_the_top_row(self):
        line = ranked_line(
            [("Jake", 0.6, ["legacy_match"]), ("Viper", 0.5, [])], is_suggestion=True,
        )
        apply_decision(line, override_decision("paragraph_consistency", "Jake", "same_paragraph_context_consistency"))
        trace_before = copy.deepcopy(line.attribution["decisionTrace"])
        apply_decision(line, verdict_decision(
            "jev_verify",
            reasons=["jev_verification_confirmed"],
            placement=PLACEMENT_ANNOTATE_TOP,
            is_suggestion=False,
        ))
        self.assertIn("jev_verification_confirmed", line.attribution["candidates"][0]["reasons"])
        self.assertEqual(line.attribution["decisionTrace"], trace_before)
        self.assertFalse(line.is_suggestion)

    def test_invariant_checker_reports_hand_built_drift(self):
        line = make_line("Viper", attribution={
            "genderConflict": False,
            "candidates": [
                {"name": "Jake", "confidence": 0.5, "reasons": ["legacy_match"]},
                {"name": "Viper", "confidence": 0.9, "reasons": []},
            ],
            "decisionTrace": {"selectedCandidate": "Viper", "selectedReasons": ["context_gender_conflict"]},
        })
        self.assertEqual(set(trace_invariant_violations(line)), {
            "top candidate is not the selected candidate",
            "selectedReasons != top candidate reasons",
            "genderConflict not recomputed from selectedReasons",
            "candidate order != ranking",
        })


if __name__ == "__main__":
    unittest.main()
