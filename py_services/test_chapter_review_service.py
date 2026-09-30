import json
import tempfile
import unittest
from pathlib import Path

from .chapter_review_service import (
    _modern_nominal_mentions,
    extract_nearby_descriptors,
    normalize_descriptor,
    review_chapter,
)


class ChapterReviewServiceTests(unittest.TestCase):
    def test_uses_modernbooknlp_nominal_mentions_for_confirmed_identity(self):
        attribution = {
            "decisionTrace": {
                "selectedCandidate": "Alice",
                "modernBookNLP": {"corefId": "7", "nominalMentions": ["the cartographer"]},
            },
        }
        character = {"id": "alice", "name": "Alice", "aliases": []}

        self.assertEqual(
            _modern_nominal_mentions(attribution, character),
            ["the cartographer"],
        )

    def test_ignores_modernbooknlp_mentions_after_identity_correction(self):
        attribution = {
            "decisionTrace": {
                "selectedCandidate": "Alice",
                "modernBookNLP": {"corefId": "7", "nominalMentions": ["the cartographer"]},
            },
        }
        character = {"id": "bob", "name": "Bob", "aliases": []}

        self.assertEqual(_modern_nominal_mentions(attribution, character), [])

    def test_extracts_specific_and_generic_descriptors(self):
        text = 'The chubby mage smiled thinly. “Hello.”\nThe orc replied, “No.”'
        first_start = text.index("Hello")
        second_start = text.index("No.")

        self.assertEqual(
            extract_nearby_descriptors(text, {"start": first_start, "end": first_start + 6}),
            ["The chubby mage"],
        )
        self.assertEqual(
            extract_nearby_descriptors(text, {"start": second_start, "end": second_start + 3}),
            ["The orc"],
        )
        self.assertIsNone(normalize_descriptor("he"))
        self.assertIsNone(normalize_descriptor("a few words"))
        self.assertIsNone(normalize_descriptor("the same thing to Malicia"))
        self.assertIsNone(normalize_descriptor("the all orc"))

    def test_relocates_dialogue_when_saved_span_is_stale(self):
        text = 'The enemy lieutenant called out. “First.”\nThe chubby mage smiled thinly. “Hello.”'
        start = text.index("Hello")

        self.assertEqual(
            extract_nearby_descriptors(
                text,
                {"start": text.index("First"), "end": text.index("First") + 6},
                "Hello.",
            ),
            ["The chubby mage"],
        )
        self.assertEqual(start, text.index("Hello"))

    def test_extracts_descriptor_after_comma_ended_dialogue(self):
        text = '“You think I am prejudiced,” the green-eyed man stated.'
        start = text.index("You think")

        self.assertEqual(
            extract_nearby_descriptors(
                text,
                {"start": start, "end": start + len("You think I am prejudiced,")},
                "You think I am prejudiced,",
            ),
            ["the green-eyed man"],
        )

    def test_extracts_descriptor_from_quoted_attribution_with_stale_span(self):
        text = (
            'Nauk laughed.\n'
            '"We march West, once more," the tall orc quoted in Mthethwa.\n'
            '"Waging that same old war," we all echoed.'
        )
        quote = "We march West, once more,"

        self.assertEqual(
            extract_nearby_descriptors(
                text,
                {"start": text.index("Waging"), "end": text.index("Waging") + 6},
                quote,
            ),
            ["the tall orc"],
        )

    def test_extracts_reviewed_chapter_descriptor_forms(self):
        examples = [
            (
                'The Taghreb aristocrat shook her head. "If you are summoned in your station as the Squire."',
                "If you are summoned in your station as the Squire.",
                "The Taghreb aristocrat",
            ),
            (
                'The Staff Tribune met my eyes unflinchingly. "With all due respect, my Lady."',
                "With all due respect, my Lady.",
                "The Staff Tribune",
            ),
            (
                '"Gave them the old Callow treatment, did you?" the orc snickered.',
                "Gave them the old Callow treatment, did you?",
                "the orc",
            ),
        ]

        for text, quote, expected in examples:
            start = text.index(quote)
            with self.subTest(expected=expected):
                self.assertEqual(
                    extract_nearby_descriptors(
                        text,
                        {"start": start, "end": start + len(quote)},
                        quote,
                    ),
                    [expected],
                )

    def test_extracts_roles_without_a_role_vocabulary(self):
        text = 'The phosphorescent cartographer adjusted her spectacles. "The map is wrong."'
        quote = "The map is wrong."
        start = text.index(quote)

        self.assertEqual(
            extract_nearby_descriptors(
                text,
                {"start": start, "end": start + len(quote)},
                quote,
            ),
            ["The phosphorescent cartographer"],
        )

    def test_uses_shared_speech_verbs_without_local_enumeration(self):
        text = '"The stars remember," the moonlit cartographer vociferated.'
        quote = "The stars remember,"
        start = text.index(quote)

        self.assertEqual(
            extract_nearby_descriptors(
                text,
                {"start": start, "end": start + len(quote)},
                quote,
            ),
            ["the moonlit cartographer"],
        )

    def test_does_not_learn_an_unrelated_inanimate_subject(self):
        text = 'The old door creaked. "Hello," the weary traveler said.'
        quote = "Hello,"
        start = text.index(quote)

        self.assertEqual(
            extract_nearby_descriptors(
                text,
                {"start": start, "end": start + len(quote)},
                quote,
            ),
            ["the weary traveler"],
        )

    def test_does_not_treat_quoted_subject_as_a_descriptor(self):
        text = '"The entire point is leverage," Amadeus reminded her.'
        quote = "The entire point is leverage,"
        start = text.index(quote)

        self.assertEqual(
            extract_nearby_descriptors(
                text,
                {"start": start, "end": start + len(quote)},
                quote,
            ),
            [],
        )

    def test_does_not_learn_subject_from_subordinate_narration(self):
        text = '“You know,” I said as the warmth burned down my throat. “Enough.”'
        quote = "Enough."
        start = text.index(quote)

        self.assertEqual(
            extract_nearby_descriptors(
                text,
                {"start": start, "end": start + len(quote)},
                quote,
            ),
            [],
        )

    def test_review_harvests_descriptors_and_marks_lines_confirmed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter = root / "01-Test"
            chapter.mkdir()
            text = 'The chubby mage smiled thinly. “Hello.”\n'
            start = text.index("Hello")
            (chapter / "chapter.txt").write_text(text, encoding="utf-8")
            (root / "characters.json").write_text(json.dumps({
                "formatVersion": "2.0",
                "characters": [{"id": "apprentice", "name": "Apprentice", "aliases": []}],
            }), encoding="utf-8")
            (chapter / "dialogue.json").write_text(json.dumps({
                "formatVersion": "3.2",
                "chapterId": "01-Test",
                "lines": [{
                    "id": 1,
                    "characterId": "apprentice",
                    "text": "Hello.",
                    "span": {"start": start, "end": start + 6},
                    "metadata": {"customTags": {}},
                    "candidates": [],
                    "isConflict": False,
                }],
                "stats": {"totalLines": 1, "conflicts": 0, "characterBreakdown": {"apprentice": 1}},
            }), encoding="utf-8")

            result = review_chapter(str(root), "01-Test")
            saved_dialogue = json.loads((chapter / "dialogue.json").read_text(encoding="utf-8"))
            saved_characters = json.loads((root / "characters.json").read_text(encoding="utf-8"))

        self.assertTrue(result["reviewed"])
        self.assertEqual(result["descriptorsAdded"], 1)
        self.assertEqual(saved_dialogue["lines"][0]["attribution"]["resolutionStatus"], "user_confirmed")
        self.assertEqual(saved_dialogue["stats"]["conflicts"], 0)
        self.assertEqual(saved_characters["characters"][0]["descriptors"], ["The chubby mage"])

    def test_review_rejects_unassigned_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter = root / "01-Test"
            chapter.mkdir()
            (chapter / "chapter.txt").write_text('“Hello.”', encoding="utf-8")
            (root / "characters.json").write_text('{"characters": []}', encoding="utf-8")
            (chapter / "dialogue.json").write_text(json.dumps({
                "lines": [{"id": 1, "characterId": None, "span": {"start": 1, "end": 7}}],
            }), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unassigned"):
                review_chapter(str(root), "01-Test")

    def test_review_accepts_explicit_non_speaker_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter = root / "01-Test"
            chapter.mkdir()
            (chapter / "chapter.txt").write_text("For Procer to be ready.", encoding="utf-8")
            (root / "characters.json").write_text('{"characters": []}', encoding="utf-8")
            (chapter / "dialogue.json").write_text(json.dumps({
                "lines": [{
                    "id": 1,
                    "characterId": None,
                    "isNonSpeaker": True,
                    "text": "For Procer to be ready.",
                    "span": {"start": 0, "end": 23},
                }],
            }), encoding="utf-8")

            result = review_chapter(str(root), "01-Test")

        self.assertTrue(result["reviewed"])
        self.assertEqual(result["descriptorsAdded"], 0)


if __name__ == "__main__":
    unittest.main()