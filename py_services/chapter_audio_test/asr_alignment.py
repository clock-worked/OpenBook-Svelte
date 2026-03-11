from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from .text_utils import to_float_or_none, tokenize


def collect_asr_word_tokens(result: dict[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    segments = result.get("segments", [])
    if not isinstance(segments, list):
        return words

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_words = segment.get("words", [])
        if not isinstance(segment_words, list):
            continue

        for word_data in segment_words:
            if not isinstance(word_data, dict):
                continue
            word_text = str(word_data.get("word", "")).strip()
            normalized_tokens = tokenize(word_text)
            if not normalized_tokens:
                continue

            start = to_float_or_none(word_data.get("start"))
            end = to_float_or_none(word_data.get("end"))

            if len(normalized_tokens) == 1:
                words.append(
                    {
                        "token": normalized_tokens[0],
                        "word": word_text,
                        "start": start,
                        "end": end,
                    }
                )
                continue

            if start is not None and end is not None and end >= start:
                duration = end - start
                step = duration / float(len(normalized_tokens))
                for index, token in enumerate(normalized_tokens):
                    token_start = round(start + (index * step), 4)
                    token_end = round(start + ((index + 1) * step), 4)
                    words.append(
                        {
                            "token": token,
                            "word": word_text,
                            "start": token_start,
                            "end": token_end,
                        }
                    )
            else:
                for token in normalized_tokens:
                    words.append(
                        {
                            "token": token,
                            "word": word_text,
                            "start": start,
                            "end": end,
                        }
                    )

    return words


def map_expected_tokens_to_asr_indices(
    expected_tokens: list[str],
    asr_tokens: list[str],
) -> tuple[list[int | None], float]:
    mapping: list[int | None] = [None] * len(expected_tokens)
    matcher = SequenceMatcher(None, expected_tokens,
                              asr_tokens, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_count = i2 - i1
        right_count = j2 - j1

        if left_count <= 0:
            continue

        if tag == "equal":
            for offset in range(left_count):
                mapping[i1 + offset] = j1 + offset
            continue

        if right_count <= 0:
            continue

        for offset in range(left_count):
            relative = (offset + 0.5) / float(left_count)
            target = int(round(j1 + (relative * right_count) - 0.5))
            target = max(j1, min(j2 - 1, target))
            mapping[i1 + offset] = target

    return mapping, round(matcher.ratio(), 4)


def nearest_start_time(words: list[dict[str, Any]], index: int) -> float | None:
    if not words:
        return None

    clamped = max(0, min(index, len(words) - 1))
    for idx in range(clamped, len(words)):
        value = to_float_or_none(words[idx].get("start"))
        if value is not None:
            return value

    for idx in range(clamped, -1, -1):
        value = to_float_or_none(words[idx].get("end"))
        if value is not None:
            return value

    return None


def nearest_end_time(words: list[dict[str, Any]], index: int) -> float | None:
    if not words:
        return None

    clamped = max(0, min(index, len(words) - 1))
    for idx in range(clamped, -1, -1):
        value = to_float_or_none(words[idx].get("end"))
        if value is not None:
            return value

    for idx in range(clamped, len(words)):
        value = to_float_or_none(words[idx].get("start"))
        if value is not None:
            return value

    return None


def build_line_timestamps(
    lines: list[dict[str, Any]],
    asr_words: list[dict[str, Any]],
    audio_duration_sec: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected_tokens: list[str] = []
    line_token_ranges: list[tuple[int, int]] = []

    for line in lines:
        tokens = tokenize(line["text"])
        start_idx = len(expected_tokens)
        expected_tokens.extend(tokens)
        end_idx = len(expected_tokens)
        line_token_ranges.append((start_idx, end_idx))

    asr_tokens = [str(item.get("token", ""))
                  for item in asr_words if item.get("token")]
    mapping, ratio = map_expected_tokens_to_asr_indices(
        expected_tokens, asr_tokens)

    resolved_lines: list[dict[str, Any]] = []
    fallback_index = 0
    previous_end = 0.0

    for line, (start_idx, end_idx) in zip(lines, line_token_ranges):
        mapped_indices = [
            mapping[token_index]
            for token_index in range(start_idx, end_idx)
            if token_index < len(mapping) and mapping[token_index] is not None
        ]

        matched_tokens = len(mapped_indices)
        line_token_count = max(0, end_idx - start_idx)

        if mapped_indices:
            start_word_index = min(mapped_indices)
            end_word_index = max(mapped_indices)
            fallback_index = end_word_index
        else:
            start_word_index = fallback_index
            end_word_index = fallback_index

        start_sec = nearest_start_time(asr_words, start_word_index)
        end_sec = nearest_end_time(asr_words, end_word_index)

        if start_sec is None:
            start_sec = previous_end
        if end_sec is None:
            end_sec = start_sec

        start_sec = max(previous_end, min(start_sec, audio_duration_sec))
        end_sec = max(start_sec, min(end_sec, audio_duration_sec))

        resolved_line = {
            **line,
            "startSec": round(start_sec, 4),
            "endSec": round(end_sec, 4),
            "durationSec": round(end_sec - start_sec, 4),
            "tokenCount": line_token_count,
            "matchedTokenCount": matched_tokens,
            "startWordIndex": int(start_word_index),
            "endWordIndex": int(end_word_index),
        }
        resolved_lines.append(resolved_line)
        previous_end = end_sec

    if resolved_lines:
        last = resolved_lines[-1]
        if last["endSec"] < audio_duration_sec:
            last["endSec"] = round(audio_duration_sec, 4)
            last["durationSec"] = round(last["endSec"] - last["startSec"], 4)

    for index in range(1, len(resolved_lines)):
        current = resolved_lines[index]
        previous = resolved_lines[index - 1]
        if current["startSec"] < previous["endSec"]:
            current["startSec"] = previous["endSec"]
        if current["endSec"] < current["startSec"]:
            current["endSec"] = current["startSec"]
        current["durationSec"] = round(
            current["endSec"] - current["startSec"], 4)

    total_expected = len(expected_tokens)
    total_matched = sum(item["matchedTokenCount"] for item in resolved_lines)
    alignment_report = {
        "sequenceMatcherRatio": ratio,
        "expectedTokenCount": total_expected,
        "asrTokenCount": len(asr_tokens),
        "matchedTokenCount": total_matched,
        "matchedTokenRatio": round((total_matched / total_expected), 4) if total_expected else 0.0,
    }

    return resolved_lines, alignment_report
