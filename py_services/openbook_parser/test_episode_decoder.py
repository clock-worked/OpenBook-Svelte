"""Behavior tests for explainable episode-level speaker decoding."""

import unittest

from .attribution_decision import apply_decision, initial_decision, trace_invariant_violations
from .dialogue_parser_service import DialogueLine
from .episode_decoder import decode_dialogue_episodes


def line(candidates, paragraph, *, hard=False, suggestion=False):
    """A ranked parser line, built through the decision owner."""
    value = DialogueLine("sample", None, line_type="dialogue")
    value.paragraph_index = paragraph
    reasons = ["post_quote_attribution"] if hard else ["legacy_match"]
    apply_decision(value, initial_decision(
        "fixture",
        [(name, score, reasons) for name, score in candidates],
        is_suggestion=suggestion,
    ))
    return value


def assert_conforms(test, lines):
    for value in lines:
        test.assertEqual(trace_invariant_violations(value), [])


class EpisodeDecoderTests(unittest.TestCase):
    def test_uses_two_person_turn_structure_to_resolve_middle_turn(self):
        lines = [
            line([("Jake", 0.98)], 1, hard=True),
            line([("Jake", 0.55), ("Viper", 0.52)], 2, suggestion=True),
            line([("Jake", 0.98)], 3, hard=True),
        ]

        changes = decode_dialogue_episodes(lines)

        self.assertEqual(lines[1].speaker, "Viper")
        self.assertEqual(changes, 1)
        assert_conforms(self, lines)
        self.assertEqual(
            lines[1].attribution["decisionTrace"]["overrideReason"],
            "episode_joint_decode",
        )

    def test_does_not_override_hard_explicit_attribution(self):
        lines = [
            line([("Jake", 0.98)], 1, hard=True),
            line([("Jake", 0.98), ("Viper", 0.40)], 2, hard=True),
            line([("Jake", 0.98)], 3, hard=True),
        ]

        changes = decode_dialogue_episodes(lines)

        self.assertEqual(lines[1].speaker, "Jake")
        self.assertEqual(changes, 0)

    def test_does_not_override_confident_non_suggestion(self):
        lines = [
            line([("Jake", 0.98)], 1, hard=True),
            line([("Jake", 0.98), ("Viper", 0.40)], 2),
            line([("Jake", 0.98)], 3, hard=True),
        ]

        changes = decode_dialogue_episodes(lines)

        self.assertEqual(lines[1].speaker, "Jake")
        self.assertEqual(changes, 0)

    def test_override_keeps_candidates_consistent_and_reviewable(self):
        lines = [
            line([("Jake", 0.98)], 1, hard=True),
            line([("Jake", 0.55), ("Viper", 0.52)], 2, suggestion=True),
            line([("Jake", 0.98)], 3, hard=True),
        ]

        decode_dialogue_episodes(lines)

        changed = lines[1]
        self.assertEqual(changed.speaker, "Viper")
        self.assertTrue(changed.is_suggestion)
        self.assertEqual(changed.suggestions[0], "Viper")
        self.assertEqual(changed.attribution["candidates"][0]["name"], "Viper")
        self.assertIn(
            "episode_joint_decode",
            changed.attribution["decisionTrace"]["selectedReasons"],
        )
        self.assertEqual(changed.attribution["decisionTrace"]["selectedCandidate"], "Viper")
        self.assertEqual(
            changed.attribution["decisionTrace"]["episodeDecoder"]["originalCandidate"],
            "Jake",
        )
        assert_conforms(self, lines)

    def test_does_not_force_alternation_in_multi_party_episode(self):
        lines = [
            line([("Jake", 0.98)], 1, hard=True),
            line([("Jake", 0.55), ("Viper", 0.52), ("Carmen", 0.50)], 2, suggestion=True),
            line([("Jake", 0.98)], 3, hard=True),
        ]

        changes = decode_dialogue_episodes(lines)

        self.assertEqual(lines[1].speaker, "Jake")
        self.assertEqual(changes, 0)

    def test_scene_gap_splits_episode(self):
        lines = [
            line([("Jake", 0.98)], 1, hard=True),
            line([("Jake", 0.55), ("Viper", 0.52)], 2, suggestion=True),
            line([("Jake", 0.98)], 6, hard=True),
        ]

        changes = decode_dialogue_episodes(lines)

        self.assertEqual(lines[1].speaker, "Jake")
        self.assertEqual(changes, 0)


if __name__ == "__main__":
    unittest.main()
