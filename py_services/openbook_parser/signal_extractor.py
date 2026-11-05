"""Signal extraction for dialogue attribution.

Extracts the per-line signal computation from the parser service into a
standalone, testable module. The service calls :func:`extract_line_signals`
once per dialogue line; all the "how" of signal inference lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .attribution_passes import (
    LineSignals,
    extract_explicit_mentions,
    extract_first_sentence,
    extract_last_sentence,
    infer_addressed_name,
    infer_post_quote_named_mention,
    infer_previous_paragraph_named_mention,
    infer_pronoun_attributed_gender,
    infer_recent_named_mention,
    infer_sentence_attributed_speaker,
    quote_boundary_quality,
)
from .booknlp_parser_service import infer_unique_descriptor_speaker


@dataclass
class ExtractionContext:
    """All inputs needed to compute attribution signals for one dialogue line.

    The dicts (``*_by_line``, ``*_by_paragraph``) are pre-computed once per
    chapter before the main loop; they are stable inputs here.
    """

    line: object  # DialogueLine
    line_idx: int
    base_lines: list
    line_paragraph_index: dict
    paragraph_ranges: list
    original_text: str
    known_names: list
    known_gender_by_key: dict
    next_legacy_by_line: dict
    next_dialogue_by_line: dict
    previous_dialogue_by_line: dict
    resolved_dialogue_by_paragraph: dict
    is_returning_by_line: dict
    closed_world_catalog: dict
    closed_world: bool
    user_confirmed_speakers_by_paragraph: dict = None


@dataclass
class ExtractionResult:
    """Output of signal extraction.

    ``signals`` is the ready-to-score :class:`LineSignals`.
    ``pre_quote_context`` / ``post_quote_context`` / ``raw_context`` are
    returned because the service needs them for candidate-pool construction
    and the decision trace.
    """

    signals: LineSignals
    raw_context: str
    pre_quote_context: str
    post_quote_context: str


def extract_line_signals(ctx: ExtractionContext) -> ExtractionResult:
    """Compute all attribution signals for one dialogue line.

    This is the single entry point: given the line and its chapter-level
    context, produce a fully-populated :class:`LineSignals`.
    """
    line = ctx.line
    line_idx = ctx.line_idx

    paragraph_index = ctx.line_paragraph_index.get(line_idx, -1)
    paragraph_range = (
        ctx.paragraph_ranges[paragraph_index]
        if 0 <= paragraph_index < len(ctx.paragraph_ranges)
        else None
    )

    # --- Context windowing ---
    raw_context = ""
    pre_quote_context = ""
    post_quote_context = ""
    next_dialogue_tail = ""
    if isinstance(line.span_start, int) and isinstance(line.span_end, int):
        if paragraph_range:
            left = max(paragraph_range.start, line.span_start - 120)
            right = min(paragraph_range.end, line.span_end + 120)
            pre_left = paragraph_range.start
            post_right = paragraph_range.end
            previous_dialogue_index = ctx.previous_dialogue_by_line.get(line_idx)
            if previous_dialogue_index is not None:
                previous_dialogue = ctx.base_lines[previous_dialogue_index]
                if isinstance(previous_dialogue.span_end, int):
                    pre_left = max(pre_left, previous_dialogue.span_end)
            next_dialogue_index = ctx.next_dialogue_by_line.get(line_idx)
            if (
                next_dialogue_index is not None
                and ctx.line_paragraph_index.get(next_dialogue_index) == paragraph_index
            ):
                next_dialogue = ctx.base_lines[next_dialogue_index]
                if isinstance(next_dialogue.span_start, int):
                    post_right = min(post_right, next_dialogue.span_start)
                if isinstance(next_dialogue.span_end, int):
                    next_dialogue_tail = " " + ctx.original_text[
                        next_dialogue.span_end : next_dialogue.span_end + 80
                    ]
        else:
            left = max(0, line.span_start - 120)
            right = min(len(ctx.original_text), line.span_end + 120)
            pre_left = max(0, line.span_start - 240)
            post_right = min(len(ctx.original_text), line.span_end + 280)
        raw_context = ctx.original_text[left:right]
        pre_quote_context = ctx.original_text[pre_left : line.span_start]
        post_quote_context = (
            ctx.original_text[line.span_end : post_right] + next_dialogue_tail
        )

    # --- Sentence-level signals ---
    last_sentence = extract_last_sentence(pre_quote_context)
    next_sentence = extract_first_sentence(post_quote_context)
    addressed_name = infer_addressed_name(last_sentence, ctx.known_names)
    explicit_mentions = extract_explicit_mentions(line.text, ctx.known_names)
    next_sentence_speaker = infer_sentence_attributed_speaker(
        next_sentence, ctx.known_names
    )
    post_quote_named_mention = infer_post_quote_named_mention(
        post_quote_context, ctx.known_names
    )
    descriptor_speaker = (
        infer_unique_descriptor_speaker(
            f"{pre_quote_context[-160:]} {next_sentence}",
            ctx.closed_world_catalog,
        )
        if ctx.closed_world
        else None
    )
    recent_named_mention = infer_recent_named_mention(
        pre_quote_context, ctx.known_names
    )
    previous_paragraph_named_mention = None

    # --- Gender inference ---
    context_gender = None
    context_gender_cue = None
    for sentence in (next_sentence, last_sentence):
        context_gender, context_gender_cue = infer_pronoun_attributed_gender(sentence)
        if context_gender:
            break

    # --- Previous-paragraph fallback ---
    if (
        next_sentence_speaker is None
        and recent_named_mention is None
        and previous_paragraph_named_mention is None
        and paragraph_index > 0
    ):
        previous_paragraph_range = ctx.paragraph_ranges[paragraph_index - 1]
        previous_paragraph_text = ctx.original_text[
            previous_paragraph_range.start : previous_paragraph_range.end
        ]
        previous_paragraph_named_mention = infer_previous_paragraph_named_mention(
            previous_paragraph_text, ctx.known_names
        )

    # --- Continuity signals ---
    next_legacy = ctx.next_legacy_by_line.get(line_idx)
    prev_resolved = (
        ctx.resolved_dialogue_by_paragraph.get(paragraph_index)
        if paragraph_index >= 0
        else None
    )
    continues_paragraph_dialogue = bool(
        ctx.previous_dialogue_by_line.get(line_idx) is not None and prev_resolved
    )

    # --- Paragraph sibling (user-confirmed) signal ---
    paragraph_sibling_speaker = None
    if paragraph_index >= 0 and ctx.user_confirmed_speakers_by_paragraph:
        sibling_speakers = ctx.user_confirmed_speakers_by_paragraph.get(paragraph_index)
        if sibling_speakers:
            # Pick the most common user-confirmed speaker in this paragraph
            paragraph_sibling_speaker = max(
                sibling_speakers, key=lambda s: sibling_speakers.count(s)
            )

    # --- Assemble LineSignals ---
    legacy_name = str(line.speaker or "").strip()
    signals = LineSignals(
        predicted_name=legacy_name,
        legacy_name=legacy_name,
        prev_resolved=prev_resolved,
        next_resolved=ctx.next_legacy_by_line.get(line_idx),
        used_fallback_alignment=False,
        exact_quote_match=True,
        quote_boundary_quality=quote_boundary_quality(raw_context, line.text),
        nearby_speech_verb=_detect_nearby_speech_verb(raw_context),
        has_coref_name=False,
        alias_match_strength=0.0,
        addressed_name_prev_sentence=addressed_name,
        explicit_quote_mentions=explicit_mentions,
        next_sentence_attributed_speaker=next_sentence_speaker,
        post_quote_named_mention=post_quote_named_mention,
        recent_named_mention_before_quote=recent_named_mention,
        previous_paragraph_named_mention=previous_paragraph_named_mention,
        candidate_genders={},  # filled in by the service (needs final candidate list)
        context_gender=context_gender,
        context_gender_cue=context_gender_cue,
        is_returning=ctx.is_returning_by_line.get(line_idx, False),
        continues_paragraph_dialogue=continues_paragraph_dialogue,
        line_ends_with_question=str(line.text or "").strip().endswith("?"),
        next_dialogue_legacy=next_legacy,
        descriptor_match=descriptor_speaker,
        paragraph_sibling_speaker=paragraph_sibling_speaker,
    )

    return ExtractionResult(
        signals=signals,
        raw_context=raw_context,
        pre_quote_context=pre_quote_context,
        post_quote_context=post_quote_context,
    )


def _detect_nearby_speech_verb(context: str) -> bool:
    """Thin wrapper to avoid importing the helper at module level."""
    from .booknlp_parser_service import detect_nearby_speech_verb

    return detect_nearby_speech_verb(context)
