"""BookNLP-first parser adapter for OpenBook parse API."""

import os
import re
import tempfile
import builtins
from collections import Counter
from typing import Dict, List, Optional, Tuple

from .dialogue_parser_service import DialogueParserService, DialogueLine
from .knowledge_store import load_knowledge_for_file
from .attribution_passes import (
    LineSignals,
    detect_nearby_speech_verb,
    extract_explicit_mentions,
    extract_first_sentence,
    extract_last_sentence,
    infer_addressed_name,
    infer_sentence_attributed_speaker,
    quote_boundary_quality,
    rank_candidates,
)

BLOCKED_CHARACTER_TOKENS = {"he", "she", "as"}


def _normalize_text(value: str) -> str:
    text = (value or "").lower()
    text = (
        text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    text = re.sub(r"^[\s\"'`“”‘’]+|[\s\"'`“”‘’]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _read_tsv(path: str) -> List[List[str]]:
    rows: List[List[str]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            rows.append(line.split("\t"))
    return rows


def _build_coref_name_map(entities_path: str) -> Dict[str, str]:
    rows = _read_tsv(entities_path)
    if not rows:
        return {}
    start_idx = 1 if rows and rows[0] and rows[0][0].upper() == "COREF" else 0
    names: Dict[str, Counter] = {}
    for cols in rows[start_idx:]:
        if len(cols) < 6:
            continue
        coref = cols[0]
        prop = cols[3]
        cat = cols[4]
        text = cols[5].strip()
        if cat != "PER" or not text:
            continue
        if coref not in names:
            names[coref] = Counter()
        weight = 10.0 if prop == "PROP" else (1.0 if prop == "NOM" else 0.001)
        names[coref][text] += weight

    best: Dict[str, str] = {}
    for coref, counter in names.items():
        if counter:
            best[coref] = counter.most_common(1)[0][0]
    return best


def _canonicalize_name(raw_name: str, aliases: Dict[str, str], canonicals: Dict[str, str]) -> str:
    if not raw_name:
        return "narrator"
    name = re.sub(r"\s+", " ", raw_name.strip())
    lowered = name.lower()

    if lowered in aliases:
        return aliases[lowered]
    if lowered in canonicals:
        return canonicals[lowered]

    simplified = re.sub(r"[^a-z0-9\s'-]", "", lowered)
    if simplified in aliases:
        return aliases[simplified]
    if simplified in canonicals:
        return canonicals[simplified]

    return lowered


def _canonicalize_name_with_strength(
    raw_name: str,
    aliases: Dict[str, str],
    canonicals: Dict[str, str],
) -> Tuple[str, float]:
    if not raw_name:
        return "narrator", 0.0
    name = re.sub(r"\s+", " ", raw_name.strip())
    lowered = name.lower()

    if lowered in aliases:
        return aliases[lowered], 1.0
    if lowered in canonicals:
        return canonicals[lowered], 0.85

    simplified = re.sub(r"[^a-z0-9\s'-]", "", lowered)
    if simplified in aliases:
        return aliases[simplified], 0.7
    if simplified in canonicals:
        return canonicals[simplified], 0.55

    return lowered, 0.2


def _load_booknlp_quotes(
    quotes_path: str,
    coref_name_map: Dict[str, str],
    aliases: Dict[str, str],
    canonicals: Dict[str, str],
) -> List[Dict[str, str]]:
    rows = _read_tsv(quotes_path)
    if not rows:
        return []
    start_idx = 1 if rows and rows[0] and rows[0][0] == "quote_start" else 0
    out: List[Dict[str, str]] = []
    for cols in rows[start_idx:]:
        if len(cols) < 7:
            continue
        char_id = cols[5].strip()
        quote_text = cols[6].strip()
        raw_name = coref_name_map.get(char_id) or cols[4].strip() or "narrator"
        canonical, alias_strength = _canonicalize_name_with_strength(
            raw_name,
            aliases=aliases,
            canonicals=canonicals,
        )
        out.append(
            {
                "quote": quote_text,
                "quote_norm": _normalize_text(quote_text),
                "speaker": canonical,
                "alias_strength": alias_strength,
                "has_coref_name": bool(coref_name_map.get(char_id)),
            }
        )
    return out


def _unique_suggestions(*names: Optional[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


class BookNLPParserService:
    """BookNLP-first adapter that preserves legacy parse response shape."""

    def __init__(self) -> None:
        self.legacy_service = DialogueParserService()
        self.booknlp_cls = None
        self.booknlp = None
        self.model_unavailable_reason: Optional[str] = None

    def _ensure_booknlp(self) -> bool:
        if self.booknlp is not None:
            return True
        if self.model_unavailable_reason:
            return False
        try:
            from booknlp.booknlp import BookNLP  # pylint: disable=import-outside-toplevel

            self.booknlp_cls = BookNLP
            self.booknlp = self.booknlp_cls(
                "en",
                {
                    "pipeline": "entity,quote,coref",
                    "model": "small",
                },
            )
            return True
        except Exception as exc:  # pylint: disable=broad-except
            self.model_unavailable_reason = str(exc)
            return False

    def _run_booknlp(self, file_path: str) -> Tuple[str, str]:
        with tempfile.TemporaryDirectory(prefix="openbook_booknlp_") as temp_dir:
            chapter_out_dir = os.path.join(temp_dir, "chapter")
            os.makedirs(chapter_out_dir, exist_ok=True)
            book_id = "chapter"
            quotes_path = os.path.join(chapter_out_dir, f"{book_id}.quotes")
            entities_path = os.path.join(chapter_out_dir, f"{book_id}.entities")

            normalized_input = os.path.join(chapter_out_dir, f"{book_id}.input.utf8.txt")
            with open(file_path, "rb") as source_handle:
                raw_bytes = source_handle.read()
            try:
                decoded = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                decoded = raw_bytes.decode("cp1252", errors="replace")

            # BookNLP may read plain text files using the platform default encoding.
            # On Windows this is commonly cp1252, so write a Windows-compatible file
            # to avoid charmap decode errors during processing.
            input_encoding = "cp1252" if os.name == "nt" else "utf-8"
            with open(
                normalized_input,
                "w",
                encoding=input_encoding,
                errors="replace",
                newline="",
            ) as normalized_handle:
                normalized_handle.write(decoded)

            original_open = builtins.open

            def _utf8_default_open(file, mode="r", *args, **kwargs):
                if os.name == "nt" and "b" not in mode and "encoding" not in kwargs:
                    kwargs["encoding"] = "utf-8"
                    kwargs.setdefault("errors", "replace")
                return original_open(file, mode, *args, **kwargs)

            try:
                if os.name == "nt":
                    builtins.open = _utf8_default_open
                self.booknlp.process(normalized_input, chapter_out_dir, book_id)
            finally:
                if os.name == "nt":
                    builtins.open = original_open

            with open(quotes_path, "r", encoding="utf-8") as quote_handle:
                quotes_content = quote_handle.read()
            with open(entities_path, "r", encoding="utf-8") as entities_handle:
                entities_content = entities_handle.read()

        # Persist temporary outputs for parsing outside temp context
        tmp_quotes = tempfile.NamedTemporaryFile(
            mode="w", delete=False, encoding="utf-8", suffix=".quotes"
        )
        tmp_entities = tempfile.NamedTemporaryFile(
            mode="w", delete=False, encoding="utf-8", suffix=".entities"
        )
        try:
            tmp_quotes.write(quotes_content)
            tmp_entities.write(entities_content)
            tmp_quotes.close()
            tmp_entities.close()
            return tmp_quotes.name, tmp_entities.name
        except Exception:
            try:
                os.unlink(tmp_quotes.name)
            except OSError:
                pass
            try:
                os.unlink(tmp_entities.name)
            except OSError:
                pass
            raise

    def parse_file(
        self,
        file_path: str,
        options: Optional[dict] = None,
        manual_blocklist: Optional[List[str]] = None,
        source_path: Optional[str] = None,
    ) -> Tuple[List[DialogueLine], List[str], Dict[str, object]]:
        """Parse a chapter file with BookNLP-first attribution and legacy fallback."""
        normalized_manual_blocklist = {
            _normalize_text(name)
            for name in (manual_blocklist or [])
            if _normalize_text(name)
        }
        effective_blocklist = set(BLOCKED_CHARACTER_TOKENS)
        effective_blocklist.update(normalized_manual_blocklist)

        self.legacy_service.blocked_speakers.update(effective_blocklist)

        legacy_input_path = (
            source_path
            if (source_path and os.path.exists(source_path))
            else file_path
        )
        base_lines, _ = self.legacy_service.parse_file(legacy_input_path, options=options or {})

        if not self._ensure_booknlp():
            names = sorted(
                list(
                    set(
                        line.speaker
                        for line in base_lines
                        if _normalize_text(line.speaker) not in effective_blocklist
                    )
                )
            )
            return base_lines, names, {
                "backend": "legacy",
                "fallback_reason": self.model_unavailable_reason or "booknlp_unavailable",
            }

        quotes_path = ""
        entities_path = ""
        try:
            quotes_path, entities_path = self._run_booknlp(file_path)

            knowledge_store = load_knowledge_for_file(source_path or file_path)
            aliases: Dict[str, str] = {}
            canonicals: Dict[str, str] = {}
            display_name_by_key: Dict[str, str] = {}

            if knowledge_store:
                for alias, canonical in knowledge_store.aliases.items():
                    aliases[_normalize_text(alias)] = _normalize_text(canonical)
                for canonical in knowledge_store.genders.keys():
                    key = _normalize_text(canonical)
                    canonicals[key] = key
                    display_name_by_key[key] = canonical

            # Fallback display names from legacy parser output
            for line in base_lines:
                if line.speaker:
                    key = _normalize_text(line.speaker)
                    if key and key not in display_name_by_key:
                        display_name_by_key[key] = line.speaker

            known_names = sorted(set(display_name_by_key.values()))

            coref_name_map = _build_coref_name_map(entities_path)
            quotes = _load_booknlp_quotes(
                quotes_path=quotes_path,
                coref_name_map=coref_name_map,
                aliases=aliases,
                canonicals=canonicals,
            )

            with open(file_path, "rb") as source_handle:
                raw_bytes = source_handle.read()
            try:
                original_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                original_text = raw_bytes.decode("cp1252", errors="replace")

            qpos = 0
            aligned = 0
            exact_aligned = 0
            fallback_aligned = 0
            agreement_hits = 0
            uncertain_lines = 0

            next_legacy_by_line: Dict[int, Optional[str]] = {}
            next_dialogue_legacy: Optional[str] = None
            for idx in range(len(base_lines) - 1, -1, -1):
                base_line = base_lines[idx]
                if base_line.line_type != "dialogue":
                    continue
                next_legacy_by_line[idx] = next_dialogue_legacy
                current = str(base_line.speaker or "").strip()
                if current:
                    next_dialogue_legacy = current

            for line_idx, line in enumerate(base_lines):
                if line.line_type != "dialogue":
                    continue

                legacy_name = str(line.speaker or "").strip()
                legacy_key = _normalize_text(legacy_name)
                line_norm = _normalize_text(line.text)
                chosen = None
                used_fallback = False

                lookahead = min(qpos + 8, len(quotes))
                for idx in range(qpos, lookahead):
                    if quotes[idx]["quote_norm"] == line_norm and line_norm:
                        chosen = idx
                        break

                if chosen is None and qpos < len(quotes):
                    chosen = qpos
                    used_fallback = True

                if chosen is None:
                    continue

                predicted_key = _normalize_text(quotes[chosen]["speaker"])
                if not predicted_key:
                    continue

                predicted_name = display_name_by_key.get(
                    predicted_key,
                    quotes[chosen]["speaker"].title(),
                )
                if _normalize_text(predicted_name) in effective_blocklist:
                    continue

                if used_fallback:
                    fallback_aligned += 1
                else:
                    if legacy_key and legacy_key == predicted_key:
                        agreement_hits += 1
                    exact_aligned += 1

                raw_context = ""
                pre_quote_context = ""
                post_quote_context = ""
                if isinstance(line.span_start, int) and isinstance(line.span_end, int):
                    left = max(0, line.span_start - 120)
                    right = min(len(original_text), line.span_end + 120)
                    raw_context = original_text[left:right]
                    pre_left = max(0, line.span_start - 240)
                    pre_quote_context = original_text[pre_left:line.span_start]
                    post_right = min(len(original_text), line.span_end + 280)
                    post_quote_context = original_text[line.span_end:post_right]

                last_sentence = extract_last_sentence(pre_quote_context)
                next_sentence = extract_first_sentence(post_quote_context)
                addressed_name = infer_addressed_name(last_sentence, known_names)
                explicit_mentions = extract_explicit_mentions(line.text, known_names)
                next_sentence_speaker = infer_sentence_attributed_speaker(next_sentence, known_names)
                next_legacy = next_legacy_by_line.get(line_idx)

                dialogue_candidates = _unique_suggestions(predicted_name, legacy_name)
                signals = LineSignals(
                    predicted_name=predicted_name,
                    legacy_name=legacy_name,
                    prev_resolved=None,
                    next_resolved=None,
                    used_fallback_alignment=used_fallback,
                    exact_quote_match=not used_fallback,
                    quote_boundary_quality=quote_boundary_quality(raw_context, line.text),
                    nearby_speech_verb=detect_nearby_speech_verb(raw_context),
                    has_coref_name=bool(quotes[chosen].get("has_coref_name")),
                    alias_match_strength=float(quotes[chosen].get("alias_strength") or 0.0),
                    addressed_name_prev_sentence=addressed_name,
                    explicit_quote_mentions=explicit_mentions,
                    next_sentence_attributed_speaker=next_sentence_speaker,
                    line_ends_with_question=str(line.text or "").strip().endswith("?"),
                    next_dialogue_legacy=next_legacy,
                )
                decision = rank_candidates(dialogue_candidates, signals)
                line.speaker = decision.chosen_name
                line.is_suggestion = decision.is_suggestion
                line.suggestions = [entry.name for entry in decision.ranked]
                if decision.is_suggestion:
                    uncertain_lines += 1

                aligned += 1
                qpos = chosen + 1

            # Context-consistency pass:
            # For uncertain lines, prefer a candidate that matches both neighboring
            # high-confidence dialogue speakers when available.
            dialogue_indices = [
                idx for idx, value in enumerate(base_lines)
                if value.line_type == "dialogue"
            ]

            for idx_pos, line_idx in enumerate(dialogue_indices):
                line = base_lines[line_idx]
                if not line.is_suggestion:
                    continue
                if not line.suggestions or len(line.suggestions) < 2:
                    continue

                previous_speaker = None
                next_speaker = None

                for prev_pos in range(idx_pos - 1, -1, -1):
                    prev_line = base_lines[dialogue_indices[prev_pos]]
                    if prev_line.line_type == "dialogue" and not prev_line.is_suggestion:
                        previous_speaker = str(prev_line.speaker or "").strip()
                        break

                for next_pos in range(idx_pos + 1, len(dialogue_indices)):
                    next_line = base_lines[dialogue_indices[next_pos]]
                    if next_line.line_type == "dialogue" and not next_line.is_suggestion:
                        next_speaker = str(next_line.speaker or "").strip()
                        break

                if not previous_speaker or not next_speaker:
                    continue
                if _normalize_text(previous_speaker) != _normalize_text(next_speaker):
                    continue

                for candidate in line.suggestions:
                    if _normalize_text(candidate) == _normalize_text(previous_speaker):
                        reordered = _unique_suggestions(candidate, *line.suggestions)
                        line.speaker = reordered[0]
                        line.suggestions = reordered
                        break

            names = sorted(
                list(
                    set(
                        line.speaker
                        for line in base_lines
                        if _normalize_text(line.speaker) not in effective_blocklist
                    )
                )
            )
            return base_lines, names, {
                "backend": "booknlp",
                "quotes": len(quotes),
                "aligned_quotes": aligned,
                "exact_aligned_quotes": exact_aligned,
                "fallback_aligned_quotes": fallback_aligned,
                "agreement_hits": agreement_hits,
                "uncertain_lines": uncertain_lines,
            }
        except Exception as exc:  # pylint: disable=broad-except
            names = sorted(
                list(
                    set(
                        line.speaker
                        for line in base_lines
                        if _normalize_text(line.speaker) not in effective_blocklist
                    )
                )
            )
            return base_lines, names, {
                "backend": "legacy",
                "fallback_reason": f"booknlp_error: {exc}",
            }
        finally:
            if quotes_path and os.path.exists(quotes_path):
                try:
                    os.unlink(quotes_path)
                except OSError:
                    pass
            if entities_path and os.path.exists(entities_path):
                try:
                    os.unlink(entities_path)
                except OSError:
                    pass
