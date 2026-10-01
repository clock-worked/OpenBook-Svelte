"""Behavior tests for the JEV cross-verification layer (fake client, no network).

exp2: run queries. Gated targets are grouped into contiguous dialogue runs
and verified with one batched call per run.
"""

import unittest

from .dialogue_parser_service import DialogueLine
from .jev_verify_service import (
    CARRYOVER_RULES,
    JevVerifyCache,
    VerifyPolicy,
    apply_verdict,
    build_line_query,
    build_roster,
    build_run_query,
    cache_key,
    decide_verdict,
    group_runs,
    parse_answer,
    parse_run_answer,
    select_targets,
    verify_chapter,
)


CHAPTER_TEXT = (
    "Carmen watched the door. \"Stay sharp.\" She muttered.\n"
    "Dorian replied without turning. \"I never stopped.\"\n"
    "\"Then we move.\"\n"
    "\"Agreed.\"\n"
)


def make_line(
    speaker,
    candidates,
    *,
    rule=None,
    suggestion=False,
    reasons=None,
    span=(0, 4),
    text="sample",
    line_type="dialogue",
):
    value = DialogueLine(text, speaker, line_type=line_type)
    value.suggestions = [name for name, _score in candidates]
    value.is_suggestion = suggestion
    value.span_start, value.span_end = span
    value.attribution = {
        "legacyRule": rule,
        "candidates": [
            {"name": name, "confidence": score, "reasons": list(reasons or ["legacy_match"])}
            for name, score in candidates
        ],
        "decisionTrace": {"selectedReasons": list(reasons or ["legacy_match"])},
    }
    return value


def jev_response(choice, confidence):
    return {"answers": {"speaker": {"choice": choice, "confidence": confidence}}}


