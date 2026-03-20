"""Review Book-1 audio clips with WhisperX and flag mismatches for manual review."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

from _path_setup import bootstrap_python_script_paths

PATHS = bootstrap_python_script_paths(__file__)

from py_services.chapter_audio_test.validation import create_split_validation_transcriber
from scripts.python.asr_validation.asr_chapter_audio_audit import discover_chapter_dirs, run_chapter_audit

try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

    def tqdm(iterable, **_kwargs):
        """Fallback iterator when tqdm is unavailable."""
        return iterable


def utc_now_iso() -> str:
    """Return current UTC time in stable ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_WINDOWS_DLL_HANDLES: list[Any] = []


def _ensure_windows_cudnn_runtime() -> None:
    """Register common cuDNN DLL folders on Windows for WhisperX CUDA runs."""
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
        Path(sys.base_prefix)
        / "Lib"
        / "site-packages"
        / "nvidia"
        / "cudnn"
        / "bin",
        PATHS["repoRoot"]
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


class WhisperXAuditPipeline:
    """Minimal ASR pipeline adapter expected by chapter audit helpers."""

    def __init__(self, transcribe_clip: Any) -> None:
        self._transcribe_clip = transcribe_clip

    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        """Transcribe one clip and return normalized text payload."""
        result = self._transcribe_clip(audio_path)
        if not isinstance(result, dict):
            return {"text": ""}
        return {"text": str(result.get("text", "")).strip()}


def parse_args() -> argparse.Namespace:
    """Parse CLI args for the Book-1 WhisperX review run."""
    parser = argparse.ArgumentParser(
        description=(
            "Run a WhisperX review across Book-1 audio clips, compare against dialogue.json, "
            "and flag clips that diverge for manual review."
        )
    )
    parser.add_argument("--book-dir", type=Path, required=True,
                        help="Book directory containing chapter folders.")
    parser.add_argument(
        "--chapter-regex",
        default=None,
        help="Optional regex to include only matching chapter folder names.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: <book-dir>/asr_audit_book_whisperx).",
    )
    parser.add_argument(
        "--mismatch-threshold",
        type=float,
        default=0.74,
        help="Expected-line similarity below this threshold is flagged.",
    )
    parser.add_argument(
        "--min-wrong-line-delta",
        type=float,
        default=0.08,
        help=(
            "Best non-expected line similarity must exceed expected similarity by this "
            "delta to mark likely wrong-line audio."
        ),
    )
    parser.add_argument("--top-k", type=int, default=3,
                        help="Top candidate line matches to keep per clip.")
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="WhisperX device selection.",
    )
    parser.add_argument(
        "--compute-type",
        choices=["auto", "float16", "int8", "int8_float16", "float32"],
        default="auto",
        help="WhisperX compute type.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="WhisperX batch size (0 = auto, 32 on CUDA or 8 on CPU).",
    )
    parser.add_argument(
        "--vad-method",
        choices=["silero", "pyannote"],
        default="silero",
        help="WhisperX VAD method.",
    )
    parser.add_argument(
        "--quiet",
        dest="quiet",
        action="store_true",
        default=True,
        help="Reduce third-party warning/log noise (default: enabled).",
    )
    parser.add_argument(
        "--no-quiet",
        dest="quiet",
        action="store_false",
        help="Show WhisperX/library logs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate structure and emit missing/unparseable reports without ASR inference.",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help=(
            "Resume from previous output by skipping chapters that already have "
            "chapter_asr_audit.json (default: enabled)."
        ),
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Disable resume behavior and reprocess all discovered chapters.",
    )
    return parser.parse_args()


