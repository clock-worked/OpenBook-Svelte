"""Run end-to-end chapter audio generation, splitting, and validation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

from _path_setup import bootstrap_python_script_paths

bootstrap_python_script_paths(__file__)

import soundfile as sf

from py_services.chapter_audio_test.asr_alignment import (
    build_line_timestamps,
    collect_asr_word_tokens,
)
from py_services.chapter_audio_test.audio_ops import (
    load_audio_float_2d,
    refine_line_boundaries_with_silence,
    split_audio_by_lines,
)
from py_services.chapter_audio_test.generation import (
    build_line_chunk_generation_plan,
    generate_line_chunked_chapter_audio,
)
from py_services.chapter_audio_test.manifest_ops import (
    build_character_manifests,
    load_existing_split_state,
    load_manifest_lines,
)
from py_services.chapter_audio_test.text_utils import normalize_chapter_text, tokenize, utc_now_iso
from py_services.chapter_audio_test.validation import (
    attach_validation_summary_to_split_lines,
    attempt_boundary_shift,
    build_split_validation_payload,
    create_split_validation_transcriber,
    release_accelerator_cache,
    summarize_validation_reports,
    validate_split_lines,
)
from py_services.vibevoice_local_service import VibeVoiceLocalService
from scripts.python.asr_validation.transcribe_whisper_small_en_gpu import (
    transcribe_with_openai_whisper,
    transcribe_with_whisperx,
)


_WINDOWS_DLL_HANDLES: list[Any] = []


def _ensure_windows_cudnn_runtime() -> None:
    """Ensure cuDNN DLLs from the active venv are discoverable on Windows.

    WhisperX/ctranslate2 loads CUDA/cuDNN dynamically and can fail with
    `Could not locate cudnn_ops_infer64_8.dll` when the venv nvidia cudnn bin
    directory is not in the process DLL search path.
    """
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
        Path(__file__).resolve().parents[3]
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


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for chapter audio test execution."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate full chapter VibeVoice audio, transcribe with timestamps, "
            "split by manifest lines, and validate post-cut snippets."
        )
    )
    parser.add_argument("--chapter-dir", type=Path, required=True)
    parser.add_argument("--sample-path", type=Path, required=True)
    parser.add_argument("--chapter-text", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--full-audio", type=Path, default=None)
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--readjust-existing-splits", action="store_true")
    parser.add_argument("--no-line-chunk-generate", action="store_true")
    parser.add_argument("--line-chunk-min-chars", type=int, default=800)
    parser.add_argument("--line-chunk-max-chars", type=int, default=None, help="Maximum characters per chunk before splitting at sentence boundary.")
    parser.add_argument("--line-chunk-join-ms", type=int, default=0)
    parser.add_argument("--resume-chunks", action="store_true", help="Resume existing generated audio chunks if present.")
    parser.add_argument(
        "--character-filter",
        default=None,
        help=(
            "Optional character selector (for example: 'scribe'). When set, "
            "only matching manifest lines are generated and split."
        ),
    )

    parser.add_argument(
        "--engine", choices=["openai", "whisperx"], default="whisperx")
    parser.add_argument(
        "--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument(
        "--compute-type",
        choices=["auto", "float16", "int8", "int8_float16", "float32"],
        default="auto",
    )
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument(
        "--vad-method", choices=["silero", "pyannote"], default="silero")
    parser.add_argument("--no-align", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-trim-leading-silence", action="store_true")
    parser.add_argument("--silence-db", type=float, default=-40.0)
    parser.add_argument("--no-boundary-silence-snap", action="store_true")
    parser.add_argument("--boundary-frame-ms", type=int, default=10)
    parser.add_argument("--boundary-window-ms", type=int, default=360)
    parser.add_argument("--boundary-min-silence-ms", type=int, default=80)
    parser.add_argument("--boundary-silence-enter-db",
                        type=float, default=-42.0)
    parser.add_argument("--boundary-silence-exit-db",
                        type=float, default=-35.0)
    parser.add_argument("--boundary-line-end-pad-ms", type=int, default=140)
    parser.add_argument("--boundary-next-start-backpad-ms",
                        type=int, default=45)
    parser.add_argument("--boundary-min-line-ms", type=int, default=90)

    parser.add_argument("--no-split-validation", action="store_true")
    parser.add_argument(
        "--split-validation-engine",
        choices=["same", "openai", "whisperx"],
        default="same",
    )
    parser.add_argument("--split-validation-threshold",
                        type=float, default=0.82)
    parser.add_argument("--split-validation-neighbor-delta",
                        type=float, default=0.08)
    parser.add_argument("--split-validation-min-improvement",
                        type=float, default=0.04)
    parser.add_argument(
        "--split-validation-alternates-per-side", type=int, default=1)

    parser.add_argument("--vibevoice-model-path", default=None)
    parser.add_argument("--vibevoice-repo-path", default=None)
    parser.add_argument("--vibevoice-device", default=None)
    parser.add_argument("--vibevoice-ddpm-steps", type=int, default=20)
    parser.add_argument("--vibevoice-cfg-scale", type=float, default=1.3)
    parser.add_argument("--vibevoice-seed", type=int, default=None)

    parser.add_argument(
        "--tts-engine",
        choices=["vibevoice", "chatterbox"],
        default="vibevoice",
        help="TTS engine to use for chapter audio generation.",
    )
    parser.add_argument(
        "--chatterbox-variant",
        choices=["turbo", "base"],
        default="turbo",
        help="Chatterbox model variant: 'turbo' (1-step fast) or 'base' (multi-step expressive).",
    )
    parser.add_argument(
        "--chatterbox-device",
        default="cuda",
        help="Device for Chatterbox model (cuda or cpu).",
    )
    parser.add_argument(
        "--chatterbox-python",
        type=Path,
        default=None,
        help="Path to Python interpreter with chatterbox installed.",
    )
    parser.add_argument(
        "--chatterbox-temperature",
        type=float,
        default=0.8,
        help="Sampling temperature for Chatterbox.",
    )
    parser.add_argument(
        "--chatterbox-repetition-penalty",
        type=float,
        default=1.2,
        help="Repetition penalty for Chatterbox.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for audio generation (applies to both VibeVoice and Chatterbox).",
    )
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    """Resolve and validate all input/output filesystem paths."""
    chapter_dir = args.chapter_dir.resolve()
    chapter_text_path = (args.chapter_text or (
        chapter_dir / "chapter.txt")).resolve()

    candidate_manifest = args.manifest
    if candidate_manifest is None:
        if (chapter_dir / "audio_lines" / "manifest.json").exists():
            candidate_manifest = chapter_dir / "audio_lines" / "manifest.json"
        elif (chapter_dir / "dialogue.json").exists():
            candidate_manifest = chapter_dir / "dialogue.json"
        else:
            candidate_manifest = chapter_dir / "audio_lines" / "manifest.json"
    manifest_path = candidate_manifest.resolve()

    output_dir = (args.output_dir or (chapter_dir / "audio_test")).resolve()
    tts_engine = getattr(args, "tts_engine", "vibevoice")
    default_audio_name = f"{chapter_dir.name}-{tts_engine}-full.wav"
    full_audio_path = (
        args.full_audio or (output_dir / default_audio_name)
    ).resolve()

    output_dir.mkdir(parents=True, exist_ok=True)

    if not chapter_text_path.exists():
        raise FileNotFoundError(
            f"Chapter text file not found: {chapter_text_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")
    if not args.sample_path.exists():
        raise FileNotFoundError(f"Voice sample not found: {args.sample_path}")

    return {
        "chapterDir": chapter_dir,
        "chapterTextPath": chapter_text_path,
        "manifestPath": manifest_path,
        "outputDir": output_dir,
        "fullAudioPath": full_audio_path,
    }


def load_chapter_inputs(
    *,
    chapter_text_path: Path,
    manifest_path: Path,
) -> tuple[list[dict[str, Any]], str, str]:
    """Load source manifest and chapter text content used by the pipeline."""
    lines = load_manifest_lines(manifest_path)
    chapter_raw_text = chapter_text_path.read_text(encoding="utf-8")
    chapter_text = normalize_chapter_text(chapter_raw_text)
    if not chapter_text:
        raise ValueError(f"Chapter text is empty: {chapter_text_path}")
    return lines, chapter_raw_text, chapter_text


def normalize_character_token(value: str) -> str:
    """Normalize character text for case-insensitive matching."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def filter_lines_by_character(
    lines: list[dict[str, Any]],
    character_filter: str,
) -> list[dict[str, Any]]:
    """Filter manifest lines to only those matching the requested character."""
    target = normalize_character_token(character_filter)
    if not target:
        raise ValueError("--character-filter cannot be empty")

    filtered: list[dict[str, Any]] = []
    for line in lines:
        character_id = str(line.get("characterId", ""))
        character_folder = str(line.get("characterFolder", ""))
        if (
            normalize_character_token(character_id) == target
            or normalize_character_token(character_folder) == target
        ):
            filtered.append(line)

    return filtered


