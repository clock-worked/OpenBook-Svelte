import argparse
import importlib
import json
import logging
import os
import sys
import warnings
from typing import Any


def format_ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm for readable timestamp output."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def detect_leading_silence_seconds(
    audio_samples: Any,
    sample_rate: int,
    silence_db: float = -40.0,
    frame_ms: float = 10.0,
) -> float:
    """Estimate leading silence by finding first frame above an RMS threshold."""
    import numpy as np

    if len(audio_samples) == 0:
        return 0.0

    frame_size = max(1, int(sample_rate * (frame_ms / 1000.0)))
    threshold = 10 ** (silence_db / 20.0)

    total_frames = len(audio_samples) // frame_size
    if total_frames == 0:
        return 0.0

    for frame_idx in range(total_frames):
        start = frame_idx * frame_size
        end = start + frame_size
        frame = audio_samples[start:end]

        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms >= threshold:
            return start / sample_rate

    return 0.0


def shift_result_timestamps(result: dict, offset_seconds: float) -> dict:
    """Shift segment and word timestamps to match the original untrimmed audio timeline."""
    if offset_seconds <= 0:
        return result

    for segment in result.get("segments", []):
        if "start" in segment:
            segment["start"] = float(segment["start"]) + offset_seconds
        if "end" in segment:
            segment["end"] = float(segment["end"]) + offset_seconds

        for word_data in segment.get("words", []):
            if "start" in word_data and word_data["start"] is not None:
                word_data["start"] = float(word_data["start"]) + offset_seconds
            if "end" in word_data and word_data["end"] is not None:
                word_data["end"] = float(word_data["end"]) + offset_seconds

    return result


def transcribe_with_openai_whisper(
    audio_path: str, trim_leading_silence: bool, silence_db: float
) -> dict:
    try:
        import whisper
    except ImportError:
        print(
            "Whisper is not installed. Install it with: pip install openai-whisper",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.exists(audio_path):
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    print("Loading model: small.en (OpenAI Whisper)")
    model = whisper.load_model("small.en")

    audio_input = audio_path
    leading_silence_offset = 0.0

    if trim_leading_silence:
        audio_samples = whisper.load_audio(audio_path)
        leading_silence_offset = detect_leading_silence_seconds(
            audio_samples=audio_samples,
            sample_rate=16000,
            silence_db=silence_db,
        )

        if leading_silence_offset > 0:
            start_idx = int(leading_silence_offset * 16000)
            audio_input = audio_samples[start_idx:]
            print(
                f"Detected leading silence: {leading_silence_offset:.3f}s "
                f"(threshold {silence_db:.1f} dB)"
            )
        else:
            print("No leading silence detected.")

    print(f"Transcribing: {audio_path}")
    result = model.transcribe(audio_input, word_timestamps=True)
    result = shift_result_timestamps(result, leading_silence_offset)

    return result


def transcribe_with_whisperx(
    audio_path: str,
    device: str = "auto",
    compute_type: str = "auto",
    batch_size: int = 0,
    vad_method: str = "silero",
    run_alignment: bool = True,
    quiet: bool = False,
) -> dict:
    try:
        import torch
        whisperx = importlib.import_module("whisperx")
    except ImportError:
        print(
            "WhisperX is not installed. Install it with: pip install whisperx",
            file=sys.stderr,
        )
        sys.exit(1)

    selected_device = "cuda" if torch.cuda.is_available() else "cpu"
    if device != "auto":
        selected_device = device

    if selected_device == "cuda" and not torch.cuda.is_available():
        print(
            "CUDA was requested but is not available in this Python environment. "
            "Install a CUDA-enabled PyTorch build or use --device cpu.",
            file=sys.stderr,
        )
        sys.exit(1)

    selected_compute_type = "float16" if selected_device == "cuda" else "int8"
    if compute_type != "auto":
        selected_compute_type = compute_type

    # Tune throughput automatically when the caller leaves batch size unset.
    selected_batch_size = batch_size
    if selected_batch_size <= 0:
        selected_batch_size = 32 if selected_device == "cuda" else 8

    if quiet:
        warnings.simplefilter("ignore")
        os.environ.setdefault("PYTHONWARNINGS", "ignore")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        logging.getLogger().setLevel(logging.ERROR)
        logging.getLogger("whisperx").setLevel(logging.ERROR)
        logging.getLogger("pyannote").setLevel(logging.ERROR)
        logging.getLogger("faster_whisper").setLevel(logging.ERROR)
        logging.getLogger("torch").setLevel(logging.ERROR)

    print(
        "Loading model: "
        f"small.en (WhisperX, device={selected_device}, compute={selected_compute_type}, "
        f"batch={selected_batch_size}, vad={vad_method}, align={run_alignment})"
    )
    model = whisperx.load_model(
        "small.en",
        device=selected_device,
        compute_type=selected_compute_type,
        vad_method=vad_method,
    )

    print(f"Transcribing: {audio_path}")
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=selected_batch_size)

    if run_alignment:
        print("Running forced alignment for improved word timestamps...")
        align_model, metadata = whisperx.load_align_model(
            language_code=result["language"],
            device=selected_device,
        )
        aligned = whisperx.align(
            result["segments"],
            align_model,
            metadata,
            audio,
            selected_device,
            return_char_alignments=False,
        )
    else:
        aligned = result

    # Normalize field naming so output printer works the same way as OpenAI Whisper results.
    aligned["text"] = " ".join(
        seg.get("text", "").strip() for seg in aligned.get("segments", [])
    ).strip()
    return aligned


