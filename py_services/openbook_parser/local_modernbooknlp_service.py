import tempfile
import unittest
import csv
import json
from pathlib import Path
from unittest.mock import patch

import torch

from .dialogue_parser_service import DialogueLine
from .modernbooknlp_service import (
    CACHE_DIRECTORY,
    CACHE_MANIFEST,
    ModernBookNLPQuote,
    ModernBookNLPService,
    _booknlp_checkpoint_compatibility,
    _load_cached_book_predictions,
    _read_tsv,
    _text_hash,
    align_dialogue_lines,
    collect_coref_nominal_mentions,
    load_predictions,
)


class ModernBookNLPServiceTests(unittest.TestCase):
    def test_tsv_reader_accepts_whole_book_fields_and_restores_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "book.entities"
            large_text = "a" * 150_000
            path.write_text(f"coref\ttext\n7\t{large_text}\n", encoding="utf-8")
            original_limit = csv.field_size_limit()

            rows = _read_tsv(path)

        self.assertEqual(rows, [{"coref": "7", "text": large_text}])
        self.assertEqual(csv.field_size_limit(), original_limit)

    def test_booknlp_checkpoint_compatibility_ignores_only_position_ids(self):
        from booknlp.english.bert_qa import QuotationAttribution

        model = torch.nn.Linear(2, 1)
        checkpoint = model.state_dict()
        checkpoint["bert.embeddings.position_ids"] = torch.arange(2)
        original_loader = torch.nn.Module.load_state_dict
        original_initializer = QuotationAttribution.__init__

        with _booknlp_checkpoint_compatibility():
            model.load_state_dict(checkpoint)
            with self.assertRaisesRegex(RuntimeError, "unexpected.weight"):
                model.load_state_dict({**model.state_dict(), "unexpected.weight": torch.ones(1)})

        self.assertIs(torch.nn.Module.load_state_dict, original_loader)
        self.assertIs(QuotationAttribution.__init__, original_initializer)

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

    def test_collects_only_nominal_person_mentions_by_coreference_cluster(self):
        entities = [
            {"coref": "7", "prop": "PROP", "cat": "PER", "text": "Alice"},
            {"coref": "7", "prop": "PRON", "cat": "PER", "text": "she"},
            {"coref": "7", "prop": "NOM", "cat": "PER", "text": "the cartographer"},
            {"coref": "7", "prop": "NOM", "cat": "PER", "text": "The Cartographer"},
            {"coref": "8", "prop": "NOM", "cat": "LOC", "text": "the duchy"},
        ]

        self.assertEqual(
            collect_coref_nominal_mentions(entities),
            {"7": ("the cartographer",)},
        )

    def test_cached_predictions_are_mapped_to_chapter_offsets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter = root / "01-Test"
            cache = root / CACHE_DIRECTORY
            chapter.mkdir()
            cache.mkdir()
            source = chapter / "chapter.txt"
            source.write_text('“Hello.”', encoding="utf-8")
            (cache / CACHE_MANIFEST).write_text(json.dumps({
                "version": 1,
                "chapterCount": 1,
                "chapters": [{
                    "name": "01-Test",
                    "path": "01-Test/chapter.txt",
                    "start": 100,
                    "end": 108,
                    "sha256": _text_hash('“Hello.”'),
                }],
            }), encoding="utf-8")
            (cache / "book.entities").write_text(
                "COREF\tprop\tcat\ttext\n7\tPROP\tPER\tAlice\n7\tNOM\tPER\tthe cartographer\n",
                encoding="utf-8",
            )
            (cache / "book.tokens").write_text(
                "token_ID_within_document\tbyte_onset\tbyte_offset\n10\t101\t103\n11\t103\t106\n",
                encoding="utf-8",
            )
            (cache / "book.quotes").write_text(
                "quote_start\tquote_end\tchar_id\n10\t12\t7\n",
                encoding="utf-8",
            )

            predictions = ModernBookNLPService().attribute_file(
                str(source),
                [{"name": "Alice", "aliases": []}],
            )

        self.assertEqual(
            predictions,
            [ModernBookNLPQuote(1, 6, "Alice", "7", ("the cartographer",))],
        )

    def test_cached_book_outputs_are_decoded_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter = root / "01-Test"
            cache = root / CACHE_DIRECTORY
            chapter.mkdir()
            cache.mkdir()
            source = chapter / "chapter.txt"
            source.write_text('“Hello.”', encoding="utf-8")
            (cache / CACHE_MANIFEST).write_text(json.dumps({
                "version": 1,
                "chapterCount": 1,
                "chapters": [{
                    "name": "01-Test",
                    "path": "01-Test/chapter.txt",
                    "start": 0,
                    "end": 8,
                    "sha256": _text_hash('“Hello.”'),
                }],
            }), encoding="utf-8")
            (cache / "book.entities").write_text(
                "COREF\tprop\tcat\ttext\n7\tPROP\tPER\tAlice\n",
                encoding="utf-8",
            )
            (cache / "book.tokens").write_text(
                "token_ID_within_document\tbyte_onset\tbyte_offset\n10\t1\t3\n11\t3\t6\n",
                encoding="utf-8",
            )
            (cache / "book.quotes").write_text(
                "quote_start\tquote_end\tchar_id\n10\t12\t7\n",
                encoding="utf-8",
            )
            service = ModernBookNLPService()
            _load_cached_book_predictions.cache_clear()

            with patch(
                "py_services.openbook_parser.modernbooknlp_service._read_tsv",
                wraps=_read_tsv,
            ) as read_tsv:
                service.attribute_file(str(source), [{"name": "Alice", "aliases": []}])
                service.attribute_file(str(source), [{"name": "Alice", "aliases": []}])

            self.assertEqual(read_tsv.call_count, 3)


if __name__ == "__main__":
    unittest.main()