def run_readjust_mode(
    *,
    lines: list[dict[str, Any]],
    output_dir: Path,
    result_manifest_path: Path,
    full_audio_path: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    float,
    dict[str, Any],
]:
    """Load existing split artifacts and prepare reports for readjust mode."""
    if not full_audio_path.exists():
        raise FileNotFoundError(
            "Readjust mode requires an existing full-audio file: "
            f"{full_audio_path}"
        )

    (
        line_timestamps,
        split_lines,
        existing_result_payload,
        text_mismatch_count,
    ) = load_existing_split_state(
        result_manifest_path=result_manifest_path,
        expected_lines=lines,
        output_dir=output_dir,
    )

    if text_mismatch_count > 0:
        print(
            "Warning: source manifest text differs from existing split "
            f"results for {text_mismatch_count} lines."
        )

    missing_split_file_count = sum(
        1
        for item in split_lines
        if not Path(str(item["audioPath"])).exists()
    )
    if missing_split_file_count > 0:
        print(
            "Existing split timestamps found, but "
            f"{missing_split_file_count} split clips are missing; "
            "rebuilding split clips before readjustment."
        )
        split_lines = split_audio_by_lines(
            full_audio_path=full_audio_path,
            output_dir=output_dir,
            lines=line_timestamps,
        )
    else:
        print(f"Reusing existing split clips from: {result_manifest_path}")

    audio_duration_sec = float(sf.info(str(full_audio_path)).duration)
    existing_summary = (
        existing_result_payload.get("summary")
        if isinstance(existing_result_payload.get("summary"), dict)
        else {}
    )

    previous_generation = existing_summary.get("generation")
    if isinstance(previous_generation, dict):
        generation_report = {
            **previous_generation,
            "mode": "readjust-existing-splits",
            "reusedFrom": str(result_manifest_path),
        }
    else:
        generation_report = {
            "mode": "readjust-existing-splits",
            "chunkCount": None,
            "minChunkChars": None,
            "reusedFrom": str(result_manifest_path),
        }

    previous_alignment = existing_summary.get("alignment")
    alignment_report = (
        previous_alignment
        if isinstance(previous_alignment, dict)
        else {
            "enabled": False,
            "reused": True,
            "reason": "loaded-from-existing-split-manifest",
        }
    )

    previous_boundary_refinement = existing_summary.get("boundaryRefinement")
    boundary_refinement_report = (
        previous_boundary_refinement
        if isinstance(previous_boundary_refinement, dict)
        else {
            "enabled": False,
            "applied": False,
            "reason": "loaded-from-existing-split-manifest",
        }
    )

    return (
        line_timestamps,
        split_lines,
        generation_report,
        alignment_report,
        boundary_refinement_report,
        audio_duration_sec,
        existing_result_payload,
    )


