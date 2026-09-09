"""ModernBookNLP speaker attribution over OpenBook dialogue spans."""

from __future__ import annotations

import csv
import re
import tempfile
import threading
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class ModernBookNLPQuote:
    start: int
    end: int
    speaker: Optional[str]
    coref_id: Optional[str]


_MODEL = None
_MODEL_LOCK = threading.Lock()


def _normalize(value: object) -> str:
    text = str(value or "").casefold().replace("_", " ").replace("-", " ")
    return " ".join(re.findall(r"[\w']+", text, flags=re.UNICODE))


def _read_tsv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def build_catalog_lookup(rows: Iterable[Mapping[str, object]]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    ambiguous = set()
    for row in rows or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        for surface in [row.get("characterId"), name, *(row.get("aliases") or [])]:
            key = _normalize(surface)
            if not key:
                continue
            if key in lookup and lookup[key] != name:
                ambiguous.add(key)
            else:
                lookup[key] = name
    for key in ambiguous:
        lookup.pop(key, None)
    return lookup


def map_coref_clusters(
    entities: Iterable[Mapping[str, object]],
    catalog_lookup: Mapping[str, str],
) -> Dict[str, Optional[str]]:
    scores: Dict[str, Counter] = defaultdict(Counter)
    seen = set()
    for entity in entities:
        coref_id = str(entity.get("coref") or entity.get("COREF") or "").strip()
        if not coref_id:
            continue
        seen.add(coref_id)
        canonical = catalog_lookup.get(_normalize(entity.get("text")))
        if not canonical:
            continue
        prop = str(entity.get("prop") or "").upper()
        scores[coref_id][canonical] += 10 if prop == "PROP" else 2 if prop == "NOM" else 1

    mapping: Dict[str, Optional[str]] = {}
    for coref_id in seen:
        ranked = scores[coref_id].most_common()
        mapping[coref_id] = (
            ranked[0][0]
            if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1])
            else None
        )
    return mapping


def load_predictions(
    quotes_path: Path,
    tokens_path: Path,
    coref_mapping: Mapping[str, Optional[str]],
) -> List[ModernBookNLPQuote]:
    token_spans: Dict[int, tuple[int, int]] = {}
    for token in _read_tsv(tokens_path):
        try:
            token_spans[int(token["token_ID_within_document"])] = (
                int(token["byte_onset"]),
                int(token["byte_offset"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    token_ids = sorted(token_spans)

    predictions: List[ModernBookNLPQuote] = []
    for quote in _read_tsv(quotes_path):
        try:
            start_token = int(quote["quote_start"])
            end_token = int(quote["quote_end"])
        except (KeyError, TypeError, ValueError):
            continue
        start_index = bisect_left(token_ids, start_token)
        end_index = bisect_left(token_ids, end_token) - 1
        if start_index >= len(token_ids) or end_index < start_index:
            continue
        coref_id = str(quote.get("char_id") or "").strip()
        if not coref_id or coref_id.casefold() == "none":
            coref_id = None
        predictions.append(ModernBookNLPQuote(
            start=token_spans[token_ids[start_index]][0],
            end=token_spans[token_ids[end_index]][1],
            speaker=coref_mapping.get(coref_id) if coref_id else None,
            coref_id=coref_id,
        ))
    return predictions


def align_dialogue_lines(
    lines: Sequence[object],
    predictions: Sequence[ModernBookNLPQuote],
) -> Dict[int, ModernBookNLPQuote]:
    candidates = []
    for line_index, line in enumerate(lines):
        if getattr(line, "line_type", None) != "dialogue":
            continue
        line_start = getattr(line, "span_start", None)
        line_end = getattr(line, "span_end", None)
        if not isinstance(line_start, int) or not isinstance(line_end, int) or line_end <= line_start:
            continue
        for prediction_index, prediction in enumerate(predictions):
            if not prediction.speaker:
                continue
            overlap = max(0, min(line_end, prediction.end) - max(line_start, prediction.start))
            coverage = overlap / (line_end - line_start)
            if coverage >= 0.8:
                candidates.append((coverage, overlap, line_index, prediction_index))

    aligned: Dict[int, ModernBookNLPQuote] = {}
    used_predictions = set()
    for _, _, line_index, prediction_index in sorted(candidates, reverse=True):
        if line_index in aligned or prediction_index in used_predictions:
            continue
        aligned[line_index] = predictions[prediction_index]
        used_predictions.add(prediction_index)
    return aligned


def _get_model():
    global _MODEL
    if _MODEL is None:
        from booknlp.english.english_booknlp import EnglishBookNLP

        _MODEL = EnglishBookNLP({
            "pipeline": "entity,quote,coref",
            "model": "big",
            "modern_qa": True,
            "direct_qa": False,
        })
    return _MODEL


class ModernBookNLPService:
    def attribute_file(
        self,
        file_path: str,
        character_catalog: Iterable[Mapping[str, object]],
    ) -> List[ModernBookNLPQuote]:
        catalog_lookup = build_catalog_lookup(character_catalog)
        if not catalog_lookup:
            raise ValueError("ModernBookNLP attribution requires a non-empty character catalog")

        with tempfile.TemporaryDirectory(prefix="openbook-modernbooknlp-") as temp_dir:
            output_dir = Path(temp_dir)
            book_id = "chapter"
            with _MODEL_LOCK:
                _get_model().process(file_path, str(output_dir), book_id)
            entities = _read_tsv(output_dir / f"{book_id}.entities")
            coref_mapping = map_coref_clusters(entities, catalog_lookup)
            return load_predictions(
                output_dir / f"{book_id}.quotes",
                output_dir / f"{book_id}.tokens",
                coref_mapping,
            )