def print_result(result: dict) -> None:

    print("\n=== Full Transcript ===")
    print(result.get("text", "").strip())

    print("\n=== Word Timestamps ===")
    segments = result.get("segments", [])
    words_printed = 0
    for segment in segments:
        for word_data in segment.get("words", []):
            word = word_data.get("word", "").strip()
            start = word_data.get("start")
            end = word_data.get("end")

            if start is None or end is None:
                continue

            print(f"[{format_ts(start)} -> {format_ts(end)}] {word}")
            words_printed += 1

    if words_printed == 0:
        print(
            "(No word-level timestamps in this result. Use alignment for word timestamps.)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe audio with Whisper small.en and print word timestamps."
    )
    parser.add_argument("audio_path", help="Path to the input audio file")
    parser.add_argument(
        "--no-trim-leading-silence",
        action="store_true",
        help="Disable leading-silence trimming before transcription.",
    )
    parser.add_argument(
        "--silence-db",
        type=float,
        default=-40.0,
        help="Silence threshold in dB for leading-silence detection (default: -40.0).",
    )
    parser.add_argument(
        "--engine",
        choices=["openai", "whisperx"],
        default="openai",
        help="Timestamp engine: openai (default) or whisperx (more accurate word alignment).",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="WhisperX device selection (default: auto).",
    )
    parser.add_argument(
        "--compute-type",
        choices=["auto", "float16", "int8", "int8_float16", "float32"],
        default="auto",
        help="WhisperX compute type (default: auto).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help=(
            "WhisperX transcription batch size. "
            "Default auto: 32 on CUDA, 8 on CPU."
        ),
    )
    parser.add_argument(
        "--vad-method",
        choices=["silero", "pyannote"],
        default="silero",
        help="WhisperX VAD backend (default: silero; usually faster and quieter).",
    )
    parser.add_argument(
        "--no-align",
        action="store_true",
        help="Skip WhisperX forced alignment (faster, but no reliable word timestamps).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce third-party warning/log noise in WhisperX mode.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to save raw transcription JSON (segments + word timestamps).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.engine == "whisperx":
        transcription_result = transcribe_with_whisperx(
            audio_path=args.audio_path,
            device=args.device,
            compute_type=args.compute_type,
            batch_size=args.batch_size,
            vad_method=args.vad_method,
            run_alignment=not args.no_align,
            quiet=args.quiet,
        )
    else:
        transcription_result = transcribe_with_openai_whisper(
            audio_path=args.audio_path,
            trim_leading_silence=not args.no_trim_leading_silence,
            silence_db=args.silence_db,
        )

    if args.json_out:
        output_path = os.path.abspath(args.json_out)
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(transcription_result, handle,
                      indent=2, ensure_ascii=False)
        print(f"Saved raw result JSON: {output_path}")

    print_result(transcription_result)
