"""ModernBookNLP speaker attribution over OpenBook dialogue spans."""

from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import re
import shutil
import sys
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
    nominal_mentions: tuple[str, ...] = ()


_MODEL_LOCK = threading.Lock()
_CSV_LOCK = threading.Lock()
CACHE_DIRECTORY = ".modernbooknlp"
CACHE_BOOK_ID = "book"
CACHE_MANIFEST = "manifest.json"


def _normalize(value: object) -> str:
    text = str(value or "").casefold().replace("_", " ").replace("-", " ")
    return " ".join(re.findall(r"[\w']+", text, flags=re.UNICODE))


def _read_tsv(path: Path) -> List[Dict[str, str]]:
    with _CSV_LOCK:
        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle, delimiter="\t"))
        finally:
            csv.field_size_limit(previous_limit)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _chapter_sources(book_root: Path) -> List[Path]:
    return sorted(
        path
        for path in book_root.glob("*/chapter.txt")
        if not path.parent.name.startswith(".")
    )


@contextmanager
def _booknlp_checkpoint_compatibility():
    import torch
    from booknlp.english.bert_qa import QuotationAttribution
    from booknlp.english.entity_tagger import LitBankEntityTagger
    from booknlp.english.litbank_coref import LitBankCoref

    original_load_state_dict = torch.nn.Module.load_state_dict
    model_classes = (QuotationAttribution, LitBankEntityTagger, LitBankCoref)
    original_initializers = {model_class: model_class.__init__ for model_class in model_classes}

    def load_state_dict_without_obsolete_buffers(module, state_dict, *args, **kwargs):
        filtered = state_dict.copy()
        filtered.pop("bert.embeddings.position_ids", None)
        for key in tuple(filtered):
            if key.endswith(".bert.embeddings.position_ids"):
                filtered.pop(key)
        return original_load_state_dict(module, filtered, *args, **kwargs)

    def windows_safe_initializer(original_initializer):
        def initialize(instance, model_path, *args, **kwargs):
            return original_initializer(instance, str(model_path).replace("\\", "/"), *args, **kwargs)
        return initialize

    torch.nn.Module.load_state_dict = load_state_dict_without_obsolete_buffers
    for model_class, initializer in original_initializers.items():
        model_class.__init__ = windows_safe_initializer(initializer)
    try:
        yield
    finally:
        torch.nn.Module.load_state_dict = original_load_state_dict
        for model_class, initializer in original_initializers.items():
            model_class.__init__ = initializer


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


def collect_coref_nominal_mentions(
    entities: Iterable[Mapping[str, object]],
) -> Dict[str, tuple[str, ...]]:
    mentions: Dict[str, List[str]] = defaultdict(list)
    seen: Dict[str, set[str]] = defaultdict(set)
    for entity in entities:
        coref_id = str(entity.get("coref") or entity.get("COREF") or "").strip()
        if (
            not coref_id
            or str(entity.get("prop") or "").upper() != "NOM"
            or str(entity.get("cat") or "").upper() != "PER"
        ):
            continue
        text = str(entity.get("text") or "").strip()
        key = _normalize(text)
        if text and key and key not in seen[coref_id]:
            seen[coref_id].add(key)
            mentions[coref_id].append(text)
    return {coref_id: tuple(values) for coref_id, values in mentions.items()}


def load_predictions(
    quotes_path: Path,
    tokens_path: Path,
    coref_mapping: Mapping[str, Optional[str]],
    nominal_mentions: Optional[Mapping[str, tuple[str, ...]]] = None,
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
            nominal_mentions=(nominal_mentions or {}).get(coref_id, ()) if coref_id else (),
        ))
    return predictions


@lru_cache(maxsize=8)
def _load_cached_book_predictions(
    cache_directory: str,
    file_signatures: tuple[tuple[int, int], ...],
    catalog_items: tuple[tuple[str, str], ...],
) -> tuple[ModernBookNLPQuote, ...]:
    del file_signatures
    cache_dir = Path(cache_directory)
    entities = _read_tsv(cache_dir / f"{CACHE_BOOK_ID}.entities")
    coref_mapping = map_coref_clusters(entities, dict(catalog_items))
    return tuple(load_predictions(
        cache_dir / f"{CACHE_BOOK_ID}.quotes",
        cache_dir / f"{CACHE_BOOK_ID}.tokens",
        coref_mapping,
        collect_coref_nominal_mentions(entities),
    ))


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


@lru_cache(maxsize=1)
def _get_model():
    from booknlp.english.english_booknlp import EnglishBookNLP

    with _booknlp_checkpoint_compatibility():
        return EnglishBookNLP({
            "pipeline": "entity,quote,coref",
            "model": "big",
            "modern_qa": True,
            "direct_qa": False,
        })


