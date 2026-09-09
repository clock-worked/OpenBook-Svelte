import json
import tempfile
import unittest
from pathlib import Path

try:
    from .modernbooknlp_benchmark import (
        GoldQuote,
        PredictedQuote,
        build_canonical_lookup,
        build_input_corpus,
        evaluate_predictions,
        load_modernbooknlp_predictions,
        map_coref_clusters,
    )
except ImportError:
    from modernbooknlp_benchmark import (
        GoldQuote,
        PredictedQuote,
        build_canonical_lookup,
        build_input_corpus,
        evaluate_predictions,
        load_modernbooknlp_predictions,
        map_coref_clusters,
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class ModernBookNlpBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_input_corpus_keeps_chapter_byte_offsets_and_gold_quotes(self) -> None:
        book = self.root / "Book-15"
        first = book / "chapter-a"
        second = book / "chapter-b"
        first.mkdir(parents=True)
        second.mkdir()
        (first / "chapter.txt").write_text('Intro. “Hello.”', encoding="utf-8")
        (second / "chapter.txt").write_text('“Bye.”', encoding="utf-8")
        write_json(first / "dialogue.json", {"lines": [
            {"id": 0, "characterId": "narrator", "text": "Intro.", "span": {"start": 0, "end": 6}},
            {"id": 1, "characterId": "alice", "text": "Hello.", "span": {"start": 999, "end": 1005}},
        ]})
        write_json(second / "dialogue.json", {"lines": [
            {"id": 0, "characterId": "bob", "text": "Bye.", "span": {"start": 999, "end": 1003}},
        ]})

        corpus = build_input_corpus(book)

        self.assertEqual(corpus.text, 'Intro. “Hello.”\n\n“Bye.”')
        self.assertEqual(
            [(q.character_id, q.start, q.end) for q in corpus.gold_quotes],
            [("alice", 8, 14), ("bob", 18, 22)],
        )
        self.assertEqual(corpus.chapters[1].byte_start, 17)

    def test_unresolved_dialogue_is_retained_as_gold(self) -> None:
        book = self.root / "Book-15"
        chapter = book / "01"
        chapter.mkdir(parents=True)
        (chapter / "chapter.txt").write_text('“Who am I?”', encoding="utf-8")
        write_json(book / "characters.json", {"characters": []})
        write_json(chapter / "dialogue.json", {"lines": [
            {"id": 1, "characterId": None, "text": "Who am I?", "span": {"start": 0, "end": 1}},
        ]})

        corpus = build_input_corpus(book)

        self.assertEqual(len(corpus.gold_quotes), 1)
        self.assertIsNone(corpus.gold_quotes[0].character_id)
        self.assertEqual((corpus.gold_quotes[0].start, corpus.gold_quotes[0].end), (1, 10))

    def test_canonical_mapping_uses_names_aliases_and_weighted_cluster_mentions(self) -> None:
        characters = {"characters": [
            {"id": "alice", "name": "Alice Smith", "aliases": ["Alice"]},
            {"id": "bob", "name": "Robert", "aliases": ["Bob"]},
        ]}
        lookup = build_canonical_lookup(characters)
        entities = [
            {"coref": "7", "prop": "PRON", "cat": "PER", "text": "she"},
            {"coref": "7", "prop": "PROP", "cat": "PER", "text": "Alice"},
            {"coref": "8", "prop": "PROP", "cat": "PER", "text": "Unknown"},
        ]

        mapping = map_coref_clusters(entities, lookup)

        self.assertEqual(mapping["7"], "alice")
        self.assertIsNone(mapping["8"])

    def test_load_predictions_converts_token_indices_to_utf8_byte_spans(self) -> None:
        tokens = self.root / "sample.tokens"
        quotes = self.root / "sample.quotes"
        tokens.write_text(
            "paragraph_ID\tsentence_ID\ttoken_ID_within_sentence\ttoken_ID_within_document\tword\tlemma\tbyte_onset\tbyte_offset\tPOS_tag\tfine_POS_tag\tdependency_relation\tsyntactic_head_ID\tevent\n"
            "0\t0\t0\t0\t“\t“\t0\t3\tPUNCT\t``\tpunct\t1\tO\n"
            "0\t0\t1\t1\tHi\thi\t3\t5\tINTJ\tUH\tROOT\t1\tO\n"
            "0\t0\t2\t2\t”\t”\t5\t8\tPUNCT\t''\tpunct\t1\tO\n",
            encoding="utf-8",
        )
        quotes.write_text(
            "quote_start\tquote_end\tmention_start\tmention_end\tmention_phrase\tchar_id\tquote\n"
            "0\t3\t3\t3\tAlice\t7\t“ Hi ”\n",
            encoding="utf-8",
        )

        predictions = load_modernbooknlp_predictions(quotes, tokens, {"7": "alice"})

        self.assertEqual(len(predictions), 1)
        self.assertEqual((predictions[0].start, predictions[0].end, predictions[0].character_id), (0, 8, "alice"))

    def test_load_predictions_uses_document_token_id_not_tsv_row_position(self) -> None:
        tokens = self.root / "sample.tokens"
        quotes = self.root / "sample.quotes"
        tokens.write_text(
            "paragraph_ID\tsentence_ID\ttoken_ID_within_sentence\ttoken_ID_within_document\tword\tlemma\tbyte_onset\tbyte_offset\tPOS_tag\tfine_POS_tag\tdependency_relation\tsyntactic_head_ID\tevent\n"
            "0\t0\t0\t10\t“\t“\t0\t3\tPUNCT\t``\tpunct\t11\tO\n"
            "0\t0\t1\t11\tHi\thi\t3\t5\tINTJ\tUH\tROOT\t11\tO\n"
            "0\t0\t2\t12\t”\t”\t5\t8\tPUNCT\t''\tpunct\t11\tO\n",
            encoding="utf-8",
        )
        quotes.write_text(
            "quote_start\tquote_end\tmention_start\tmention_end\tmention_phrase\tchar_id\tquote\n"
            "10\t13\t3\t3\tAlice\t7\t“ Hi ”\n",
            encoding="utf-8",
        )

        predictions = load_modernbooknlp_predictions(quotes, tokens, {"7": "alice"})

        self.assertEqual((predictions[0].start, predictions[0].end), (0, 8))

    def test_evaluation_reports_detection_attribution_and_end_to_end(self) -> None:
        gold = [
            GoldQuote("c", 1, "alice", 10, 20, "hello"),
            GoldQuote("c", 2, "bob", 30, 40, "bye"),
        ]
        predicted = [
            PredictedQuote(11, 19, "alice", "7", "hello"),
            PredictedQuote(31, 39, "alice", "7", "bye"),
            PredictedQuote(50, 60, None, None, "extra"),
        ]

        result = evaluate_predictions(gold, predicted)

        self.assertEqual(result["matched_quotes"], 2)
        self.assertAlmostEqual(result["quote_detection"]["precision"], 2 / 3)
        self.assertAlmostEqual(result["quote_detection"]["recall"], 1.0)
        self.assertAlmostEqual(result["quote_detection"]["f1"], 0.8)
        self.assertEqual(result["speaker_attribution"]["correct"], 1)
        self.assertAlmostEqual(result["speaker_attribution"]["accuracy_on_matched"], 0.5)
        self.assertAlmostEqual(result["end_to_end_correct_speaker_recall"], 0.5)

    def test_overlap_matching_is_one_to_one(self) -> None:
        gold = [
            GoldQuote("c", 1, "alice", 0, 10, "a"),
            GoldQuote("c", 2, "alice", 10, 20, "b"),
        ]
        predicted = [PredictedQuote(5, 15, "alice", "1", "wide")]

        result = evaluate_predictions(gold, predicted)

        self.assertEqual(result["matched_quotes"], 1)
        self.assertAlmostEqual(result["quote_detection"]["recall"], 0.5)

    def test_tiny_overlap_is_not_a_quote_match(self) -> None:
        gold = [GoldQuote("c", 1, "alice", 0, 100, "long")]
        predicted = [PredictedQuote(99, 120, "alice", "1", "other")]

        result = evaluate_predictions(gold, predicted)

        self.assertEqual(result["matched_quotes"], 0)

    def test_book14_chosen_speaker_is_normalized_through_catalog(self) -> None:
        book = self.root / "Book-14"
        chapter = book / "01"
        chapter.mkdir(parents=True)
        (chapter / "chapter.txt").write_text('“Hi.”', encoding="utf-8")
        write_json(book / "characters.json", {"characters": [
            {"id": "alice", "name": "Alice", "aliases": ["Al"]},
        ]})
        write_json(chapter / "dialogue.json", {"lines": [
            {"id": 1, "chosenSpeaker": "Al", "text": "Hi.", "span": {"start": 1, "end": 4}},
        ]})

        corpus = build_input_corpus(book)

        self.assertEqual(corpus.gold_quotes[0].character_id, "alice")

    def test_missing_dialogue_or_hidden_directories_are_skipped(self) -> None:
        book = self.root / "Book-15"
        (book / ".cache").mkdir(parents=True)
        chapter = book / "01"
        chapter.mkdir()
        (chapter / "chapter.txt").write_text("text", encoding="utf-8")

        corpus = build_input_corpus(book)

        self.assertEqual(corpus.text, "")
        self.assertEqual(corpus.chapters, [])
        self.assertEqual(corpus.gold_quotes, [])


if __name__ == "__main__":
    unittest.main()