def run_generation_alignment_mode(
    *,
    args: argparse.Namespace,
    chapter_raw_text: str,
    chapter_text: str,
    lines: list[dict[str, Any]],
    output_dir: Path,
    full_audio_path: Path,
    result_manifest_path: Path,
    transcription_json_path: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    float,
]:
    """Generate full audio, run ASR alignment, and produce split clips."""
    if result_manifest_path.exists():
        print(
            "Existing split manifest detected. Use "
            "--readjust-existing-splits to only run readjustment checks."
        )

    if not args.skip_generate:
        tts_engine = getattr(args, "tts_engine", "vibevoice")
        effective_seed = args.seed if args.seed is not None else args.vibevoice_seed

        if tts_engine == "chatterbox":
            from py_services.chatterbox_local_service import ChatterboxLocalService

            service = ChatterboxLocalService(
                variant=getattr(args, "chatterbox_variant", "turbo"),
                device=args.chatterbox_device,
                temperature=args.chatterbox_temperature,
                repetition_penalty=args.chatterbox_repetition_penalty,
                python_executable=args.chatterbox_python,
            )
        else:
            service = VibeVoiceLocalService(
                model_path=args.vibevoice_model_path,
                repo_path=args.vibevoice_repo_path,
                device=args.vibevoice_device,
                ddpm_steps=args.vibevoice_ddpm_steps,
                cfg_scale=args.vibevoice_cfg_scale,
            )

        if args.no_line_chunk_generate:
            print(f"Generating full chapter audio ({tts_engine}): {full_audio_path}")
            service.generate_audio(
                text=chapter_text,
                sample_path=str(args.sample_path.resolve()),
                output_path=str(full_audio_path),
                seed=effective_seed,
            )
            generation_report = {
                "engine": tts_engine,
                "mode": "single-pass",
                "chunkCount": 1,
                "minChunkChars": None,
            }
        else:
            if args.line_chunk_join_ms < 0:
                raise ValueError("--line-chunk-join-ms must be >= 0")

            chunk_plan = build_line_chunk_generation_plan(
                chapter_text=chapter_raw_text,
                min_chunk_chars=args.line_chunk_min_chars,
                max_chunk_chars=args.line_chunk_max_chars,
            )
            max_chars_desc = f", max {args.line_chunk_max_chars} chars" if args.line_chunk_max_chars else ""
            print(
                f"Generating chapter audio with line chunking ({tts_engine}) "
                f"({len(chunk_plan)} chunks, min {args.line_chunk_min_chars}{max_chars_desc}): "
                f"{full_audio_path}"
            )
            generation_report = generate_line_chunked_chapter_audio(
                service=service,
                chunk_plan=chunk_plan,
                sample_path=args.sample_path.resolve(),
                output_path=full_audio_path,
                chunks_dir=output_dir / "generation_chunks",
                min_chunk_chars=args.line_chunk_min_chars,
                seed=effective_seed,
                join_pause_ms=args.line_chunk_join_ms,
                resume_existing_chunks=getattr(args, "resume_chunks", False),
            )
            generation_report["engine"] = tts_engine

        if hasattr(service, "close"):
            service.close()
        del service
        release_accelerator_cache()
    elif not full_audio_path.exists():
        raise FileNotFoundError(
            "--skip-generate was set but full audio does not exist: "
            f"{full_audio_path}"
        )
    else:
        generation_report = {
            "mode": "skip-existing",
            "chunkCount": None,
            "minChunkChars": None,
        }

    generated_duration_sec = float(sf.info(str(full_audio_path)).duration)
    chapter_word_count = len(tokenize(chapter_text))
    if chapter_word_count >= 200 and generated_duration_sec < 30.0:
        raise RuntimeError(
            "Generated chapter audio is implausibly short "
            f"({generated_duration_sec:.2f}s for {chapter_word_count} words). "
            "This usually means text formatting failed before synthesis."
        )

    print(
        f"Transcribing full chapter audio ({args.engine}): {full_audio_path}")
    if args.engine == "whisperx":
        transcription = transcribe_with_whisperx(
            audio_path=str(full_audio_path),
            device=args.device,
            compute_type=args.compute_type,
            batch_size=args.batch_size,
            vad_method=args.vad_method,
            run_alignment=not args.no_align,
            quiet=args.quiet,
        )
    else:
        transcription = transcribe_with_openai_whisper(
            audio_path=str(full_audio_path),
            trim_leading_silence=not args.no_trim_leading_silence,
            silence_db=args.silence_db,
        )

    transcription_json_path.write_text(
        json.dumps(transcription, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    audio_duration_sec = float(sf.info(str(full_audio_path)).duration)
    asr_words = collect_asr_word_tokens(transcription)
    if not asr_words:
        raise RuntimeError(
            "No ASR word timestamps were produced. Re-run with WhisperX alignment enabled."
        )

    line_timestamps, alignment_report = build_line_timestamps(
        lines=lines,
        asr_words=asr_words,
        audio_duration_sec=audio_duration_sec,
    )

    if args.no_boundary_silence_snap:
        boundary_refinement_report = {
            "enabled": False,
            "applied": False,
            "reason": "disabled-by-flag",
        }
    else:
        line_timestamps, boundary_refinement_report = refine_line_boundaries_with_silence(
            lines=line_timestamps,
            full_audio_path=full_audio_path,
            audio_duration_sec=audio_duration_sec,
            frame_ms=args.boundary_frame_ms,
            window_ms=args.boundary_window_ms,
            min_silence_ms=args.boundary_min_silence_ms,
            silence_enter_db=args.boundary_silence_enter_db,
            silence_exit_db=args.boundary_silence_exit_db,
            line_end_pad_ms=args.boundary_line_end_pad_ms,
            next_start_backpad_ms=args.boundary_next_start_backpad_ms,
            min_line_ms=args.boundary_min_line_ms,
        )

    split_lines = split_audio_by_lines(
        full_audio_path=full_audio_path,
        output_dir=output_dir,
        lines=line_timestamps,
    )

    return (
        line_timestamps,
        split_lines,
        generation_report,
        alignment_report,
        boundary_refinement_report,
        audio_duration_sec,
    )


def run_split_validation_phase(
    *,
    args: argparse.Namespace,
    line_timestamps: list[dict[str, Any]],
    split_lines: list[dict[str, Any]],
    full_audio_path: Path,
    output_dir: Path,
    boundary_refinement_report: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], Path | None]:
    """Validate split clips and optionally readjust adjacent line boundaries."""
    if args.no_split_validation:
        return split_lines, {"enabled": False, "reason": "disabled-by-flag"}, None

    release_accelerator_cache()
    print("Loading split validation ASR...")
    validation_engine, transcribe_split_clip = create_split_validation_transcriber(
        validation_engine=args.split_validation_engine,
        main_engine=args.engine,
        device=args.device,
        compute_type=args.compute_type,
        batch_size=args.batch_size,
        vad_method=args.vad_method,
        quiet=args.quiet,
        trim_leading_silence=not args.no_trim_leading_silence,
        silence_db=args.silence_db,
    )

    validation_reports = validate_split_lines(
        lines=line_timestamps,
        split_lines=split_lines,
        transcribe_clip=transcribe_split_clip,
        similarity_threshold=args.split_validation_threshold,
        neighbor_delta=args.split_validation_neighbor_delta,
    )
    initial_validation_summary = summarize_validation_reports(
        validation_reports)
    print(
        "Initial split validation flagged "
        f"{initial_validation_summary['flaggedLineCount']} lines."
    )

    adjustments: list[dict[str, Any]] = []
    if initial_validation_summary["flaggedLineCount"] > 0:
        full_audio_2d, split_sample_rate = load_audio_float_2d(full_audio_path)
        boundary_reports_by_index: dict[int, dict[str, Any]] = {}

        boundaries = boundary_refinement_report.get("boundaries")
        if isinstance(boundaries, list):
            for item in boundaries:
                if not isinstance(item, dict):
                    continue
                boundary_index = item.get("boundaryIndex")
                if isinstance(boundary_index, int):
                    boundary_reports_by_index[boundary_index] = item

        min_line_sec = max(0.001, float(args.boundary_min_line_ms) / 1000.0)
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
                    boundary_report=boundary_reports_by_index.get(
                        flagged_index),
                    full_audio=full_audio_2d,
                    sample_rate=split_sample_rate,
                    transcribe_clip=transcribe_split_clip,
                    similarity_threshold=args.split_validation_threshold,
                    neighbor_delta=args.split_validation_neighbor_delta,
                    min_line_sec=min_line_sec,
                    alternates_per_side=args.split_validation_alternates_per_side,
                    min_improvement=args.split_validation_min_improvement,
                    trigger_line_id=trigger_line_id,
                )
                if adjustment is not None:
                    adjustments.append(adjustment)
                    print(
                        "Shifted boundary "
                        f"{adjustment['boundaryIndex']} from "
                        f"{adjustment['oldBoundarySec']:.4f}s "
                        f"to {adjustment['newBoundarySec']:.4f}s"
                    )

            if flagged_index + 1 < len(line_timestamps):
                adjustment = attempt_boundary_shift(
                    lines=line_timestamps,
                    validation_reports=validation_reports,
                    boundary_index=flagged_index + 1,
                    boundary_report=boundary_reports_by_index.get(
                        flagged_index + 1),
                    full_audio=full_audio_2d,
                    sample_rate=split_sample_rate,
                    transcribe_clip=transcribe_split_clip,
                    similarity_threshold=args.split_validation_threshold,
                    neighbor_delta=args.split_validation_neighbor_delta,
                    min_line_sec=min_line_sec,
                    alternates_per_side=args.split_validation_alternates_per_side,
                    min_improvement=args.split_validation_min_improvement,
                    trigger_line_id=trigger_line_id,
                )
                if adjustment is not None:
                    adjustments.append(adjustment)
                    print(
                        "Shifted boundary "
                        f"{adjustment['boundaryIndex']} from "
                        f"{adjustment['oldBoundarySec']:.4f}s "
                        f"to {adjustment['newBoundarySec']:.4f}s"
                    )

    if adjustments:
        split_lines = split_audio_by_lines(
            full_audio_path=full_audio_path,
            output_dir=output_dir,
            lines=line_timestamps,
        )

    attach_validation_summary_to_split_lines(
        split_lines=split_lines,
        validation_reports=validation_reports,
    )
    final_validation_summary = summarize_validation_reports(validation_reports)
    print(
        "Final split validation flagged "
        f"{final_validation_summary['flaggedLineCount']} lines after "
        f"{len(adjustments)} boundary adjustments."
    )

    split_validation_payload = build_split_validation_payload(
        validation_engine=validation_engine,
        initial_summary=initial_validation_summary,
        final_summary=final_validation_summary,
        adjustments=adjustments,
        reports=validation_reports,
        similarity_threshold=args.split_validation_threshold,
        neighbor_delta=args.split_validation_neighbor_delta,
        min_improvement=args.split_validation_min_improvement,
        alternates_per_side=args.split_validation_alternates_per_side,
    )
    split_validation_report_path = output_dir / "split_validation_report.json"
    split_validation_report_path.write_text(
        json.dumps(split_validation_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return split_lines, split_validation_payload, split_validation_report_path


def write_character_manifests(
    *,
    chapter_id: str,
    split_lines: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """Write per-character split manifests for downstream consumption."""
    per_character_manifests = build_character_manifests(
        chapter_id=chapter_id,
        split_lines=split_lines,
    )

    for character_folder, manifest in per_character_manifests.items():
        manifest_path_out = output_dir / "splits" / character_folder / "manifest.json"
        manifest_path_out.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def main() -> None:
    """Execute the chapter audio test pipeline end-to-end."""
    _ensure_windows_cudnn_runtime()
    args = parse_args()
    if args.readjust_existing_splits and args.no_split_validation:
        raise ValueError(
            "--readjust-existing-splits requires split validation. "
            "Remove --no-split-validation when running readjustment-only mode."
        )

    paths = resolve_paths(args)
    chapter_dir = paths["chapterDir"]
    chapter_text_path = paths["chapterTextPath"]
    manifest_path = paths["manifestPath"]
    output_dir = paths["outputDir"]
    full_audio_path = paths["fullAudioPath"]

    lines, chapter_raw_text, chapter_text = load_chapter_inputs(
        chapter_text_path=chapter_text_path,
        manifest_path=manifest_path,
    )

    if args.character_filter:
        filtered_lines = filter_lines_by_character(
            lines, args.character_filter)
        if not filtered_lines:
            raise ValueError(
                "No manifest lines matched --character-filter "
                f"'{args.character_filter}'."
            )
        lines = filtered_lines
        chapter_raw_text = "\n".join(str(item["text"]) for item in lines)
        chapter_text = normalize_chapter_text(chapter_raw_text)
        print(
            "Character filter enabled: "
            f"{args.character_filter} -> {len(lines)} lines"
        )

    chapter_id = chapter_dir.name
    result_manifest_path = output_dir / "audio_test_manifest.json"
    transcription_json_path = output_dir / "transcription_result.json"

    existing_result_payload: dict[str, Any] | None = None

    if args.readjust_existing_splits:
        (
            line_timestamps,
            split_lines,
            generation_report,
            alignment_report,
            boundary_refinement_report,
            audio_duration_sec,
            existing_result_payload,
        ) = run_readjust_mode(
            lines=lines,
            output_dir=output_dir,
            result_manifest_path=result_manifest_path,
            full_audio_path=full_audio_path,
        )
    else:
        (
            line_timestamps,
            split_lines,
            generation_report,
            alignment_report,
            boundary_refinement_report,
            audio_duration_sec,
        ) = run_generation_alignment_mode(
            args=args,
            chapter_raw_text=chapter_raw_text,
            chapter_text=chapter_text,
            lines=lines,
            output_dir=output_dir,
            full_audio_path=full_audio_path,
            result_manifest_path=result_manifest_path,
            transcription_json_path=transcription_json_path,
        )

    (
        split_lines,
        split_validation_payload,
        split_validation_report_path,
    ) = run_split_validation_phase(
        args=args,
        line_timestamps=line_timestamps,
        split_lines=split_lines,
        full_audio_path=full_audio_path,
        output_dir=output_dir,
        boundary_refinement_report=boundary_refinement_report,
    )

    write_character_manifests(
        chapter_id=chapter_id,
        split_lines=split_lines,
        output_dir=output_dir,
    )

    transcription_artifact: str | None = None
    if transcription_json_path.exists():
        transcription_artifact = str(transcription_json_path)
    elif isinstance(existing_result_payload, dict):
        previous_artifacts = existing_result_payload.get("artifacts")
        if isinstance(previous_artifacts, dict):
            previous_transcription = previous_artifacts.get(
                "transcriptionJson")
            if isinstance(previous_transcription, str) and previous_transcription.strip():
                transcription_artifact = previous_transcription

    result_payload = {
        "formatVersion": "audio-test/chapter-timestamp-split/v1",
        "createdAt": utc_now_iso(),
        "inputs": {
            "chapterDir": str(chapter_dir),
            "chapterText": str(chapter_text_path),
            "sourceManifest": str(manifest_path),
            "characterFilter": args.character_filter,
            "voiceSample": str(args.sample_path.resolve()),
            "fullAudio": str(full_audio_path),
            "ttsEngine": getattr(args, "tts_engine", "vibevoice"),
            "engine": args.engine,
        },
        "summary": {
            "lineCount": len(split_lines),
            "audioDurationSec": round(audio_duration_sec, 4),
            "generation": generation_report,
            "alignment": alignment_report,
            "boundaryRefinement": boundary_refinement_report,
            "splitValidation": split_validation_payload.get("summary", split_validation_payload)
            if isinstance(split_validation_payload, dict)
            else None,
        },
        "artifacts": {
            "transcriptionJson": transcription_artifact,
            "splitsRoot": str((output_dir / "splits").resolve()),
            "splitValidationJson": str(split_validation_report_path)
            if split_validation_report_path is not None
            else None,
        },
        "lines": split_lines,
    }

    result_manifest_path.write_text(
        json.dumps(result_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if transcription_json_path.exists():
        print(f"Wrote transcription JSON: {transcription_json_path}")
    elif transcription_artifact:
        print(f"Using existing transcription JSON: {transcription_artifact}")
    else:
        print("No transcription JSON available for this run.")
    print(f"Wrote split manifest: {result_manifest_path}")
    print(f"Wrote split audio root: {output_dir / 'splits'}")


if __name__ == "__main__":
    main()