class ModernBookNLPService:
    def build_book_cache(self, book_root: str) -> Dict[str, object]:
        root = Path(book_root).resolve()
        chapters = _chapter_sources(root)
        if not chapters:
            raise ValueError("No chapter.txt files found in the selected book")

        parts: List[str] = []
        manifest_chapters: List[Dict[str, object]] = []
        offset = 0
        for chapter_path in chapters:
            text = _read_text(chapter_path)
            separator = "" if not parts else "\n\n"
            parts.append(separator)
            offset += len(separator)
            start = offset
            parts.append(text)
            offset += len(text)
            manifest_chapters.append({
                "name": chapter_path.parent.name,
                "path": str(chapter_path.relative_to(root)).replace("\\", "/"),
                "start": start,
                "end": offset,
                "sha256": _text_hash(text),
            })

        cache_dir = root / CACHE_DIRECTORY
        with tempfile.TemporaryDirectory(prefix=".modernbooknlp-build-", dir=root) as temp_dir:
            output_dir = Path(temp_dir)
            corpus_path = output_dir / f"{CACHE_BOOK_ID}.txt"
            corpus_path.write_text("".join(parts), encoding="utf-8")
            with _MODEL_LOCK:
                _get_model().process(str(corpus_path), str(output_dir), CACHE_BOOK_ID)
            manifest = {
                "version": 1,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "chapterCount": len(manifest_chapters),
                "chapters": manifest_chapters,
            }
            (output_dir / CACHE_MANIFEST).write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            output_dir.rename(cache_dir)
        return manifest

    def cache_status(self, book_root: str) -> Dict[str, object]:
        root = Path(book_root).resolve()
        manifest_path = root / CACHE_DIRECTORY / CACHE_MANIFEST
        if not manifest_path.is_file():
            return {"available": False, "staleChapters": []}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stale = []
        manifest_chapters = manifest.get("chapters") or []
        for chapter in manifest_chapters:
            path = root / str(chapter.get("path") or "")
            if not path.is_file() or _text_hash(_read_text(path)) != chapter.get("sha256"):
                stale.append(chapter.get("name"))
        cached_paths = {str(chapter.get("path") or "") for chapter in manifest_chapters}
        stale.extend(
            path.parent.name
            for path in _chapter_sources(root)
            if str(path.relative_to(root)).replace("\\", "/") not in cached_paths
        )
        return {
            "available": True,
            "createdAt": manifest.get("createdAt"),
            "chapterCount": manifest.get("chapterCount"),
            "staleChapters": stale,
        }

    def _cached_predictions(
        self,
        file_path: str,
        catalog_lookup: Mapping[str, str],
    ) -> Optional[List[ModernBookNLPQuote]]:
        source = Path(file_path).resolve()
        if source.name != "chapter.txt":
            return None
        root = source.parent.parent
        cache_dir = root / CACHE_DIRECTORY
        manifest_path = cache_dir / CACHE_MANIFEST
        if not manifest_path.is_file():
            raise ValueError("ModernBookNLP book cache is missing; build it before parsing chapters")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative_path = str(source.relative_to(root)).replace("\\", "/")
        chapter = next(
            (row for row in manifest.get("chapters") or [] if row.get("path") == relative_path),
            None,
        )
        if chapter is None:
            raise ValueError("Chapter is not present in the ModernBookNLP book cache")
        text = _read_text(source)
        if _text_hash(text) != chapter.get("sha256"):
            raise ValueError("Chapter changed after the ModernBookNLP book cache was built")

        output_paths = [
            cache_dir / f"{CACHE_BOOK_ID}.{extension}"
            for extension in ("entities", "quotes", "tokens")
        ]
        file_signatures = tuple(
            (path.stat().st_mtime_ns, path.stat().st_size)
            for path in output_paths
        )
        predictions = _load_cached_book_predictions(
            str(cache_dir),
            file_signatures,
            tuple(sorted(catalog_lookup.items())),
        )
        chapter_start = int(chapter["start"])
        chapter_end = int(chapter["end"])
        return [
            ModernBookNLPQuote(
                start=prediction.start - chapter_start,
                end=prediction.end - chapter_start,
                speaker=prediction.speaker,
                coref_id=prediction.coref_id,
                nominal_mentions=prediction.nominal_mentions,
            )
            for prediction in predictions
            if prediction.start >= chapter_start and prediction.end <= chapter_end
        ]

    def attribute_file(
        self,
        file_path: str,
        character_catalog: Iterable[Mapping[str, object]],
    ) -> List[ModernBookNLPQuote]:
        catalog_lookup = build_catalog_lookup(character_catalog)
        if not catalog_lookup:
            raise ValueError("ModernBookNLP attribution requires a non-empty character catalog")

        cached = self._cached_predictions(file_path, catalog_lookup)
        if cached is not None:
            return cached

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
                collect_coref_nominal_mentions(entities),
            )