import tempfile
import unittest
from pathlib import Path

from .booknlp_parser_service import BookNLPParserService, build_closed_world_catalog, canonicalize_closed_world_name
from .dialogue_parser_service import DialogueParserService


class ClosedWorldParserTests(unittest.TestCase):
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