def jev_run_response(mapping):
    """mapping: question id -> (choice, confidence)."""
    return {
        "answers": {
            question_id: {"choice": choice, "confidence": confidence}
            for question_id, (choice, confidence) in mapping.items()
        }
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def ask(self, state, questions):
        self.calls += 1
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


NAME_LOOKUP = {"carmen": "Carmen", "dorian": "Dorian"}


class GateTests(unittest.TestCase):
    def test_carryover_gate_selects_only_turn_taking_inheritance(self):
        lines = [
            make_line("Carmen", [("Carmen", 0.9)], rule="2_forward_explicit_tags",
                      reasons=["post_quote_attribution"]),
            make_line("Carmen", [("Carmen", 0.8), ("Dorian", 0.5)], rule="4_contiguous_dialogue",
                      reasons=["parser_match", "same_paragraph_continuation"]),
            make_line("Dorian", [("Dorian", 0.8), ("Carmen", 0.5)], rule="4c_carry_across_short_narration",
                      reasons=["legacy_match"]),
            make_line("Dorian", [("Dorian", 0.9), ("Carmen", 0.4)], rule="4_contiguous_dialogue",
                      reasons=["parser_match", "post_quote_attribution"]),
        ]
        targets = select_targets(lines, "carryover")
        self.assertEqual([index for index, _ in targets], [1, 2])
        self.assertTrue(all("carryover_rule" in reasons for _, reasons in targets))

    def test_carryover_rule_with_local_evidence_is_excluded(self):
        lines = [
            make_line("Dorian", [("Dorian", 0.85), ("Carmen", 0.5)], rule="4_contiguous_dialogue",
                      reasons=["parser_match", "coref_support"]),
        ]
        self.assertEqual(select_targets(lines, "carryover"), [])

    def test_none_gate_selects_nothing(self):
        lines = [make_line("Carmen", [("Carmen", 0.8)], rule="4_contiguous_dialogue")]
        self.assertEqual(select_targets(lines, "none"), [])

    def test_full_gate_adds_risk_classes(self):
        lines = [
            make_line("Carmen", [("Carmen", 0.9), ("Dorian", 0.4)], rule="2_forward_explicit_tags",
                      reasons=["post_quote_attribution"]),
            make_line("Carmen", [("Carmen", 0.7), ("Dorian", 0.68)], rule="4_contiguous_dialogue"),
            make_line("Dorian", [("Dorian", 0.5), ("Carmen", 0.4)], suggestion=True,
                      rule="5_suggest_alternatives"),
        ]
        targets = {index: set(reasons) for index, reasons in select_targets(lines, "full")}
        self.assertIn("carryover_rule", targets.get(1, set()))
        self.assertIn("tight_margin", targets.get(1, set()))
        self.assertIn("already_suggestion", targets.get(2, set()))
        self.assertNotIn(0, targets)  # explicit tag line is clean


class RunGroupingTests(unittest.TestCase):
    def test_targets_group_into_maximal_contiguous_dialogue_run(self):
        lines = [
            make_line("Carmen", [("Carmen", 0.9)], rule="2_forward_explicit_tags"),
            make_line("Dorian", [("Dorian", 0.8)], rule="4_contiguous_dialogue"),
            make_line("Dorian", [("Dorian", 0.8)], rule="4_contiguous_dialogue"),
            make_line("narrator", [], line_type="narration"),
            make_line("Carmen", [("Carmen", 0.8)], rule="4_contiguous_dialogue"),
        ]
        runs = group_runs(lines, {1, 2})
        self.assertEqual(runs, [[0, 1, 2]])  # extends over the tagged anchor line

    def test_non_dialogue_breaks_the_run(self):
        lines = [
            make_line("Dorian", [("Dorian", 0.8)], rule="4_contiguous_dialogue"),
            make_line("narrator", [], line_type="narration"),
            make_line("Dorian", [("Dorian", 0.8)], rule="4_contiguous_dialogue"),
        ]
        runs = group_runs(lines, {0, 2})
        self.assertEqual(runs, [[0], [2]])

    def test_adjacent_targets_share_one_run(self):
        lines = [
            make_line("Dorian", [("Dorian", 0.8)], rule="4_contiguous_dialogue"),
            make_line("Dorian", [("Dorian", 0.8)], rule="4c_carry_across_short_narration"),
        ]
        self.assertEqual(group_runs(lines, {0, 1}), [[0, 1]])

    def test_run_without_target_is_dropped(self):
        lines = [
            make_line("Carmen", [("Carmen", 0.9)], rule="2_forward_explicit_tags"),
            make_line("Carmen", [("Carmen", 0.9)], rule="2b_backward_explicit_tags"),
        ]
        self.assertEqual(group_runs(lines, set()), [])


class VerdictTableTests(unittest.TestCase):
    def setUp(self):
        self.policy = VerifyPolicy()

    def _line(self):
        return make_line("Carmen", [("Carmen", 0.8), ("Dorian", 0.5)])

    def test_error_is_skip(self):
        self.assertEqual(decide_verdict(self._line(), None, 0.0, NAME_LOOKUP, self.policy), "skip")

    def test_low_confidence_is_note(self):
        self.assertEqual(decide_verdict(self._line(), "Dorian", 0.3, NAME_LOOKUP, self.policy), "note")

    def test_agree_high_confirms(self):
        self.assertEqual(decide_verdict(self._line(), "Carmen", 0.8, NAME_LOOKUP, self.policy), "confirm")

    def test_agree_medium_notes(self):
        self.assertEqual(decide_verdict(self._line(), "Carmen", 0.5, NAME_LOOKUP, self.policy), "note")

    def test_disagree_high_downgrades(self):
        self.assertEqual(decide_verdict(self._line(), "Dorian", 0.7, NAME_LOOKUP, self.policy), "downgrade")

    def test_disagree_below_veto_notes(self):
        self.assertEqual(decide_verdict(self._line(), "Dorian", 0.5, NAME_LOOKUP, self.policy), "note")

    def test_none_choice_downgrades(self):
        self.assertEqual(decide_verdict(self._line(), "None", 0.8, NAME_LOOKUP, self.policy), "downgrade")

    def test_alias_choice_matches_canonical(self):
        lookup = {"kate": "Kate", "katherine": "Kate", "dorian": "Dorian"}
        line = make_line("Kate", [("Kate", 0.8)])
        self.assertEqual(decide_verdict(line, "katherine", 0.8, lookup, self.policy), "confirm")


class ApplyVerdictTests(unittest.TestCase):
    def _block(self):
        return {"called": True, "cacheHit": False, "query": "run", "choice": "Dorian",
                "confidence": 0.9, "heuristicSpeaker": "Carmen", "ts": "now"}

    def test_downgrade_moves_jev_pick_first_and_marks_suggestion(self):
        line = make_line("Carmen", [("Carmen", 0.8), ("Dorian", 0.5)])
        apply_verdict(line, "downgrade", "Dorian", 0.9, NAME_LOOKUP, self._block())
        self.assertTrue(line.is_suggestion)
        self.assertEqual(line.speaker, "Dorian")
        self.assertEqual(line.suggestions[0], "Dorian")
        candidates = line.attribution["candidates"]
        self.assertEqual(candidates[0]["name"], "Dorian")
        self.assertIn("jev_verification_disagreement", candidates[0]["reasons"])
        self.assertEqual(len(candidates), 2)
        self.assertEqual(line.attribution["jevVerification"]["heuristicSpeaker"], "Carmen")
        # decisionTrace survives untouched
        self.assertEqual(line.attribution["decisionTrace"]["selectedReasons"], ["legacy_match"])

    def test_downgrade_on_none_keeps_heuristic_pick_as_guess(self):
        line = make_line("Carmen", [("Carmen", 0.8)])
        apply_verdict(line, "downgrade", "None", 0.9, NAME_LOOKUP, self._block())
        self.assertTrue(line.is_suggestion)
        self.assertEqual(line.speaker, "Carmen")

    def test_confirm_promotes_suggestion_to_assert(self):
        line = make_line("Carmen", [("Carmen", 0.6), ("Dorian", 0.55)], suggestion=True)
        block = {"called": True, "cacheHit": False, "query": "run", "choice": "Carmen",
                 "confidence": 0.9, "heuristicSpeaker": "Carmen", "ts": "now"}
        apply_verdict(line, "confirm", "Carmen", 0.9, NAME_LOOKUP, block)
        self.assertFalse(line.is_suggestion)
        self.assertIn("jev_verification_confirmed", line.attribution["candidates"][0]["reasons"])

    def test_confirm_does_not_touch_assert(self):
        line = make_line("Carmen", [("Carmen", 0.9)])
        apply_verdict(line, "confirm", "Carmen", 0.9, NAME_LOOKUP, self._block())
        self.assertFalse(line.is_suggestion)
        self.assertEqual(line.speaker, "Carmen")


class QueryBuildingTests(unittest.TestCase):
    def test_line_query_wraps_quote_and_preambles_last_speaker(self):
        line = make_line("Carmen", [("Carmen", 0.8)], span=(60, 70))
        roster = build_roster(line, NAME_LOOKUP, "Dorian", None, 6)
        state, questions = build_line_query(CHAPTER_TEXT, line, roster, "Dorian", 1500)
        self.assertIn("<quote>", state)
        self.assertTrue(state.startswith("[Last speaker: Dorian]"))
        self.assertIn("None", questions["speaker"]["criteria"])
        self.assertEqual(questions["speaker"]["type"], "choice")

    def test_roster_capped_and_deduped(self):
        line = make_line(
            "Carmen",
            [("Carmen", 0.8), ("Dorian", 0.6), ("Viper", 0.5), ("Ash", 0.4)],
        )
        lookup = {name.lower(): name for name in ("Carmen", "Dorian", "Viper", "Ash")}
        roster = build_roster(line, lookup, "Carmen", "Dorian", 3)
        self.assertEqual(len(roster), 3)
        self.assertNotIn("None", roster)

    def test_run_query_marks_each_quote_and_asks_one_question_per_line(self):
        run_lines = [
            make_line("Carmen", [("Carmen", 0.8)], span=(26, 37), text="Stay sharp."),
            make_line("Dorian", [("Dorian", 0.8)], span=(86, 102), text="I never stopped."),
            make_line("Dorian", [("Dorian", 0.78)], span=(105, 118), text="Then we move."),
        ]
        state, questions = build_run_query(CHAPTER_TEXT, run_lines, {"Carmen": "Carmen", "Dorian": "Dorian"}, 1500)
        self.assertIn("<q0>Stay sharp.</q0>", state)
        self.assertIn("<q1>I never stopped.</q1>", state)
        self.assertIn("<q2>Then we move.</q2>", state)
        self.assertEqual(set(questions), {"q0", "q1", "q2"})
        for question in questions.values():
            self.assertEqual(question["type"], "choice")
            self.assertIn("None", question["criteria"])
            self.assertIn("Carmen", question["criteria"])

    def test_run_query_rejects_empty_run(self):
        with self.assertRaises(ValueError):
            build_run_query(CHAPTER_TEXT, [], {"Carmen": "Carmen"}, 1500)

    def test_run_query_window_covers_non_monotonic_spans(self):
        # Regression: line spans are not guaranteed monotonic in index
        # order. The window must be bounded by the full run box, else a
        # later-in-text span falls outside the state and query building
        # crashes (which used to kill the whole book run).
        text = "x" * 1300
        run_lines = [
            make_line("Carmen", [("Carmen", 0.8)], span=(100, 1200), text="long"),
            make_line("Dorian", [("Dorian", 0.8)], span=(200, 203), text="mid"),
            make_line("Dorian", [("Dorian", 0.78)], span=(400, 403), text="end"),
        ]
        state, questions = build_run_query(
            text, run_lines, {"Carmen": "Carmen", "Dorian": "Dorian"}, 100
        )
        self.assertEqual(set(questions), {"q0", "q1", "q2"})
        self.assertIn("<q0>", state)
        self.assertIn("</q0>", state)
        self.assertIn("<q1>xxx</q1>", state)
        self.assertIn("<q2>xxx</q2>", state)


class EndToEndTests(unittest.TestCase):
    def _chapter_lines(self):
        return [
            make_line("Carmen", [("Carmen", 0.9), ("Dorian", 0.4)], rule="2_forward_explicit_tags",
                      reasons=["post_quote_attribution"], span=(26, 37), text="Stay sharp."),
            make_line("Dorian", [("Dorian", 0.8), ("Carmen", 0.55)], rule="4_contiguous_dialogue",
                      reasons=["parser_match", "same_paragraph_continuation"], span=(86, 102),
                      text="I never stopped."),
            make_line("Dorian", [("Dorian", 0.78), ("Carmen", 0.6)], rule="4_contiguous_dialogue",
                      reasons=["parser_match", "same_paragraph_continuation"], span=(105, 118),
                      text="Then we move."),
        ]

    def test_disagreement_downgrades_only_gated_lines_in_one_run_call(self):
        lines = self._chapter_lines()
        client = FakeClient(
            [jev_run_response({"q0": ("Carmen", 0.9), "q1": ("Carmen", 0.9), "q2": ("Carmen", 0.85)})]
        )
        cache = JevVerifyCache(enabled=False)
        summary = verify_chapter(
            lines, CHAPTER_TEXT, NAME_LOOKUP,
            policy=VerifyPolicy(max_calls=10), client=client, cache=cache, log=lambda _m: None,
        )
        self.assertEqual(summary["targets"], 2)
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(client.calls, 1)
        self.assertEqual(summary["modelCalls"], 1)
        self.assertEqual(summary["actions"], {"downgrade": 2})
        # gated lines flipped to unknown-with-guess
        self.assertTrue(lines[1].is_suggestion)
        self.assertEqual(lines[1].speaker, "Carmen")
        self.assertTrue(lines[2].is_suggestion)
        # explicit-tag anchor line untouched
        self.assertFalse(lines[0].is_suggestion)
        self.assertNotIn("jevVerification", lines[0].attribution)

    def test_run_query_answers_recorded_with_query_kind(self):
        lines = self._chapter_lines()
        client = FakeClient([jev_run_response({"q0": ("Carmen", 0.9), "q1": ("Dorian", 0.9), "q2": ("Dorian", 0.9)})])
        summary = verify_chapter(
            lines, CHAPTER_TEXT, NAME_LOOKUP,
            policy=VerifyPolicy(max_calls=10), client=client, cache=JevVerifyCache(enabled=False),
            log=lambda _m: None,
        )
        self.assertEqual(summary["actions"], {"confirm": 2})
        self.assertEqual(lines[1].attribution["jevVerification"]["query"], "run")
        self.assertEqual(lines[2].attribution["jevVerification"]["choice"], "Dorian")

    def test_error_fails_open_per_run(self):
        lines = self._chapter_lines()
        client = FakeClient([ConnectionError("gateway down")])
        summary = verify_chapter(
            lines, CHAPTER_TEXT, NAME_LOOKUP,
            policy=VerifyPolicy(max_calls=10, retries=0),
            client=client, cache=JevVerifyCache(enabled=False), log=lambda _m: None,
        )
        self.assertEqual(summary["errors"], 1)  # one run = one failed call
        self.assertEqual(summary["actions"], {"skip": 2})  # both gated lines kept
        self.assertFalse(lines[1].is_suggestion)
        self.assertEqual(lines[1].speaker, "Dorian")
        self.assertIn("jevVerification", lines[1].attribution)
        self.assertIn("error", lines[1].attribution["jevVerification"])

    def test_cache_avoids_second_call(self):
        import tempfile
        from pathlib import Path

        lines = self._chapter_lines()
        client = FakeClient([jev_run_response({"q0": ("Carmen", 0.9), "q1": ("Carmen", 0.9), "q2": ("Carmen", 0.9)})])
        with tempfile.TemporaryDirectory() as tmp:
            cache = JevVerifyCache(cache_dir=Path(tmp))
            summary = verify_chapter(
                lines, CHAPTER_TEXT, NAME_LOOKUP,
                policy=VerifyPolicy(max_calls=10), client=client, cache=cache, log=lambda _m: None,
            )
            self.assertEqual(client.calls, 1)
            self.assertEqual(summary["modelCalls"], 1)

            fresh_lines = self._chapter_lines()
            summary2 = verify_chapter(
                fresh_lines, CHAPTER_TEXT, NAME_LOOKUP,
                policy=VerifyPolicy(max_calls=10), client=client, cache=cache, log=lambda _m: None,
            )
            self.assertEqual(client.calls, 1)  # no new network calls
            self.assertEqual(summary2["cacheHits"], 1)
            self.assertEqual(summary2["modelCalls"], 0)

    def test_max_calls_caps_runs_not_lines(self):
        lines = self._chapter_lines() + [
            make_line("narrator", [], line_type="narration", span=(95, 96)),
            make_line("Dorian", [("Dorian", 0.8)], rule="4_contiguous_dialogue",
                      reasons=["same_paragraph_continuation"], span=(100, 110), text="Again."),
        ]
        client = FakeClient([jev_run_response({})])
        summary = verify_chapter(
            lines, CHAPTER_TEXT, NAME_LOOKUP,
            policy=VerifyPolicy(max_calls=1), client=client, cache=JevVerifyCache(enabled=False),
            log=lambda _m: None,
        )
        # both targets sit in the first run; the second run is capped out
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(client.calls, 1)

    def test_parse_answers_tolerate_missing_fields(self):
        self.assertEqual(parse_answer(None), (None, 0.0))
        self.assertEqual(parse_answer({"answers": {}}), (None, 0.0))
        self.assertEqual(parse_answer(jev_response("Carmen", "0.9")), ("Carmen", 0.9))
        self.assertEqual(parse_run_answer(jev_run_response({"q1": ("Dorian", 0.7)}), "q1"), ("Dorian", 0.7))
        self.assertEqual(parse_run_answer(jev_run_response({"q0": ("Carmen", 0.9)}), "q2"), (None, 0.0))

    def test_cache_key_is_stable_and_sensitive(self):
        key_a = cache_key("state", {"speaker": {"criteria": {"A": "A"}}})
        key_b = cache_key("state", {"speaker": {"criteria": {"A": "A"}}})
        key_c = cache_key("state ", {"speaker": {"criteria": {"A": "A"}}})
        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)


if __name__ == "__main__":
    unittest.main()
