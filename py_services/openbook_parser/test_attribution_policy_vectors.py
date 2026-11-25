"""Cross-language vectors for the attribution assert/suggestion policy spec.

Reads the shared vector file tests/attribution_policy_vectors.json and
asserts the Python spec (attribution_decision.py) on every vector. The TS
side (apps/desktop/src/lib/services/attributionPolicy.ts) asserts the same
vectors in apps/desktop/src/lib/services/__tests__/attributionPolicy.test.ts
(vitest). Keep both green when changing either side (ADR-0013).
"""

import json
import unittest
from pathlib import Path

from .attribution_decision import (
    CATEGORY_PRECEDENCE,
    FIRST_PERSON_RULES,
    POLICY_EXEMPTIONS,
    POLICY_RULES,
    POLICY_THRESHOLDS,
    RULE_CATEGORIES,
    PolicyFacts,
    evaluate_policy,
    trace_is_first_person,
)

VECTORS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "tests" / "attribution_policy_vectors.json"
)

#: Vector flag (camelCase, as on the TS side) -> PolicyFacts field.
FLAG_FIELDS = {
    "usedFallbackAlignment": "used_fallback_alignment",
    "questionNextSpeakerConflict": "question_next_speaker_conflict",
    "genderConflict": "gender_conflict",
    "genericTopChoice": "generic_top_choice",
    "hasSupportReason": "has_support_reason",
    "parserSuggestion": "parser_suggestion",
    "hasNamedCandidates": "has_named_candidates",
    "speakerBlocked": "speaker_blocked",
    "firstPerson": "first_person",
}


def load_vectors() -> dict:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


class AttributionPolicyVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = load_vectors()

    def test_spec_constants_match_the_vector_file(self):
        spec = self.vectors["spec"]
        self.assertEqual(spec["thresholds"], dict(POLICY_THRESHOLDS))
        self.assertEqual(
            spec["rules"],
            [{"name": rule.name, "edges": list(rule.edges)} for rule in POLICY_RULES],
        )
        self.assertEqual(
            spec["exemptions"],
            {edge: list(names) for edge, names in POLICY_EXEMPTIONS.items()},
        )
        self.assertEqual(spec["categories"], list(RULE_CATEGORIES))
        self.assertEqual(spec["categoryPrecedence"], list(CATEGORY_PRECEDENCE))
        # The TS legacy fallback set is exactly the registry's FIRST_PERSON rules.
        self.assertEqual(set(spec["legacyFirstPersonRules"]), set(FIRST_PERSON_RULES))

    def test_policy_vectors(self):
        self.assertTrue(self.vectors["policy"])
        for vector in self.vectors["policy"]:
            with self.subTest(vector=vector["name"]):
                unknown = set(vector["flags"]) - set(FLAG_FIELDS)
                self.assertEqual(unknown, set())
                facts = PolicyFacts.from_confidences(
                    vector["confidences"],
                    **{FLAG_FIELDS[key]: value for key, value in vector["flags"].items()},
                )
                outcome = evaluate_policy(vector["edge"], facts)
                self.assertEqual(
                    {
                        "isSuggestion": outcome.is_suggestion,
                        "firedRules": list(outcome.fired_rules),
                        "exemptedBy": outcome.exempted_by,
                    },
                    vector["expected"],
                )

    def test_first_person_vectors(self):
        self.assertTrue(self.vectors["firstPerson"])
        for vector in self.vectors["firstPerson"]:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(trace_is_first_person(vector["trace"]), vector["expected"])


if __name__ == "__main__":
    unittest.main()
