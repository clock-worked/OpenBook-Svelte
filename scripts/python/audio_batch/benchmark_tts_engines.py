"""Benchmark Chatterbox vs VibeVoice on a chapter dialogue file."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def get_gpu_memory_mb() -> float:
    """Query current GPU memory usage via nvidia-smi."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,nounits,noheader"],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(res.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0


def run_engine_pipeline(
    *,
    engine: str,
    chapter_dir: Path,
    sample_path: Path,
    manifest_path: Path,
    output_dir: Path,
    python_exe: Path,
    chatterbox_variant: str = "turbo",
    line_chunk_min_chars: int = 800,
    vibevoice_ddpm_steps: int = 20,
    seed: int | None = 42,
) -> dict[str, Any]:
    """Execute generate_and_split_chapter_narrator.py for a specific TTS engine."""
    output_dir.mkdir(parents=True, exist_ok=True)
    full_audio_path = output_dir / f"{chapter_dir.name}-{engine}-full.wav"

    script_path = (
        Path(__file__).resolve().parent / "generate_and_split_chapter_narrator.py"
    )

    cmd = [
        str(python_exe),
        "-u",  # unbuffered stdout
        str(script_path),
        "--chapter-dir", str(chapter_dir),
        "--sample-path", str(sample_path),
        "--manifest", str(manifest_path),
        "--output-dir", str(output_dir),
        "--full-audio", str(full_audio_path),
        "--tts-engine", engine,
        "--line-chunk-min-chars", str(line_chunk_min_chars),
    ]
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    if engine == "vibevoice":
        cmd.extend(["--vibevoice-ddpm-steps", str(vibevoice_ddpm_steps)])
    elif engine == "chatterbox":
        cmd.extend(["--chatterbox-variant", chatterbox_variant])

    print(f"\n{'='*70}")
    print(f"STARTING PIPELINE RUN: {engine.upper()}")
    print(f"Output directory: {output_dir}")
    print(f"Full audio path:  {full_audio_path}")
    print(f"Command:          {' '.join(cmd)}")
    print(f"{'='*70}\n")

    vram_before = get_gpu_memory_mb()
    t_start = time.perf_counter()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    peak_vram = vram_before
    log_lines: list[str] = []

    # Stream output and monitor VRAM
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        sys.stdout.write(line)
        sys.stdout.flush()
        log_lines.append(line)
        current_vram = get_gpu_memory_mb()
        if current_vram > peak_vram:
            peak_vram = current_vram

    proc.wait()
    total_elapsed = time.perf_counter() - t_start
    vram_after = get_gpu_memory_mb()

    if proc.returncode != 0:
        raise RuntimeError(
            f"Pipeline failed for engine '{engine}' with exit code {proc.returncode}"
        )

    # Read generated manifest and summary reports
    result_manifest_path = output_dir / "audio_test_manifest.json"
    if not result_manifest_path.exists():
        raise FileNotFoundError(f"Result manifest not found: {result_manifest_path}")

    manifest_data = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    summary = manifest_data.get("summary", {})
    gen_summary = summary.get("generation", {})
    val_summary = summary.get("splitValidation", {}) or {}

    # Read validation report if present
    validation_report_path = output_dir / "split_validation_report.json"
    val_reports: list[dict[str, Any]] = []
    if validation_report_path.exists():
        val_data = json.loads(validation_report_path.read_text(encoding="utf-8"))
        val_reports = val_data.get("reports", [])

    audio_dur = float(summary.get("audioDurationSec", 0.0))
    file_size_mb = (
        round(full_audio_path.stat().st_size / (1024 * 1024), 2)
        if full_audio_path.exists()
        else 0.0
    )

    # Compute detailed validation statistics
    sims = [
        float(r.get("initialSimilarity", r.get("similarity", 0.0)))
        for r in val_reports
        if "initialSimilarity" in r or "similarity" in r
    ]
    flagged_count = sum(1 for r in val_reports if r.get("flagged", False))
    perfect_matches = sum(1 for s in sims if s >= 0.999)

    # Extract chunk generation timing from generation report
    chunks = gen_summary.get("chunks", [])
    chunk_times = [
        float(c.get("durationSec", 0.0)) for c in chunks
    ]
    total_chunk_count = len(chunks)

    return {
        "engine": engine,
        "total_pipeline_sec": round(total_elapsed, 2),
        "audio_duration_sec": round(audio_dur, 2),
        "audio_duration_fmt": f"{int(audio_dur // 60):02d}:{int(audio_dur % 60):02d}",
        "audio_file_size_mb": file_size_mb,
        "full_audio_path": str(full_audio_path),
        "output_dir": str(output_dir),
        "line_count": summary.get("lineCount", len(manifest_data.get("lines", []))),
        "chunk_count": total_chunk_count,
        "vram_baseline_mb": round(vram_before, 1),
        "vram_peak_mb": round(peak_vram, 1),
        "vram_delta_mb": round(peak_vram - vram_before, 1),
        "generation_summary": gen_summary,
        "validation_stats": {
            "total_lines_scored": len(sims),
            "avg_similarity": round(sum(sims) / len(sims), 4) if sims else 0.0,
            "min_similarity": round(min(sims), 4) if sims else 0.0,
            "max_similarity": round(max(sims), 4) if sims else 0.0,
            "flagged_lines": flagged_count,
            "perfect_matches": perfect_matches,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Chatterbox vs VibeVoice on chapter audio generation."
    )
    parser.add_argument("--chapter-dir", type=Path, required=True)
    parser.add_argument("--sample-path", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument(
        "--engine",
        choices=["chatterbox", "vibevoice", "both"],
        default="both",
        help="Which engine(s) to benchmark.",
    )
    parser.add_argument(
        "--chatterbox-variant",
        choices=["turbo", "base"],
        default="turbo",
        help="Chatterbox variant to use: 'turbo' or 'base'.",
    )
    parser.add_argument("--vibevoice-ddpm-steps", type=int, default=20)
    parser.add_argument("--line-chunk-min-chars", type=int, default=800)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--report-json",
        type=Path,
        default=None,
        help="Optional path to save final comparative benchmark JSON.",
    )
    args = parser.parse_args()

    python_exe = Path(sys.executable)
    chapter_dir = args.chapter_dir.resolve()
    sample_path = args.sample_path.resolve()
    manifest_path = (args.manifest or (chapter_dir / "dialogue.json")).resolve()

    if not chapter_dir.exists():
        raise FileNotFoundError(f"Chapter directory not found: {chapter_dir}")
    if not sample_path.exists():
        raise FileNotFoundError(f"Voice sample not found: {sample_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    engines = (
        ["chatterbox", "vibevoice"]
        if args.engine == "both"
        else [args.engine]
    )

    results: dict[str, Any] = {}

    for eng in engines:
        out_dir = chapter_dir / f"audio_benchmark_{eng}"
        res = run_engine_pipeline(
            engine=eng,
            chapter_dir=chapter_dir,
            sample_path=sample_path,
            manifest_path=manifest_path,
            output_dir=out_dir,
            python_exe=python_exe,
            chatterbox_variant=args.chatterbox_variant,
            line_chunk_min_chars=args.line_chunk_min_chars,
            vibevoice_ddpm_steps=args.vibevoice_ddpm_steps,
            seed=args.seed,
        )
        results[eng] = res

    # Print comparative report
    print("\n" + "=" * 75)
    print("TTS BENCHMARK COMPARISON REPORT: CHAPTER 1387")
    print("=" * 75)

    headers = [
        "Metric",
        *(eng.upper() for eng in engines),
    ]
    rows = [
        ("Total Pipeline Time", *(f"{results[e]['total_pipeline_sec']:.1f}s" for e in engines)),
        ("Audio Duration", *(f"{results[e]['audio_duration_fmt']} ({results[e]['audio_duration_sec']:.1f}s)" for e in engines)),
        ("Overall Pipeline RTF", *(f"{results[e]['total_pipeline_sec'] / results[e]['audio_duration_sec']:.3f}" if results[e]['audio_duration_sec'] > 0 else "N/A" for e in engines)),
        ("Full Audio File Size", *(f"{results[e]['audio_file_size_mb']} MB" for e in engines)),
        ("Chunks / Lines Split", *(f"{results[e]['chunk_count']} chunks / {results[e]['line_count']} lines" for e in engines)),
        ("Peak VRAM Usage", *(f"{results[e]['vram_peak_mb']} MB" for e in engines)),
        ("ASR Avg Similarity", *(f"{results[e]['validation_stats']['avg_similarity']:.3f}" for e in engines)),
        ("ASR Min Similarity", *(f"{results[e]['validation_stats']['min_similarity']:.3f}" for e in engines)),
        ("ASR Perfect Matches", *(f"{results[e]['validation_stats']['perfect_matches']}/{results[e]['validation_stats']['total_lines_scored']}" for e in engines)),
        ("ASR Flagged Lines", *(f"{results[e]['validation_stats']['flagged_lines']}" for e in engines)),
    ]

    col_widths = [max(len(str(item)) for item in [h, *(r[i] for r in rows)]) + 2 for i, h in enumerate(headers)]
    header_str = "".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers))
    print(header_str)
    print("-" * len(header_str))
    for r in rows:
        print("".join(f"{str(cell):<{col_widths[i]}}" for i, cell in enumerate(r)))
    print("=" * 75)

    report_path = (
        args.report_json
        or (chapter_dir / "tts_benchmark_comparison_1387.json")
    )
    report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote full benchmark report to: {report_path}\n")


if __name__ == "__main__":
    main()
