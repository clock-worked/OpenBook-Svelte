"""Generate normalized ASR word timestamp artifacts for audio clips."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import torch


WORD_RE = re.compile(r"[a-z0-9']+")
CLIP_NAME_RE = re.compile(r"^(?P<line_id>\d+)-(?P<character>.+)$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_token(value: str) -> str:
    return "".join(WORD_RE.findall(value.lower()))


def tokenize_for_compare(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def to_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def similarity_ratio(expected: str, actual: str) -> float:
    expected_tokens = " ".join(tokenize_for_compare(expected))
    actual_tokens = " ".join(tokenize_for_compare(actual))
    if not expected_tokens and not actual_tokens:
        return 1.0
    return round(SequenceMatcher(None, expected_tokens, actual_tokens, autojunk=False).ratio(), 4)


def load_dialogue_map(dialogue_path: Path | None) -> dict[int, dict[str, Any]]:
    if dialogue_path is None:
        return {}
    payload = json.loads(dialogue_path.read_text(encoding="utf-8"))
    lines = payload.get("lines")
    if not isinstance(lines, list):
        return {}
    line_map: dict[int, dict[str, Any]] = {}
    for entry in lines:
        if not isinstance(entry, dict):
            continue
        line_id = entry.get("id")
        if isinstance(line_id, int):
            line_map[line_id] = entry
    return line_map


def parse_clip_name(audio_path: Path) -> tuple[int | None, str | None]:
    stem = audio_path.stem
    match = CLIP_NAME_RE.match(stem)
    if not match:
        return None, None
    line_id_text = match.group("line_id")
    try:
        line_id = int(line_id_text)
    except ValueError:
        line_id = None
    return line_id, match.group("character")


def collect_audio_files(audio_files: list[Path], audio_dir: Path | None) -> list[Path]:
    collected: list[Path] = []
    for candidate in audio_files:
        if candidate.exists() and candidate.is_file():
            collected.append(candidate.resolve())
    if audio_dir and audio_dir.exists():
        for extension in ("*.wav", "*.mp3", "*.flac", "*.m4a"):
            for candidate in sorted(audio_dir.rglob(extension)):
                collected.append(candidate.resolve())
    deduped: list[Path] = []
    seen = set()
    for item in collected:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def split_words_for_chunks(text: str) -> list[str]:
    return [item for item in re.findall(r"\S+", text) if item.strip()]


def build_word_chunks_from_segment_text(
    text: str,
    start_sec: float | None,
    end_sec: float | None,
) -> list[dict[str, Any]]:
    words = split_words_for_chunks(text)
    if not words:
        return []

    if start_sec is None or end_sec is None or end_sec < start_sec:
        return [{"text": token, "timestamp": [None, None]} for token in words]

    total = len(words)
    duration = end_sec - start_sec
    if duration <= 0:
        return [{"text": token, "timestamp": [start_sec, start_sec]} for token in words]

    step = duration / total
    chunks: list[dict[str, Any]] = []
    for index, token in enumerate(words):
        token_start = round(start_sec + (index * step), 4)
        token_end = round(start_sec + ((index + 1) * step), 4)
        chunks.append(
            {
                "text": token,
                "timestamp": [token_start, token_end],
            }
        )
    return chunks


def create_native_vibevoice_asr(model: str, device: str, torch_dtype: str | None):
    from vibevoice.modular.modeling_vibevoice_asr import VibeVoiceASRForConditionalGeneration  # noqa: WPS433
    from vibevoice.processor.vibevoice_asr_processor import VibeVoiceASRProcessor  # noqa: WPS433

    class NativeVibeVoiceASR:
        def __init__(self):
            resolved_device = device
            if resolved_device == "auto":
                resolved_device = "cuda" if torch.cuda.is_available() else "cpu"

            self.device = resolved_device
            if torch_dtype == "float16":
                self.dtype = torch.float16
            elif torch_dtype == "bfloat16":
                self.dtype = torch.bfloat16
            elif torch_dtype == "float32":
                self.dtype = torch.float32
            else:
                self.dtype = torch.float16 if self.device == "cuda" else torch.float32

            self.processor = VibeVoiceASRProcessor.from_pretrained(model)
            model_kwargs: dict[str, Any] = {"dtype": self.dtype}
            self.model = VibeVoiceASRForConditionalGeneration.from_pretrained(
                model, **model_kwargs)
            self.model.to(self.device)
            self.model.eval()

        def transcribe(self, audio_path: Path) -> dict[str, Any]:
            inputs = self.processor(
                audio=str(audio_path),
                sampling_rate=24000,
                return_tensors="pt",
                use_streaming=False,
            )
            moved_inputs = {
                key: (value.to(self.device) if hasattr(value, "to") else value)
                for key, value in inputs.items()
            }
            with torch.no_grad():
                output_ids = self.model.generate(
                    **moved_inputs, max_new_tokens=2048)

            decoded = self.processor.batch_decode(
                output_ids, skip_special_tokens=True)
            decoded_text = decoded[0] if decoded else ""
            parsed_segments = self.processor.post_process_transcription(
                decoded_text)

            if isinstance(parsed_segments, list) and parsed_segments:
                segment_texts: list[str] = []
                chunks: list[dict[str, Any]] = []
                for segment in parsed_segments:
                    if not isinstance(segment, dict):
                        continue
                    content = str(segment.get("text", "")).strip()
                    start_sec = to_float_or_none(segment.get("start_time"))
                    end_sec = to_float_or_none(segment.get("end_time"))
                    if content:
                        segment_texts.append(content)
                    chunks.extend(build_word_chunks_from_segment_text(
                        content, start_sec, end_sec))

                return {
                    "text": " ".join(segment_texts).strip(),
                    "chunks": chunks,
                    "raw": decoded_text,
                    "_engine_provider": "vibevoice-native",
                }

            return {
                "text": str(decoded_text).strip(),
                "chunks": [],
                "raw": decoded_text,
                "_engine_provider": "vibevoice-native",
            }

    return NativeVibeVoiceASR()


def create_pipeline(model: str, device: str, torch_dtype: str | None):
    from transformers import pipeline  # noqa: WPS433

    if "vibevoice" in model.lower():
        return create_native_vibevoice_asr(model=model, device=device, torch_dtype=torch_dtype)

    if "vibevoice" in model.lower():
        try:
            import vibevoice  # noqa: F401,WPS433
        except Exception:
            pass

    if device == "auto":
        use_device = 0 if torch.cuda.is_available() else -1
    elif device == "cuda":
        use_device = 0
    else:
        use_device = -1

    resolved_dtype = None
    if torch_dtype == "float16":
        resolved_dtype = torch.float16
    elif torch_dtype == "bfloat16":
        resolved_dtype = torch.bfloat16
    elif torch_dtype == "float32":
        resolved_dtype = torch.float32

    kwargs: dict[str, Any] = {
        "task": "automatic-speech-recognition",
        "model": model,
        "device": use_device,
    }
    if resolved_dtype is not None:
        kwargs["torch_dtype"] = resolved_dtype
    try:
        return pipeline(**kwargs)
    except ValueError as exc:
        message = str(exc)
        needs_remote_code = (
            "model type `vibevoice`" in message.lower()
            or "trust_remote_code" in message
            or "does not recognize this architecture" in message
        )
        if not needs_remote_code:
            raise

        print(
            "[ASR] Retrying with trust_remote_code=True for custom model architecture...")
        if "vibevoice" in model.lower():
            try:
                import vibevoice  # noqa: F401,WPS433
            except Exception:
                pass
        kwargs["trust_remote_code"] = True
        return pipeline(**kwargs)


def transcribe_with_words(asr_pipeline, audio_path: Path, chunk_length_s: int, batch_size: int) -> dict[str, Any]:
    if hasattr(asr_pipeline, "transcribe"):
        return asr_pipeline.transcribe(audio_path)

    model_type = ""
    try:
        model_type = str(getattr(asr_pipeline.model.config, "model_type", "")).lower()
    except (AttributeError, TypeError):
        model_type = ""

    base_kwargs: dict[str, Any] = {
        "batch_size": batch_size,
    }
    if model_type != "whisper":
        base_kwargs["chunk_length_s"] = chunk_length_s

    try:
        return asr_pipeline(str(audio_path), return_timestamps="word", **base_kwargs)
    except (TypeError, ValueError, RuntimeError, IndexError):
        pass

    try:
        return asr_pipeline(str(audio_path), return_timestamps=True, **base_kwargs)
    except (TypeError, ValueError, RuntimeError, IndexError):
        pass

    return asr_pipeline(str(audio_path), **base_kwargs)


def build_word_entries(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        token = str(chunk.get("text", "")).strip()
        timestamp = chunk.get("timestamp")
        start_sec: float | None = None
        end_sec: float | None = None
        if isinstance(timestamp, (list, tuple)) and len(timestamp) == 2:
            start_sec = to_float_or_none(timestamp[0])
            end_sec = to_float_or_none(timestamp[1])
        confidence = to_float_or_none(chunk.get("score"))
        words.append(
            {
                "index": index,
                "word": token,
                "normalized": normalize_token(token),
                "startSec": start_sec,
                "endSec": end_sec,
                "durationSec": (
                    round(end_sec - start_sec, 4)
                    if start_sec is not None and end_sec is not None and end_sec >= start_sec
                    else None
                ),
                "confidence": confidence,
            }
        )
    return words


def build_output_payload(
    audio_path: Path,
    asr_result: dict[str, Any],
    model: str,
    dialogue_entry: dict[str, Any] | None,
    line_id: int | None,
    character_name: str | None,
) -> dict[str, Any]:
    chunks = asr_result.get("chunks")
    chunk_list = chunks if isinstance(chunks, list) else []
    words = build_word_entries(chunk_list)
    covered = [item for item in words if item["startSec"]
               is not None and item["endSec"] is not None]
    line_character_id = dialogue_entry.get(
        "characterId") if isinstance(dialogue_entry, dict) else None
    source_text = dialogue_entry.get("text") if isinstance(
        dialogue_entry, dict) else None
    transcript_text = str(asr_result.get("text", "")).strip()
    engine_provider = str(asr_result.get("_engine_provider", "transformers"))

    payload: dict[str, Any] = {
        "formatVersion": "asr-word-timestamps/v1",
        "createdAt": utc_now_iso(),
        "engine": {
            "provider": engine_provider,
            "model": model,
            "task": "automatic-speech-recognition",
            "wordTimestamps": True,
        },
        "audio": {
            "fileName": audio_path.name,
            "audioPath": str(audio_path),
            "lineId": line_id,
            "characterNameFromFile": character_name,
            "characterIdFromDialogue": line_character_id,
        },
        "transcript": {
            "text": transcript_text,
            "language": asr_result.get("language"),
        },
        "words": words,
        "quality": {
            "wordCount": len(words),
            "timedWordCount": len(covered),
            "timingCoverageRatio": round((len(covered) / len(words)), 4) if words else 0,
        },
    }

    if isinstance(source_text, str):
        payload["validation"] = {
            "sourceText": source_text,
            "sourceWordCount": len(tokenize_for_compare(source_text)),
            "asrWordCount": len(tokenize_for_compare(transcript_text)),
            "textSimilarity": similarity_ratio(source_text, transcript_text),
        }

    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local ASR and save normalized word timestamp JSON files for OpenBook clips."
    )
    parser.add_argument("--audio-files", nargs="*", type=Path,
                        default=[], help="Specific audio files.")
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=None,
        help="Optional directory to recursively scan for audio files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write ASR JSON files (default: <audio-dir>/asr_words or audio file parent).",
    )
    parser.add_argument(
        "--model",
        default="distil-whisper/distil-small.en",
        help="ASR model ID or local path.",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="Inference device selection.",
    )
    parser.add_argument(
        "--torch-dtype",
        choices=["float16", "bfloat16", "float32"],
        default=None,
        help="Optional torch dtype override.",
    )
    parser.add_argument("--chunk-length-s", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--dialogue",
        type=Path,
        default=None,
        help="Optional dialogue.json for source text drift checks.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Only print planned work.")
    return parser


def resolve_output_dir(args: argparse.Namespace, audio_files: list[Path]) -> Path:
    if args.output_dir:
        return args.output_dir.resolve()
    if args.audio_dir:
        return (args.audio_dir.resolve() / "asr_words")
    if audio_files:
        return audio_files[0].parent / "asr_words"
    raise ValueError("No audio files found; cannot resolve output directory.")


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    audio_files = collect_audio_files(args.audio_files, args.audio_dir)
    if not audio_files:
        raise SystemExit(
            "No input audio files found. Provide --audio-files and/or --audio-dir.")

    output_dir = resolve_output_dir(args, audio_files)
    dialogue_map = load_dialogue_map(
        args.dialogue.resolve() if args.dialogue else None)

    print(f"Found {len(audio_files)} audio files")
    print(f"Output directory: {output_dir}")
    if args.dry_run:
        for item in audio_files:
            print(f"- {item}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    asr_pipeline = create_pipeline(args.model, args.device, args.torch_dtype)

    for audio_path in audio_files:
        line_id, character_name = parse_clip_name(audio_path)
        dialogue_entry = dialogue_map.get(
            line_id) if isinstance(line_id, int) else None
        print(f"Transcribing: {audio_path.name}")
        result = transcribe_with_words(
            asr_pipeline=asr_pipeline,
            audio_path=audio_path,
            chunk_length_s=args.chunk_length_s,
            batch_size=args.batch_size,
        )
        payload = build_output_payload(
            audio_path=audio_path,
            asr_result=result,
            model=args.model,
            dialogue_entry=dialogue_entry,
            line_id=line_id,
            character_name=character_name,
        )
        output_path = output_dir / f"{audio_path.stem}.asr.json"
        output_path.write_text(json.dumps(
            payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  -> {output_path}")


if __name__ == "__main__":
    main()
