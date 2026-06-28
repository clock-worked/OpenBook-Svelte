from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import soundfile as sf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from audio_generation_service import AudioGenerationService
from chapter_audio_test.asr_alignment import (
    build_line_timestamps,
    collect_asr_word_tokens,
)
from chapter_audio_test.audio_ops import (
    load_audio_float_2d,
    refine_line_boundaries_with_silence,
    write_audio_excerpt,
)
from chapter_audio_test.generation import (
    build_line_chunk_generation_plan,
    generate_line_chunked_chapter_audio,
)
from chapter_audio_test.validation import (
    attach_validation_summary_to_split_lines,
    attempt_boundary_shift,
    build_split_validation_payload,
    create_split_validation_transcriber,
    release_accelerator_cache,
    summarize_validation_reports,
    validate_split_lines,
)
from chapter_audio_test.text_utils import normalize_chapter_text, utc_now_iso


DEFAULT_MIN_CHUNK_CHARS = 800
DEFAULT_LINE_CHUNK_JOIN_MS = 0

WHISPERX_DEVICE = "cuda"
WHISPERX_COMPUTE_TYPE = "float16"
WHISPERX_BATCH_SIZE = 0
WHISPERX_VAD_METHOD = "silero"
WHISPERX_QUIET = True

BOUNDARY_FRAME_MS = 10
BOUNDARY_WINDOW_MS = 360
BOUNDARY_MIN_SILENCE_MS = 80
BOUNDARY_SILENCE_ENTER_DB = -42.0
BOUNDARY_SILENCE_EXIT_DB = -35.0
BOUNDARY_LINE_END_PAD_MS = 140
BOUNDARY_NEXT_START_BACKPAD_MS = 45
BOUNDARY_MIN_LINE_MS = 90

SPLIT_VALIDATION_THRESHOLD = 0.82
SPLIT_VALIDATION_NEIGHBOR_DELTA = 0.08
SPLIT_VALIDATION_MIN_IMPROVEMENT = 0.04
SPLIT_VALIDATION_ALTERNATES_PER_SIDE = 1


_WINDOWS_DLL_HANDLES: list[Any] = []


def _ensure_windows_cudnn_runtime() -> None:
    if os.name != "nt":
        return

    path_env = os.environ.get("PATH", "")
    existing_paths = {
        os.path.normcase(os.path.normpath(path))
        for path in path_env.split(os.pathsep)
        if path
    }

    candidate_dirs = [
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
        Path(sys.base_prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
        Path(__file__).resolve().parents[2]
        / ".venv"
        / "Lib"
        / "site-packages"
        / "nvidia"
        / "cudnn"
        / "bin",
    ]

    for candidate in candidate_dirs:
        if not candidate.exists() or not candidate.is_dir():
            continue

        candidate_str = str(candidate)
        normalized_candidate = os.path.normcase(os.path.normpath(candidate_str))
        if normalized_candidate in existing_paths:
            continue

        if hasattr(os, "add_dll_directory"):
            try:
                _WINDOWS_DLL_HANDLES.append(os.add_dll_directory(candidate_str))
            except (FileNotFoundError, OSError):
                pass

        os.environ["PATH"] = candidate_str + os.pathsep + os.environ.get("PATH", "")
        existing_paths.add(normalized_candidate)


class VibeVoiceLineRequest(BaseModel):
    line_id: int
    text: str
    character_name: str
    character_id: str
    voice_id: str
    provider: str = "vibevoice_local"
    chapter_title: str
    source_file: str
    voice_sample_root: Optional[str] = None
    audio_root: Optional[str] = None


class VibeVoiceLineInput(BaseModel):
    id: int
    text: str
    characterId: Optional[str] = None
    characterName: Optional[str] = None
    chosenSpeaker: Optional[str] = None
    chapterTitle: Optional[str] = None
    sourceFile: Optional[str] = None


class VibeVoiceCharacterRequest(BaseModel):
    character_name: str
    character_id: str
    lines: List[VibeVoiceLineInput]
    voice_id: str
    provider: str = "vibevoice_local"
    chapter_title: str
    source_file: str
    use_filler_for_short_batch: bool = False
    filler_text: Optional[str] = None
    replace_line_final_commas_with_periods: bool = True
    replace_numbers_with_words: bool = False
    voice_sample_root: Optional[str] = None
    audio_root: Optional[str] = None


class VibeVoiceAssignment(BaseModel):
    character_id: str
    character_name: str
    voice_id: str
    provider: str = "vibevoice_local"


class VibeVoiceChapterRequest(BaseModel):
    dialogue_path: str
    assignments: List[VibeVoiceAssignment]
    chapter_title: Optional[str] = None
    source_file: Optional[str] = None
    voice_sample_root: Optional[str] = None
    audio_root: Optional[str] = None


def _is_vibevoice_provider(provider: str) -> bool:
    return "vibevoice" in str(provider or "").strip().lower()


QUOTE_ENDS_WITH_COMMA_RE = re.compile(r",(?=(?:['\"])(?:\s|$))|,(?=\s*$)")
NUMBER_TOKEN_RE = re.compile(r"(?<![\w.])\d[\d,]*(?![\w.])")

ONES = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}

TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}


def _int_to_words(value: int) -> str:
    if value < 0:
        raise ValueError("Negative values are not supported.")
    if value < 20:
        return ONES[value]
    if value < 100:
        tens_value = (value // 10) * 10
        remainder = value % 10
        if remainder == 0:
            return TENS[tens_value]
        return f"{TENS[tens_value]} {ONES[remainder]}"
    if value < 1000:
        hundreds = value // 100
        remainder = value % 100
        if remainder == 0:
            return f"{ONES[hundreds]} hundred"
        return f"{ONES[hundreds]} hundred and {_int_to_words(remainder)}"
    if value < 10000:
        thousands = value // 1000
        remainder = value % 1000
        if remainder == 0:
            return f"{ONES[thousands]} thousand"
        connector = " and " if remainder < 100 else " "
        return f"{ONES[thousands]} thousand{connector}{_int_to_words(remainder)}"
    raise ValueError(f"Unsupported number: {value}")


def _replace_numbers_with_words(text: str) -> str:
    def replace_match(match: re.Match[str]) -> str:
        raw_value = match.group(0)
        collapsed = raw_value.replace(",", "")
        if not collapsed.isdigit():
            return raw_value

        try:
            return _int_to_words(int(collapsed))
        except ValueError:
            return raw_value

    return NUMBER_TOKEN_RE.sub(replace_match, text)


def _normalize_generation_text(
    text: str,
    *,
    replace_line_final_commas_with_periods: bool,
    replace_numbers_with_words: bool,
) -> str:
    normalized_text = normalize_chapter_text(text)
    if replace_line_final_commas_with_periods:
        normalized_text = QUOTE_ENDS_WITH_COMMA_RE.sub(".", normalized_text)
    if replace_numbers_with_words:
        normalized_text = _replace_numbers_with_words(normalized_text)
    return normalize_chapter_text(normalized_text)


def _normalize_character_lines(
    lines: list[VibeVoiceLineInput],
    *,
    character_id: str,
    character_name: str,
    fallback_chapter_title: str,
    fallback_source_file: str,
    replace_line_final_commas_with_periods: bool,
    replace_numbers_with_words: bool,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in lines:
        source_text = normalize_chapter_text(str(item.text or ""))
        if not source_text:
            continue

        text = _normalize_generation_text(
            source_text,
            replace_line_final_commas_with_periods=replace_line_final_commas_with_periods,
            replace_numbers_with_words=replace_numbers_with_words,
        )
        if not text:
            continue

        line_character_id = str(item.characterId or character_id or "").strip()
        if not line_character_id:
            line_character_id = str(character_id or "narrator").strip() or "narrator"

        line_character_name = str(
            item.characterName
            or item.chosenSpeaker
            or character_name
            or ""
        ).strip()
        if not line_character_name:
            line_character_name = str(character_name or "narrator").strip() or "narrator"

        chapter_title = str(item.chapterTitle or fallback_chapter_title or "").strip()
        if not chapter_title:
            chapter_title = str(fallback_chapter_title or "").strip()
        if not chapter_title:
            chapter_title = "unknown"

        source_file = str(item.sourceFile or fallback_source_file or "")
        if not source_file:
            source_file = str(fallback_source_file or fallback_chapter_title or "")

        normalized.append(
            {
                "id": int(item.id),
                "characterId": line_character_id,
                "characterFolder": line_character_name,
                "text": text,
                "sourceText": source_text,
                "chapterTitle": chapter_title,
                "sourceFile": source_file,
            }
        )

    return normalized


def _build_character_generation_text(
    *,
    lines: list[dict[str, Any]],
    min_chunk_chars: int,
    use_filler_for_short_batch: bool,
    filler_text: Optional[str],
) -> dict[str, Any]:
    line_texts = [
        str(line.get("text") or "").strip()
        for line in lines
        if str(line.get("text") or "").strip()
    ]
    input_characters = len(" ".join(line_texts))

    normalized_filler = normalize_chapter_text(str(filler_text or ""))
    filler_segments: list[str] = []
    filler_characters = 0

    if use_filler_for_short_batch and normalized_filler and input_characters < min_chunk_chars:
        while input_characters + filler_characters < min_chunk_chars:
            filler_segments.append(normalized_filler)
            filler_characters = len(" ".join(filler_segments))

    effective_lines = filler_segments + line_texts if filler_segments else line_texts

    return {
        "chapterText": "\n".join(effective_lines),
        "inputCharacters": input_characters,
        "fillerCharacters": filler_characters,
        "usedFiller": filler_characters > 0,
        "fillerAvailable": bool(normalized_filler),
    }


def _transcribe_with_whisperx_alignment(
    *,
    audio_path: Path,
    device: str,
    compute_type: str,
    batch_size: int,
    vad_method: str,
    quiet: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _ensure_windows_cudnn_runtime()

    try:
        import torch  # noqa: WPS433

        whisperx = importlib.import_module("whisperx")
    except ImportError as exc:
        raise RuntimeError(
            "WhisperX is not installed. Install it with: pip install whisperx"
        ) from exc

    selected_device = device
    if selected_device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "WhisperX is configured for CUDA but CUDA is not available in this environment."
        )

    if selected_device not in {"cuda", "cpu"}:
        raise RuntimeError(
            f"Unsupported WhisperX device '{selected_device}'. Expected 'cuda' or 'cpu'."
        )

    selected_batch_size = batch_size if batch_size > 0 else (32 if selected_device == "cuda" else 8)

    if quiet:
        warnings.simplefilter("ignore")
        os.environ.setdefault("PYTHONWARNINGS", "ignore")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        logging.getLogger().setLevel(logging.ERROR)
        logging.getLogger("whisperx").setLevel(logging.ERROR)
        logging.getLogger("pyannote").setLevel(logging.ERROR)
        logging.getLogger("faster_whisper").setLevel(logging.ERROR)
        logging.getLogger("torch").setLevel(logging.ERROR)

    model = whisperx.load_model(
        "small.en",
        device=selected_device,
        compute_type=compute_type,
        vad_method=vad_method,
    )

    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, batch_size=selected_batch_size)

    language_code = result.get("language")
    if not isinstance(language_code, str) or not language_code:
        raise RuntimeError("WhisperX transcription did not return a language code for alignment.")

    align_model, metadata = whisperx.load_align_model(
        language_code=language_code,
        device=selected_device,
    )

    aligned = whisperx.align(
        result.get("segments", []),
        align_model,
        metadata,
        audio,
        selected_device,
        return_char_alignments=False,
    )

    aligned["text"] = " ".join(
        str(segment.get("text", "")).strip()
        for segment in aligned.get("segments", [])
        if isinstance(segment, dict)
    ).strip()

    engine_report = {
        "engine": "whisperx",
        "model": "small.en",
        "device": selected_device,
        "computeType": compute_type,
        "batchSize": selected_batch_size,
        "vadMethod": vad_method,
        "alignment": True,
    }

    return aligned, engine_report


def _write_character_split_clips(
    *,
    lines: list[dict[str, Any]],
    full_audio: Any,
    sample_rate: int,
    audio_root: Path,
    default_chapter_title: str,
    default_source_file: str,
) -> list[dict[str, Any]]:
    written: list[dict[str, Any]] = []

    for line in lines:
        line_id = int(line["id"])
        chapter_title = str(line.get("chapterTitle") or default_chapter_title or "").strip()
        if not chapter_title:
            chapter_title = str(default_chapter_title or "unknown").strip() or "unknown"

        source_file = str(line.get("sourceFile") or default_source_file or "").strip()
        if not source_file:
            source_file = str(default_source_file or default_chapter_title or "").strip()

        line_character_name = str(line.get("characterFolder") or "").strip()
        if not line_character_name:
            line_character_name = "narrator"

        character_dir = audio_root / chapter_title / "audio_lines" / line_character_name
        character_dir.mkdir(parents=True, exist_ok=True)

        file_name = f"{line_id}-{line_character_name}.wav"
        file_path = character_dir / file_name

        write_audio_excerpt(
            audio=full_audio,
            sample_rate=sample_rate,
            start_sec=float(line["startSec"]),
            end_sec=float(line["endSec"]),
            output_path=file_path,
        )

        written.append(
            {
                **line,
                "audioFile": file_name,
                "audioPath": str(file_path),
                "chapterTitle": chapter_title,
                "sourceFile": source_file,
            }
        )

    return written


def _load_or_create_manifest(
    *,
    manifest_path: Path,
    character_id: str,
    character_name: str,
    voice_id: str,
) -> dict[str, Any]:
    if manifest_path.exists() and manifest_path.is_file():
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        if isinstance(manifest, dict):
            return manifest

    return {
        "formatVersion": "2.0",
        "characterId": character_id,
        "characterName": character_name,
        "metadata": {
            "totalClips": 0,
            "chapters": [],
            "sources": {},
            "voiceIds": {},
            "primaryVoiceId": voice_id,
            "lastUpdated": "",
        },
        "clips": [],
    }


def _upsert_character_manifest(
    *,
    manifest_path: Path,
    chapter_title: str,
    source_file: str,
    character_id: str,
    character_name: str,
    voice_id: str,
    provider: str,
    split_lines: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = _load_or_create_manifest(
        manifest_path=manifest_path,
        character_id=character_id,
        character_name=character_name,
        voice_id=voice_id,
    )

    existing_clips = manifest.get("clips")
    if not isinstance(existing_clips, list):
        existing_clips = []

    replacing_ids = {int(item["id"]) for item in split_lines}
    kept_clips = []
    for clip in existing_clips:
        if not isinstance(clip, dict):
            continue
        clip_id = clip.get("id")
        if isinstance(clip_id, int) and clip_id in replacing_ids:
            continue
        kept_clips.append(clip)

    generated_at = utc_now_iso()
    new_clips: list[dict[str, Any]] = []
    for item in split_lines:
        new_clips.append(
            {
                "id": int(item["id"]),
                "characterId": character_id,
                "characterName": character_name,
                "text": str(item.get("sourceText") or item["text"]),
                "audioFile": str(item["audioFile"]),
                "chapter": chapter_title,
                "sourceFile": source_file,
                "voiceId": voice_id,
                "provider": provider,
                "emotion": None,
                "metadata": {
                    "generatedAt": generated_at,
                    "duration": round(float(item.get("durationSec", 0.0)), 4),
                    "startSec": round(float(item.get("startSec", 0.0)), 4),
                    "endSec": round(float(item.get("endSec", 0.0)), 4),
                },
            }
        )

    all_clips = kept_clips + new_clips
    all_clips.sort(
        key=lambda clip: (
            str(clip.get("chapter", "")),
            int(clip.get("id", 0)) if isinstance(clip.get("id"), int) else 0,
        )
    )

    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    source_counts: dict[str, int] = {}
    voice_counts: dict[str, int] = {}
    chapter_names: set[str] = set()

    for clip in all_clips:
        chapter_name = clip.get("chapter")
        if isinstance(chapter_name, str) and chapter_name:
            chapter_names.add(chapter_name)

        source_name = clip.get("sourceFile")
        if isinstance(source_name, str) and source_name:
            source_counts[source_name] = source_counts.get(source_name, 0) + 1

        clip_voice_id = clip.get("voiceId")
        if isinstance(clip_voice_id, str) and clip_voice_id:
            voice_counts[clip_voice_id] = voice_counts.get(clip_voice_id, 0) + 1

    existing_primary = metadata.get("primaryVoiceId")
    if isinstance(existing_primary, str) and existing_primary in voice_counts:
        primary_voice_id = existing_primary
    elif voice_counts:
        primary_voice_id = max(voice_counts.items(), key=lambda item: item[1])[0]
    else:
        primary_voice_id = voice_id

    metadata["totalClips"] = len(all_clips)
    metadata["chapters"] = sorted(chapter_names)
    metadata["sources"] = source_counts
    metadata["voiceIds"] = voice_counts
    metadata["primaryVoiceId"] = primary_voice_id
    metadata["lastUpdated"] = generated_at

    manifest["formatVersion"] = "2.0"
    manifest["characterId"] = character_id
    manifest["characterName"] = character_name
    manifest["metadata"] = metadata
    manifest["clips"] = all_clips

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)

    return manifest


def _run_character_chunk_pipeline(
    service: AudioGenerationService,
    request: VibeVoiceCharacterRequest,
) -> dict[str, Any]:
    default_chapter_title = str(request.chapter_title or "").strip() or "unknown"
    default_source_file = str(request.source_file or "").strip() or default_chapter_title

    lines = _normalize_character_lines(
        request.lines,
        character_id=request.character_id,
        character_name=request.character_name,
        fallback_chapter_title=default_chapter_title,
        fallback_source_file=default_source_file,
        replace_line_final_commas_with_periods=bool(request.replace_line_final_commas_with_periods),
        replace_numbers_with_words=bool(request.replace_numbers_with_words),
    )

    if not lines:
        return {
            "pipelineMode": "chunked-character-v1",
            "success": True,
            "generatedCount": 0,
            "totalLines": 0,
            "chaptersAffected": [],
            "errors": [],
            "warnings": [],
            "summary": {
                "lineCount": 0,
                "chunkCount": 0,
                "audioDurationSec": 0.0,
                "splitValidation": None,
            },
        }

    generation_text = _build_character_generation_text(
        lines=lines,
        min_chunk_chars=DEFAULT_MIN_CHUNK_CHARS,
        use_filler_for_short_batch=bool(request.use_filler_for_short_batch),
        filler_text=request.filler_text,
    )

    chapter_text = str(generation_text["chapterText"])
    chunk_plan = build_line_chunk_generation_plan(
        chapter_text=chapter_text,
        min_chunk_chars=DEFAULT_MIN_CHUNK_CHARS,
    )

    input_chapters = sorted(
        {
            str(line.get("chapterTitle") or default_chapter_title).strip() or default_chapter_title
            for line in lines
        }
    )

    if len(input_chapters) == 1:
        work_root = service.audio_root / input_chapters[0] / "audio_lines" / request.character_name
    else:
        work_root = service.audio_root / "_book_batch" / request.character_name

    work_root.mkdir(parents=True, exist_ok=True)

    work_dir = work_root / "_chunk_pipeline"
    chunks_dir = work_dir / "generation_chunks"
    full_audio_path = work_dir / "full_character.wav"
    transcription_json_path = work_dir / "transcription_result.json"
    split_validation_report_path = work_dir / "split_validation_report.json"

    class _ServiceChunkGenerator:
        def __init__(
            self,
            *,
            audio_service: AudioGenerationService,
            voice_id: str,
            voice_sample_root: Optional[str],
        ) -> None:
            self._audio_service = audio_service
            self._voice_id = voice_id
            self._voice_sample_root = voice_sample_root

        def generate_audio(
            self,
            *,
            text: str,
            sample_path: str,
            output_path: str,
            seed: int | None,
        ) -> None:
            effective_voice_id = self._voice_id or str(sample_path)
            _ = seed

            success, error_details = self._audio_service.generate_vibevoice_text_to_path(
                text=text,
                voice_id=effective_voice_id,
                output_path=Path(output_path),
                voice_sample_root=self._voice_sample_root,
            )
            if not success:
                raise RuntimeError(error_details or "VibeVoice generation failed.")

    chunk_generator = _ServiceChunkGenerator(
        audio_service=service,
        voice_id=request.voice_id,
        voice_sample_root=request.voice_sample_root,
    )

    generation_report = generate_line_chunked_chapter_audio(
        service=chunk_generator,
        chunk_plan=chunk_plan,
        sample_path=Path(request.voice_id),
        output_path=full_audio_path,
        chunks_dir=chunks_dir,
        min_chunk_chars=DEFAULT_MIN_CHUNK_CHARS,
        seed=None,
        join_pause_ms=DEFAULT_LINE_CHUNK_JOIN_MS,
    )

    audio_duration_sec = float(sf.info(str(full_audio_path)).duration)

    transcription, transcription_engine = _transcribe_with_whisperx_alignment(
        audio_path=full_audio_path,
        device=WHISPERX_DEVICE,
        compute_type=WHISPERX_COMPUTE_TYPE,
        batch_size=WHISPERX_BATCH_SIZE,
        vad_method=WHISPERX_VAD_METHOD,
        quiet=WHISPERX_QUIET,
    )

    transcription_json_path.parent.mkdir(parents=True, exist_ok=True)
    transcription_json_path.write_text(
        json.dumps(transcription, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    asr_words = collect_asr_word_tokens(transcription)
    if not asr_words:
        raise RuntimeError(
            "No WhisperX word timestamps were produced for chunked character generation."
        )

    line_timestamps, alignment_report = build_line_timestamps(
        lines=lines,
        asr_words=asr_words,
        audio_duration_sec=audio_duration_sec,
    )

    line_timestamps, boundary_refinement_report = refine_line_boundaries_with_silence(
        lines=line_timestamps,
        full_audio_path=full_audio_path,
        audio_duration_sec=audio_duration_sec,
        frame_ms=BOUNDARY_FRAME_MS,
        window_ms=BOUNDARY_WINDOW_MS,
        min_silence_ms=BOUNDARY_MIN_SILENCE_MS,
        silence_enter_db=BOUNDARY_SILENCE_ENTER_DB,
        silence_exit_db=BOUNDARY_SILENCE_EXIT_DB,
        line_end_pad_ms=BOUNDARY_LINE_END_PAD_MS,
        next_start_backpad_ms=BOUNDARY_NEXT_START_BACKPAD_MS,
        min_line_ms=BOUNDARY_MIN_LINE_MS,
    )

    full_audio_2d, split_sample_rate = load_audio_float_2d(full_audio_path)
    split_lines = _write_character_split_clips(
        lines=line_timestamps,
        full_audio=full_audio_2d,
        sample_rate=split_sample_rate,
        audio_root=service.audio_root,
        default_chapter_title=default_chapter_title,
        default_source_file=default_source_file,
    )

    def _upsert_split_manifests(split_lines_input: list[dict[str, Any]]) -> list[str]:
        split_lines_by_target: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for item in split_lines_input:
            chapter_title_value = item.get("chapterTitle") or default_chapter_title
            source_file_value = item.get("sourceFile") or default_source_file
            line_character_id_value = item.get("characterId") or request.character_id
            line_character_name_value = item.get("characterFolder") or request.character_name
            chapter_title = str(chapter_title_value).strip() or default_chapter_title
            source_file = str(source_file_value).strip() or default_source_file
            line_character_id = str(line_character_id_value).strip() or request.character_id
            line_character_name = str(line_character_name_value).strip() or request.character_name
            item["chapterTitle"] = chapter_title
            item["sourceFile"] = source_file
            item["characterId"] = line_character_id
            item["characterFolder"] = line_character_name
            split_lines_by_target.setdefault(
                (chapter_title, source_file, line_character_id, line_character_name),
                [],
            ).append(item)

        chapters = sorted({chapter for chapter, _, _, _ in split_lines_by_target})

        for (
            chapter_title,
            source_file,
            line_character_id,
            line_character_name,
        ), chapter_split_lines in split_lines_by_target.items():
            manifest_path = (
                service.audio_root
                / chapter_title
                / "audio_lines"
                / line_character_name
                / "manifest.json"
            )
            _upsert_character_manifest(
                manifest_path=manifest_path,
                chapter_title=chapter_title,
                source_file=source_file,
                character_id=line_character_id,
                character_name=line_character_name,
                voice_id=request.voice_id,
                provider=request.provider,
                split_lines=chapter_split_lines,
            )

        return chapters

    # Persist manifests as soon as split clips are first written so the UI can
    # reflect generated audio even if later validation steps fail.
    chapters_affected = _upsert_split_manifests(split_lines)

    release_accelerator_cache()
    validation_engine, transcribe_split_clip = create_split_validation_transcriber(
        validation_engine="whisperx",
        main_engine="whisperx",
        device=WHISPERX_DEVICE,
        compute_type=WHISPERX_COMPUTE_TYPE,
        batch_size=WHISPERX_BATCH_SIZE,
        vad_method=WHISPERX_VAD_METHOD,
        quiet=WHISPERX_QUIET,
        trim_leading_silence=True,
        silence_db=-40.0,
    )

    validation_reports = validate_split_lines(
        lines=line_timestamps,
        split_lines=split_lines,
        transcribe_clip=transcribe_split_clip,
        similarity_threshold=SPLIT_VALIDATION_THRESHOLD,
        neighbor_delta=SPLIT_VALIDATION_NEIGHBOR_DELTA,
    )

    initial_validation_summary = summarize_validation_reports(validation_reports)
    adjustments: list[dict[str, Any]] = []

    if initial_validation_summary["flaggedLineCount"] > 0:
        boundary_reports_by_index: dict[int, dict[str, Any]] = {}
        boundaries = boundary_refinement_report.get("boundaries")
        if isinstance(boundaries, list):
            for item in boundaries:
                if not isinstance(item, dict):
                    continue
                boundary_index = item.get("boundaryIndex")
                if isinstance(boundary_index, int):
                    boundary_reports_by_index[boundary_index] = item

        min_line_sec = max(0.001, float(BOUNDARY_MIN_LINE_MS) / 1000.0)
        flagged_indexes = [
            int(report["lineIndex"])
            for report in validation_reports
            if report.get("needsBoundaryAdjustment")
        ]

        for flagged_index in flagged_indexes:
            trigger_line_id = int(line_timestamps[flagged_index]["id"])

            if flagged_index > 0:
                adjustment = attempt_boundary_shift(
                    lines=line_timestamps,
                    validation_reports=validation_reports,
                    boundary_index=flagged_index,
                    boundary_report=boundary_reports_by_index.get(flagged_index),
                    full_audio=full_audio_2d,
                    sample_rate=split_sample_rate,
                    transcribe_clip=transcribe_split_clip,
                    similarity_threshold=SPLIT_VALIDATION_THRESHOLD,
                    neighbor_delta=SPLIT_VALIDATION_NEIGHBOR_DELTA,
                    min_line_sec=min_line_sec,
                    alternates_per_side=SPLIT_VALIDATION_ALTERNATES_PER_SIDE,
                    min_improvement=SPLIT_VALIDATION_MIN_IMPROVEMENT,
                    trigger_line_id=trigger_line_id,
                )
                if adjustment is not None:
                    adjustments.append(adjustment)

            if flagged_index + 1 < len(line_timestamps):
                adjustment = attempt_boundary_shift(
                    lines=line_timestamps,
                    validation_reports=validation_reports,
                    boundary_index=flagged_index + 1,
                    boundary_report=boundary_reports_by_index.get(flagged_index + 1),
                    full_audio=full_audio_2d,
                    sample_rate=split_sample_rate,
                    transcribe_clip=transcribe_split_clip,
                    similarity_threshold=SPLIT_VALIDATION_THRESHOLD,
                    neighbor_delta=SPLIT_VALIDATION_NEIGHBOR_DELTA,
                    min_line_sec=min_line_sec,
                    alternates_per_side=SPLIT_VALIDATION_ALTERNATES_PER_SIDE,
                    min_improvement=SPLIT_VALIDATION_MIN_IMPROVEMENT,
                    trigger_line_id=trigger_line_id,
                )
                if adjustment is not None:
                    adjustments.append(adjustment)

    if adjustments:
        split_lines = _write_character_split_clips(
            lines=line_timestamps,
            full_audio=full_audio_2d,
            sample_rate=split_sample_rate,
            audio_root=service.audio_root,
            default_chapter_title=default_chapter_title,
            default_source_file=default_source_file,
        )

        # Re-upsert manifests after boundary readjustment rewrites clips.
        chapters_affected = _upsert_split_manifests(split_lines)

    attach_validation_summary_to_split_lines(
        split_lines=split_lines,
        validation_reports=validation_reports,
    )
    final_validation_summary = summarize_validation_reports(validation_reports)

    split_validation_payload = build_split_validation_payload(
        validation_engine=validation_engine,
        initial_summary=initial_validation_summary,
        final_summary=final_validation_summary,
        adjustments=adjustments,
        reports=validation_reports,
        similarity_threshold=SPLIT_VALIDATION_THRESHOLD,
        neighbor_delta=SPLIT_VALIDATION_NEIGHBOR_DELTA,
        min_improvement=SPLIT_VALIDATION_MIN_IMPROVEMENT,
        alternates_per_side=SPLIT_VALIDATION_ALTERNATES_PER_SIDE,
    )

    split_validation_report_path.parent.mkdir(parents=True, exist_ok=True)
    split_validation_report_path.write_text(
        json.dumps(split_validation_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    warnings_out: list[str] = []
    if generation_text["inputCharacters"] < DEFAULT_MIN_CHUNK_CHARS:
        if generation_text["usedFiller"]:
            warnings_out.append(
                "Prepended filler text to raise a short batch from "
                f"{generation_text['inputCharacters']} to at least "
                f"{DEFAULT_MIN_CHUNK_CHARS} characters."
            )
        else:
            warnings_out.append(
                "Batch text is below the recommended "
                f"{DEFAULT_MIN_CHUNK_CHARS}-character target "
                f"({generation_text['inputCharacters']} characters). "
                "Consider enabling filler/pretext."
            )
    elif bool(request.use_filler_for_short_batch) and not generation_text["fillerAvailable"]:
        warnings_out.append("Filler/pretext was enabled, but no filler text was provided.")

    if final_validation_summary["flaggedLineCount"] > 0:
        warnings_out.append(
            "Split validation still flagged "
            f"{final_validation_summary['flaggedLineCount']} line(s) after readjustment."
        )

    return {
        "pipelineMode": "chunked-character-v1",
        "success": True,
        "generatedCount": len(split_lines),
        "totalLines": len(lines),
        "chaptersAffected": chapters_affected,
        "errors": [],
        "warnings": warnings_out,
        "summary": {
            "lineCount": len(split_lines),
            "audioDurationSec": round(audio_duration_sec, 4),
            "generation": {
                **generation_report,
                "inputCharacters": int(generation_text["inputCharacters"]),
                "fillerCharacters": int(generation_text["fillerCharacters"]),
                "usedFiller": bool(generation_text["usedFiller"]),
            },
            "transcription": transcription_engine,
            "alignment": alignment_report,
            "boundaryRefinement": boundary_refinement_report,
            "splitValidation": split_validation_payload.get("summary", {}),
            "artifacts": {
                "fullAudio": str(full_audio_path),
                "transcriptionJson": str(transcription_json_path),
                "splitValidationJson": str(split_validation_report_path),
            },
        },
    }


def create_vibevoice_router(
    get_audio_root: Callable[[], str],
    get_book_root: Callable[[], str],
    get_audio_service: Callable[[], Optional[AudioGenerationService]],
    set_audio_service: Callable[[str, AudioGenerationService], None],
) -> APIRouter:
    router = APIRouter(prefix="/api/vibevoice", tags=["vibevoice"])

    def _ensure_audio_service(audio_root_override: Optional[str]) -> AudioGenerationService:
        existing = get_audio_service()
        if existing is not None:
            return existing

        selected_root = audio_root_override or get_audio_root() or get_book_root()
        if not selected_root:
            raise HTTPException(
                status_code=400,
                detail="Audio root not configured. Set audio root or provide audio_root in request.",
            )

        normalized_root = os.path.normpath(selected_root)
        service = AudioGenerationService(normalized_root)
        set_audio_service(normalized_root, service)
        return service

    @router.post("/line")
    async def generate_line(request: VibeVoiceLineRequest):
        service = _ensure_audio_service(request.audio_root)

        result = await service.generate_audio_line(
            line_id=request.line_id,
            text=request.text,
            character_name=request.character_name,
            character_id=request.character_id,
            voice_id=request.voice_id,
            provider=request.provider,
            chapter_title=request.chapter_title,
            source_file=request.source_file,
            voice_sample_root=request.voice_sample_root,
        )

        if not result.get("success"):
            error_detail = result.get("error", "Generation failed")
            error_text = str(error_detail).lower()
            status = 500
            if "not found" in error_text or "could not be found" in error_text:
                status = 400
            elif "required" in error_text or "invalid" in error_text:
                status = 400
            raise HTTPException(status_code=status, detail=error_detail)

        return {
            "success": True,
            "generatedCount": 1,
            "audio_path": result.get("audio_path"),
        }

    @router.post("/character")
    async def generate_character(request: VibeVoiceCharacterRequest):
        if not _is_vibevoice_provider(request.provider):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Chunked character generation is VibeVoice-only. "
                    f"Received provider '{request.provider}'."
                ),
            )

        service = _ensure_audio_service(request.audio_root)

        try:
            return await asyncio.to_thread(_run_character_chunk_pipeline, service, request)
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Chunked character generation failed: {exc}",
            ) from exc

    @router.post("/chapter")
    async def generate_chapter(request: VibeVoiceChapterRequest):
        service = _ensure_audio_service(request.audio_root)

        raw_path = request.dialogue_path
        dialogue_file = Path(raw_path)
        if not dialogue_file.is_absolute():
            root = get_book_root()
            dialogue_file = Path(root) / raw_path

        if not dialogue_file.exists() or not dialogue_file.is_file():
            raise HTTPException(status_code=404, detail=f"dialogue.json not found: {dialogue_file}")

        try:
            with open(dialogue_file, "r", encoding="utf-8") as handle:
                dialogue = json.load(handle)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read dialogue.json: {exc}") from exc

        lines = dialogue.get("lines", [])
        if not isinstance(lines, list):
            raise HTTPException(status_code=400, detail="Invalid dialogue.json format: lines must be a list")

        assignments_by_id: Dict[str, VibeVoiceAssignment] = {
            item.character_id: item for item in request.assignments
        }
        assignments_by_name: Dict[str, VibeVoiceAssignment] = {
            item.character_name.strip().lower(): item
            for item in request.assignments
            if item.character_name
        }

        chapter_title = request.chapter_title or dialogue_file.parent.name
        source_file = request.source_file or str(dialogue_file)

        grouped_lines: Dict[str, dict[str, Any]] = {}
        errors: List[str] = []
        warnings_out: List[str] = []
        skipped_count = 0
        candidate_line_count = 0

        for line in lines:
            if not isinstance(line, dict):
                continue

            line_id = line.get("id")
            text = normalize_chapter_text(str(line.get("text") or ""))
            if not isinstance(line_id, int) or not text:
                continue

            candidate_line_count += 1
            character_id = str(line.get("characterId") or "narrator")
            chosen_speaker = line.get("chosenSpeaker")

            assignment = assignments_by_id.get(character_id)
            if assignment is None and isinstance(chosen_speaker, str):
                assignment = assignments_by_name.get(chosen_speaker.strip().lower())

            if assignment is None:
                skipped_count += 1
                errors.append(f"Line {line_id}: no selected voice mapping for character '{character_id}'")
                continue

            if not _is_vibevoice_provider(assignment.provider):
                skipped_count += 1
                errors.append(
                    f"Line {line_id}: provider '{assignment.provider}' is not supported. "
                    "Chunked generation is VibeVoice-only."
                )
                continue

            entry = grouped_lines.get(assignment.character_id)
            if entry is None:
                entry = {
                    "assignment": assignment,
                    "lines": [],
                }
                grouped_lines[assignment.character_id] = entry

            entry["lines"].append(
                VibeVoiceLineInput(
                    id=line_id,
                    text=text,
                    characterId=character_id,
                    chosenSpeaker=chosen_speaker if isinstance(chosen_speaker, str) else None,
                )
            )

        generated_count = 0
        per_character: list[dict[str, Any]] = []

        for character_id, item in grouped_lines.items():
            assignment = item["assignment"]
            character_lines = item["lines"]
            if not character_lines:
                continue

            character_request = VibeVoiceCharacterRequest(
                character_name=assignment.character_name,
                character_id=character_id,
                lines=character_lines,
                voice_id=assignment.voice_id,
                provider=assignment.provider,
                chapter_title=chapter_title,
                source_file=source_file,
                voice_sample_root=request.voice_sample_root,
                audio_root=request.audio_root,
            )

            try:
                result = await asyncio.to_thread(
                    _run_character_chunk_pipeline,
                    service,
                    character_request,
                )
            except (RuntimeError, ValueError, OSError, ImportError) as exc:
                errors.append(
                    f"Character {assignment.character_name}: chunked generation failed: {exc}"
                )
                continue

            if not bool(result.get("success")):
                response_errors = result.get("errors")
                if isinstance(response_errors, list) and response_errors:
                    for err in response_errors:
                        errors.append(f"Character {assignment.character_name}: {err}")
                else:
                    errors.append(f"Character {assignment.character_name}: unknown chunked generation failure")
                continue

            generated_count += int(result.get("generatedCount") or 0)

            result_warnings = result.get("warnings")
            if isinstance(result_warnings, list):
                for warning in result_warnings:
                    warnings_out.append(f"Character {assignment.character_name}: {warning}")

            per_character.append(
                {
                    "characterId": character_id,
                    "characterName": assignment.character_name,
                    "generatedCount": int(result.get("generatedCount") or 0),
                    "totalLines": int(result.get("totalLines") or len(character_lines)),
                    "summary": result.get("summary") if isinstance(result.get("summary"), dict) else None,
                }
            )

        return {
            "pipelineMode": "chunked-character-v1",
            "success": len(errors) == 0,
            "generatedCount": generated_count,
            "skippedCount": skipped_count,
            "totalLines": candidate_line_count,
            "errors": errors,
            "warnings": warnings_out,
            "chapterTitle": chapter_title,
            "characters": per_character,
        }

    return router
