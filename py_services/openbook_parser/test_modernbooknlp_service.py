import tempfile
import unittest
from pathlib import Path

from .dialogue_parser_service import DialogueLine
from .modernbooknlp_service import ModernBookNLPQuote, align_dialogue_lines, load_predictions


class ModernBookNLPServiceTests(unittest.TestCase):
    def test_predictions_use_document_ids_and_exclusive_end(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tokens = root / "chapter.tokens"
            quotes = root / "chapter.quotes"
            tokens.write_text(
                "token_ID_within_document\tbyte_onset\tbyte_offset\n"
                "10\t0\t1\n11\t1\t3\n12\t3\t4\n",
                encoding="utf-8",
            )
            quotes.write_text(
                "quote_start\tquote_end\tchar_id\n10\t13\t7\n",
                encoding="utf-8",
            )

            predictions = load_predictions(quotes, tokens, {"7": "Alice"})

            self.assertEqual(predictions, [ModernBookNLPQuote(0, 4, "Alice", "7")])

    def test_alignment_requires_most_of_openbook_dialogue_span(self):
        lines = [
            DialogueLine("Hello there", "Unknown", "dialogue", span_start=10, span_end=21),
            DialogueLine("Narration", "Narrator", "narration", span_start=22, span_end=31),
        ]
        predictions = [ModernBookNLPQuote(9, 22, "Alice", "7")]

        aligned = align_dialogue_lines(lines, predictions)

        self.assertEqual(aligned, {0: predictions[0]})


if __name__ == "__main__":
    unittest.main()