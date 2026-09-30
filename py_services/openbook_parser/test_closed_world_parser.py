import tempfile
import unittest
from pathlib import Path

from .attribution_passes import (
    LineSignals,
    infer_sentence_attributed_speaker,
    rank_candidates,
)
from .booknlp_parser_service import (
    BookNLPParserService,
    build_closed_world_catalog,
    canonicalize_closed_world_name,
    infer_unique_descriptor_speaker,
)
from .dialogue_parser_service import DialogueParserService
from .modernbooknlp_service import ModernBookNLPQuote


class ClosedWorldParserTests(unittest.TestCase):
    def test_speech_tag_prefers_subject_over_named_addressee(self):
        self.assertEqual(
            infer_sentence_attributed_speaker(
                "Morgan asked Alex.",
                ["Alex", "Morgan"],
            ),
            "Morgan",
        )

    def test_post_quote_context_stops_before_next_quote(self):
        options = {
            "episode_joint_decode": False,
            "closed_world_characters": True,
            "character_catalog": [
                {"characterId": "alex", "name": "Alex", "aliases": []},
                {"characterId": "morgan", "name": "Morgan", "aliases": []},
            ],
            "heuristics": {"coreference": False},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter.txt"
            chapter_path.write_text(
                '"First?" "Second," Morgan said.\n',
                encoding="utf-8",
            )
            lines, _, _ = BookNLPParserService().parse_file(
                str(chapter_path),
                options=options,
            )

        dialogue = [line for line in lines if line.line_type == "dialogue"]
        self.assertEqual(len(dialogue), 2)
        self.assertIsNone(
            dialogue[0].attribution["decisionTrace"]["signals"].get(
                "nextSentenceAttributedSpeaker"
            )
        )
        self.assertEqual(dialogue[1].speaker, "Morgan")

    def test_punctuated_began_tag_resolves_speaker(self):
        options = {
            "episode_joint_decode": False,
            "closed_world_characters": True,
            "character_catalog": [
                {"characterId": "alex", "name": "Alex", "aliases": []},
                {"characterId": "morgan", "name": "Morgan", "aliases": []},
            ],
            "heuristics": {"coreference": False},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter.txt"
            chapter_path.write_text(
                '"We should leave," Morgan began.\n',
                encoding="utf-8",
            )
            lines, _, _ = BookNLPParserService().parse_file(
                str(chapter_path),
                options=options,
            )

        dialogue = next(line for line in lines if line.line_type == "dialogue")
        self.assertEqual(dialogue.speaker, "Morgan")
        self.assertIn(
            "post_quote_attribution",
            dialogue.attribution["decisionTrace"]["selectedReasons"],
        )

    def test_modernbooknlp_does_not_override_explicit_local_tag(self):
        options = {
            "episode_joint_decode": False,
            "closed_world_characters": True,
            "character_catalog": [
                {"characterId": "alex", "name": "Alex", "aliases": []},
                {"characterId": "morgan", "name": "Morgan", "aliases": []},
            ],
            "heuristics": {"coreference": False},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter.txt"
            chapter_path.write_text(
                '"Hello," Morgan said to Alex.\n',
                encoding="utf-8",
            )
            service = BookNLPParserService()
            legacy_lines, _, _ = service.parse_file(str(chapter_path), options=options)
            legacy_dialogue = next(
                line for line in legacy_lines if line.line_type == "dialogue"
            )
            service.modernbooknlp_service.attribute_file = lambda *_args: [
                ModernBookNLPQuote(
                    start=legacy_dialogue.span_start,
                    end=legacy_dialogue.span_end,
                    speaker="Alex",
                    coref_id="1",
                )
            ]
            lines, _, _ = service.parse_file(
                str(chapter_path),
                options={**options, "parser_backend": "modernbooknlp"},
            )

        dialogue = next(line for line in lines if line.line_type == "dialogue")
        self.assertEqual(dialogue.speaker, "Morgan")
        self.assertEqual(
            dialogue.attribution["decisionTrace"]["modernBookNLPDecision"],
            "rejected_explicit_local_attribution",
        )

    def test_alias_canonicalizes_to_catalogue_name(self):
        catalog = build_closed_world_catalog([
            {"characterId": "jake", "name": "Jake Thayne", "aliases": ["Jake", "Chosen"], "gender": "Male"},
        ])
        self.assertEqual(canonicalize_closed_world_name("Chosen", catalog), "Jake Thayne")

    def test_unknown_candidate_is_rejected(self):
        catalog = build_closed_world_catalog([
            {"characterId": "jake", "name": "Jake Thayne", "aliases": [], "gender": "Male"},
        ])
        self.assertIsNone(canonicalize_closed_world_name("Masked Warrior", catalog))

    def test_unique_descriptor_is_soft_identity_evidence(self):
        catalog = build_closed_world_catalog([
            {"characterId": "apprentice", "name": "Apprentice", "descriptors": ["chubby mage"]},
            {"characterId": "masego", "name": "Masego", "descriptors": ["the mage"]},
        ])

        self.assertEqual(
            infer_unique_descriptor_speaker("The chubby mage smiled thinly.", catalog),
            "Apprentice",
        )
        self.assertIsNone(canonicalize_closed_world_name("chubby mage", catalog))

    def test_descriptor_shared_by_characters_is_ignored(self):
        catalog = build_closed_world_catalog([
            {"characterId": "hakram", "name": "Hakram", "descriptors": ["the orc"]},
            {"characterId": "nauk", "name": "Nauk", "descriptors": ["orc"]},
        ])

        self.assertIsNone(infer_unique_descriptor_speaker("The orc frowned.", catalog))

    def test_unique_descriptor_contributes_to_parser_candidate_score(self):
        options = {
            "pov_mode": "third_person",
            "episode_joint_decode": False,
            "closed_world_characters": True,
            "character_catalog": [
                {
                    "characterId": "apprentice",
                    "name": "Apprentice",
                    "aliases": [],
                    "descriptors": ["chubby mage"],
                    "gender": "Male",
                }
            ],
            "heuristics": {"coreference": False},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter.txt"
            chapter_path.write_text(
                'The chubby mage smiled thinly. “Spoken like someone who has never seen it.”\n',
                encoding="utf-8",
            )
            lines, _, _ = BookNLPParserService().parse_file(
                str(chapter_path),
                options=options,
            )

        dialogue = next(line for line in lines if line.line_type == "dialogue")
        self.assertEqual(dialogue.speaker, "Apprentice")
        self.assertIn(
            "learned_descriptor_match",
            dialogue.attribution["decisionTrace"]["selectedReasons"],
        )

    def test_unique_descriptor_outweighs_duplicate_parser_legacy_vote(self):
        signals = LineSignals(
            predicted_name="Jake",
            legacy_name="Jake",
            prev_resolved=None,
            next_resolved=None,
            used_fallback_alignment=False,
            exact_quote_match=True,
            quote_boundary_quality=0.95,
            nearby_speech_verb=False,
            has_coref_name=False,
            alias_match_strength=0.0,
            descriptor_match="Archweaver",
        )

        decision = rank_candidates(["Jake", "Archweaver"], signals)

        self.assertEqual(decision.chosen_name, "Archweaver")
        self.assertIn("learned_descriptor_match", decision.ranked[0].reasons)

    def test_catalog_alias_is_used_by_legacy_attribution_pass(self):
        options = {
            "closed_world_characters": True,
            "character_catalog": [
                {
                    "characterId": "jake",
                    "name": "Jake Thayne",
                    "aliases": ["Chosen"],
                    "gender": "Male",
                }
            ],
            "heuristics": {"coreference": False},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter.txt"
            chapter_path.write_text(
                'Chosen said, "We should leave now."\n',
                encoding="utf-8",
            )
            lines, names = DialogueParserService().parse_file(
                str(chapter_path),
                options=options,
            )

        dialogue = next(line for line in lines if line.line_type == "dialogue")
        self.assertEqual(dialogue.speaker, "Jake Thayne")
        self.assertIn("Jake Thayne", dialogue.suggestions)
        self.assertIn("Jake Thayne", names)

    def test_first_person_tag_remains_resolved_to_configured_protagonist(self):
        options = {
            "pov_mode": "first_person",
            "protagonists": ["Catherine"],
            "closed_world_characters": True,
            "character_catalog": [
                {
                    "characterId": "catherine",
                    "name": "Catherine",
                    "aliases": [],
                    "gender": "Female",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter.txt"
            chapter_path.write_text('"Hello," I said.\n', encoding="utf-8")
            lines, _, _ = BookNLPParserService().parse_file(
                str(chapter_path),
                options=options,
            )

        dialogue = next(line for line in lines if line.line_type == "dialogue")
        self.assertEqual(dialogue.speaker, "Catherine")
        self.assertFalse(dialogue.is_suggestion)
        self.assertIn(
            "0p_protagonist_first_person",
            dialogue.attribution["decisionTrace"]["selectedReasons"],
        )


if __name__ == "__main__":
    unittest.main()