def _float_or_default(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_regen_row(
    chapter_name: str,
    chapter_result: dict[str, Any],
    regen_item: dict[str, Any],
) -> dict[str, Any]:
    """Build one flat TSV row from a regenerate entry and its clip context."""
    line_id = regen_item.get("lineId")
    audio_path = regen_item.get("audioPath")
    clips = chapter_result.get("clips") if isinstance(
        chapter_result.get("clips"), list) else []

    matched_clip: dict[str, Any] | None = None
    if isinstance(audio_path, str) and audio_path:
        for clip in clips:
            if isinstance(clip, dict) and str(clip.get("audioPath")) == audio_path:
                matched_clip = clip
                break

    if matched_clip is None and isinstance(line_id, int):
        for clip in clips:
            if isinstance(clip, dict) and clip.get("lineIdFromFile") == line_id:
                matched_clip = clip
                break

    expected: dict[str, Any] = {}
    asr: dict[str, Any] = {}
    best_non_expected: dict[str, Any] = {}

    if isinstance(matched_clip, dict):
        expected = matched_clip.get("expected") if isinstance(
            matched_clip.get("expected"), dict) else {}
        asr = matched_clip.get("asr") if isinstance(
            matched_clip.get("asr"), dict) else {}
        best_non_expected = matched_clip.get("bestNonExpected") if isinstance(
            matched_clip.get("bestNonExpected"), dict) else {}

    if not best_non_expected:
        best_non_expected = regen_item.get("bestNonExpected") if isinstance(
            regen_item.get("bestNonExpected"), dict) else {}

    reasons = regen_item.get("reasons") if isinstance(
        regen_item.get("reasons"), list) else []

    return {
        "chapter": chapter_name,
        "lineId": line_id,
        "status": str(regen_item.get("status", "unknown")),
        "similarity": round(_float_or_default(expected.get("similarity"), 0.0), 4),
        "bestNonExpectedLineId": best_non_expected.get("lineId"),
        "bestNonExpectedScore": round(_float_or_default(best_non_expected.get("score"), 0.0), 4),
        "autoSuggestedTargetLineId": regen_item.get("autoSuggestedTargetLineId"),
        "audioPath": str(audio_path) if audio_path else "",
        "expectedText": str(expected.get("text") or regen_item.get("expectedText") or ""),
        "asrText": str(asr.get("text") or ""),
        "reasons": " | ".join(str(item) for item in reasons),
    }


def build_flagged_rows(chapter_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect and sort all flagged rows across chapter results."""
    rows: list[dict[str, Any]] = []
    for chapter_result in chapter_results:
        chapter_dir = Path(str(chapter_result.get("chapterDir", "")))
        chapter_name = chapter_dir.name or str(chapter_result.get("chapterDir", ""))
        regenerate = chapter_result.get("regenerate") if isinstance(
            chapter_result.get("regenerate"), list) else []

        for regen_item in regenerate:
            if not isinstance(regen_item, dict):
                continue
            rows.append(_extract_regen_row(chapter_name, chapter_result, regen_item))

    rows.sort(
        key=lambda item: (
            str(item.get("chapter", "")).lower(),
            int(item.get("lineId") or -1),
        )
    )
    return rows


def write_flagged_tsv(tsv_path: Path, rows: list[dict[str, Any]]) -> None:
    """Write flat flagged-review rows to a TSV artifact."""
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "chapter",
        "lineId",
        "status",
        "similarity",
        "bestNonExpectedLineId",
        "bestNonExpectedScore",
        "autoSuggestedTargetLineId",
        "audioPath",
        "expectedText",
        "asrText",
        "reasons",
    ]
    with tsv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _load_existing_chapter_report(chapter_audit_path: Path) -> dict[str, Any] | None:
    """Load an existing chapter audit report for resume mode."""
    try:
        payload = json.loads(chapter_audit_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _build_regenerate_entries(chapter_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach chapter path metadata to regenerate rows for consolidated output."""
    chapter_dir = str(chapter_result.get("chapterDir") or "")
    regenerate = chapter_result.get("regenerate") if isinstance(
        chapter_result.get("regenerate"), list) else []

    output: list[dict[str, Any]] = []
    for item in regenerate:
        if not isinstance(item, dict):
            continue
        with_chapter = dict(item)
        with_chapter["chapterDir"] = chapter_dir
        output.append(with_chapter)
    return output


def main() -> None:
    """Execute full-book WhisperX mismatch review and emit artifacts."""
    args = parse_args()

    book_dir = args.book_dir.resolve()
    if not book_dir.exists() or not book_dir.is_dir():
        raise SystemExit(f"book dir not found: {book_dir}")

    discovered_chapter_dirs = discover_chapter_dirs(book_dir, args.chapter_regex)
    if not discovered_chapter_dirs:
        raise SystemExit("No chapter folders found with dialogue.json + audio_lines")

    base_output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else (book_dir / "asr_audit_book_whisperx").resolve()
    )
    base_output_dir.mkdir(parents=True, exist_ok=True)

    pending_chapter_dirs: list[Path] = []
    result_by_chapter_name: dict[str, dict[str, Any]] = {}
    resumed_chapter_count = 0

    for chapter_dir in discovered_chapter_dirs:
        chapter_output_dir = (base_output_dir / chapter_dir.name).resolve()
        chapter_audit_path = chapter_output_dir / "chapter_asr_audit.json"

        if args.resume and chapter_audit_path.exists():
            existing = _load_existing_chapter_report(chapter_audit_path)
            if existing is not None:
                existing.setdefault("chapterDir", str(chapter_dir))
                result_by_chapter_name[chapter_dir.name] = existing
                resumed_chapter_count += 1
                continue

            print(
                f"[resume] Could not read existing report; reprocessing: {chapter_audit_path}"
            )

        pending_chapter_dirs.append(chapter_dir)

    engine_payload: dict[str, Any] = {
        "engine": "whisperx",
        "model": "small.en",
        "device": args.device,
        "computeType": args.compute_type,
        "batchSize": args.batch_size,
        "vadMethod": args.vad_method,
        "alignment": False,
    }

    print(f"Book: {book_dir}")
    print(f"Chapters discovered: {len(discovered_chapter_dirs)}")
    print(f"Output: {base_output_dir}")
    if args.resume:
        print(f"Resume: skipping {resumed_chapter_count} completed chapter(s)")
    print(f"Chapters pending this run: {len(pending_chapter_dirs)}")

    asr_pipeline: Any = None
    if pending_chapter_dirs and not args.dry_run:
        _ensure_windows_cudnn_runtime()
        engine_payload, transcribe_clip = create_split_validation_transcriber(
            validation_engine="whisperx",
            main_engine="whisperx",
            device=args.device,
            compute_type=args.compute_type,
            batch_size=args.batch_size,
            vad_method=args.vad_method,
            quiet=args.quiet,
            trim_leading_silence=False,
            silence_db=-40.0,
        )
        asr_pipeline = WhisperXAuditPipeline(transcribe_clip)

    if pending_chapter_dirs:
        chapter_iterator = tqdm(
            pending_chapter_dirs,
            total=len(pending_chapter_dirs),
            desc="Book-1 WhisperX review",
            unit="chapter",
            dynamic_ncols=True,
            leave=True,
        )

        for chapter_dir in chapter_iterator:
            if HAS_TQDM:
                chapter_iterator.set_postfix_str(chapter_dir.name)

            chapter_output_dir = (base_output_dir / chapter_dir.name).resolve()

            selected_batch_size = int(
                engine_payload.get(
                    "batchSize",
                    args.batch_size if args.batch_size > 0 else 0,
                )
            )

            chapter_result = run_chapter_audit(
                chapter_dir=chapter_dir,
                output_dir=chapter_output_dir,
                model="small.en",
                device=str(engine_payload.get("device", args.device)),
                torch_dtype=str(engine_payload.get("computeType", args.compute_type)),
                chunk_length_s=0,
                batch_size=selected_batch_size,
                mismatch_threshold=args.mismatch_threshold,
                min_wrong_line_delta=args.min_wrong_line_delta,
                top_k=args.top_k,
                dry_run=args.dry_run,
                asr_pipeline=asr_pipeline,
            )
            result_by_chapter_name[chapter_dir.name] = chapter_result

    ordered_results: list[dict[str, Any]] = []
    for chapter_dir in discovered_chapter_dirs:
        chapter_result = result_by_chapter_name.get(chapter_dir.name)
        if chapter_result is not None:
            ordered_results.append(chapter_result)

    all_regenerate: list[dict[str, Any]] = []
    for chapter_result in ordered_results:
        all_regenerate.extend(_build_regenerate_entries(chapter_result))

    selected_batch_size = int(
        engine_payload.get(
            "batchSize",
            args.batch_size if args.batch_size > 0 else 0,
        )
    )

    consolidated = {
        "createdAt": utc_now_iso(),
        "bookDir": str(book_dir),
        "chapterCount": len(ordered_results),
        "engine": {
            "task": "automatic-speech-recognition",
            "model": str(engine_payload.get("model", "small.en")),
            "engine": str(engine_payload.get("engine", "whisperx")),
            "device": str(engine_payload.get("device", args.device)),
            "computeType": str(engine_payload.get("computeType", args.compute_type)),
            "batchSize": selected_batch_size,
            "vadMethod": str(engine_payload.get("vadMethod", args.vad_method)),
            "alignment": bool(engine_payload.get("alignment", False)),
        },
        "thresholds": {
            "mismatchThreshold": args.mismatch_threshold,
            "minWrongLineDelta": args.min_wrong_line_delta,
        },
        "counts": {
            "markedForRegenerate": len(all_regenerate),
        },
        "chapters": ordered_results,
        "regenerate": all_regenerate,
    }

    consolidated_path = base_output_dir / "book_asr_audit.json"
    consolidated_manifest_path = base_output_dir / "book_regenerate_manifest.json"
    consolidated_text_path = base_output_dir / "book_regenerate_line_ids.txt"
    flagged_tsv_path = base_output_dir / "book_flagged_review.tsv"

    consolidated_path.write_text(
        json.dumps(consolidated, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    consolidated_manifest_path.write_text(
        json.dumps({"regenerate": all_regenerate}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    unique_pairs = sorted(
        {
            f"{item.get('chapterDir')}::{item.get('lineId')}"
            for item in all_regenerate
            if isinstance(item.get("lineId"), int)
        }
    )
    consolidated_text_path.write_text("\n".join(unique_pairs), encoding="utf-8")

    flagged_rows = build_flagged_rows(ordered_results)
    write_flagged_tsv(flagged_tsv_path, flagged_rows)

    print(f"Book audit report: {consolidated_path}")
    print(f"Book regenerate manifest: {consolidated_manifest_path}")
    print(f"Book regenerate list: {consolidated_text_path}")
    print(f"Book flagged TSV: {flagged_tsv_path}")


if __name__ == "__main__":
    main()
