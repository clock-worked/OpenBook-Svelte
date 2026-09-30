"""Execute a 3-way head-to-head TTS benchmark on Chapter 1387.

Compares:
  1. Chatterbox-Turbo (ResembleAI/chatterbox-turbo, fast 1-step flow matching)
  2. Chatterbox-Base  (ResembleAI/chatterbox, multi-step flow matching with CFG)
  3. VibeVoice-Large  (VibeVoice-Large-Q8, diffusion-based long-form TTS)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Ensure cuDNN bin is in PATH for ASR/ctranslate2 on Windows
CUDNN_BIN = Path(r"C:\Users\Chad\Documents\Code\JavaScript\OpenBook-svelte\.venv\Lib\site-packages\nvidia\cudnn\bin")
if CUDNN_BIN.exists():
    os.environ["PATH"] = str(CUDNN_BIN) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str(CUDNN_BIN))
        except OSError:
            pass

CHAPTER_DIR = Path(
    r"C:\Users\Chad\Documents\Code\Python\Useful-Scripts\Data\Resources\Primal-Hunter\Book-19\01 - Chapter 1387 - The Importance of Proper Penetration Technique"
)
SAMPLE_PATH = Path(
    r"C:\Users\Chad\Documents\Code\Python\Useful-Scripts\Data\Resources\Primal-Hunter\Audio Samples\narrator-b13-c1-37.wav"
)
MANIFEST_PATH = CHAPTER_DIR / "dialogue.json"
SCRIPT_PATH = Path(__file__).resolve().parent / "generate_and_split_chapter_narrator.py"
PYTHON_EXE = Path(sys.executable)

CONFIGS = [
    {
        "name": "Chatterbox-Turbo",
        "tag": "turbo",
        "tts_engine": "chatterbox",
        "chatterbox_variant": "turbo",
        "min_chars": 1,
        "max_chars": 450,
        "output_dir": CHAPTER_DIR / "audio_benchmark_turbo",
    },
    {
        "name": "Chatterbox-Base",
        "tag": "base",
        "tts_engine": "chatterbox",
        "chatterbox_variant": "base",
        "min_chars": 1,
        "max_chars": 450,
        "output_dir": CHAPTER_DIR / "audio_benchmark_base",
    },
    {
        "name": "VibeVoice-Large",
        "tag": "vibevoice",
        "tts_engine": "vibevoice",
        "chatterbox_variant": None,
        "min_chars": 800,
        "max_chars": None,
        "output_dir": CHAPTER_DIR / "audio_benchmark_vibevoice",
    },
]


def query_gpu_memory_mb() -> float:
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


def run_single_config(cfg: dict[str, Any]) -> dict[str, Any]:
    name = cfg["name"]
    out_dir: Path = cfg["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    full_audio = out_dir / f"chapter-1387-{cfg['tag']}-full.wav"

    cmd = [
        str(PYTHON_EXE),
        "-u",
        str(SCRIPT_PATH),
        "--chapter-dir", str(CHAPTER_DIR),
        "--sample-path", str(SAMPLE_PATH),
        "--manifest", str(MANIFEST_PATH),
        "--output-dir", str(out_dir),
        "--full-audio", str(full_audio),
        "--tts-engine", cfg["tts_engine"],
        "--line-chunk-min-chars", str(cfg["min_chars"]),
        "--seed", "42",
    ]
    if cfg.get("max_chars"):
        cmd.extend(["--line-chunk-max-chars", str(cfg["max_chars"])])
    if cfg.get("chatterbox_variant"):
        cmd.extend(["--chatterbox-variant", cfg["chatterbox_variant"]])
    if cfg["tts_engine"] == "vibevoice":
        cmd.extend(["--vibevoice-ddpm-steps", "20"])

    print("\n" + "=" * 80)
    print(f"STARTING BENCHMARK RUN: {name.upper()}")
    print(f"Output Directory: {out_dir}")
    print(f"Full Audio:       {full_audio}")
    print(f"Command:          {' '.join(cmd)}")
    print("=" * 80 + "\n", flush=True)

    vram_start = query_gpu_memory_mb()
    t_start = time.perf_counter()
    peak_vram = vram_start

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

    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        sys.stdout.write(line)
        sys.stdout.flush()
        curr_mem = query_gpu_memory_mb()
        if curr_mem > peak_vram:
            peak_vram = curr_mem

    proc.wait()
    total_elapsed = time.perf_counter() - t_start

    if proc.returncode != 0:
        print(f"ERROR: {name} exited with code {proc.returncode}", flush=True)
        return {
            "name": name,
            "status": "failed",
            "exit_code": proc.returncode,
            "total_elapsed_sec": round(total_elapsed, 2),
        }

    # Load results
    manifest_file = out_dir / "audio_test_manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {}
    summary = manifest_data.get("summary", {})
    gen_sum = summary.get("generation", {})
    audio_dur = float(summary.get("audioDurationSec", 0.0))

    val_file = out_dir / "split_validation_report.json"
    val_data = json.loads(val_file.read_text(encoding="utf-8")) if val_file.exists() else {}
    reports = val_data.get("reports", [])

    sims = [
        float(r.get("initialSimilarity", r.get("similarity", 0.0)))
        for r in reports
        if "initialSimilarity" in r or "similarity" in r
    ]

    file_size_mb = round(full_audio.stat().st_size / (1024 * 1024), 2) if full_audio.exists() else 0.0

    return {
        "name": name,
        "status": "success",
        "tag": cfg["tag"],
        "total_elapsed_sec": round(total_elapsed, 2),
        "total_elapsed_fmt": f"{int(total_elapsed // 60):02d}:{int(total_elapsed % 60):02d}",
        "audio_duration_sec": round(audio_dur, 2),
        "audio_duration_fmt": f"{int(audio_dur // 60):02d}:{int(audio_dur % 60):02d}",
        "rtf": round(total_elapsed / audio_dur, 3) if audio_dur > 0 else 0.0,
        "audio_file_size_mb": file_size_mb,
        "chunks_count": gen_sum.get("chunkCount", len(gen_sum.get("chunks", []))),
        "lines_count": summary.get("lineCount", len(manifest_data.get("lines", []))),
        "vram_peak_mb": round(peak_vram, 1),
        "full_audio_path": str(full_audio),
        "output_dir": str(out_dir),
        "validation": {
            "scored_lines": len(sims),
            "avg_similarity": round(sum(sims) / len(sims), 4) if sims else 0.0,
            "min_similarity": round(min(sims), 4) if sims else 0.0,
            "perfect_matches": sum(1 for s in sims if s >= 0.999),
            "flagged_lines": sum(1 for r in reports if r.get("flagged", False)),
        },
    }


def main() -> None:
    results: list[dict[str, Any]] = []

    for cfg in CONFIGS:
        res = run_single_config(cfg)
        results.append(res)
        # Small cooldown between runs to release memory
        time.sleep(3)

    print("\n" + "=" * 80)
    print("3-WAY TTS BENCHMARK COMPARISON: CHAPTER 1387")
    print("=" * 80)

    for r in results:
        if r.get("status") != "success":
            print(f"[-] {r['name']}: FAILED (code {r.get('exit_code')}) in {r.get('total_elapsed_sec')}s")
            continue
        print(f"\n[{r['name']}]")
        print(f"  • Total Pipeline Time:  {r['total_elapsed_fmt']} ({r['total_elapsed_sec']} s)")
        print(f"  • Audio Duration:       {r['audio_duration_fmt']} ({r['audio_duration_sec']} s)")
        print(f"  • Real-Time Factor:     {r['rtf']}x (lower = faster)")
        print(f"  • File Size / Chunks:   {r['audio_file_size_mb']} MB across {r['chunks_count']} chunks ({r['lines_count']} lines)")
        print(f"  • Peak VRAM:            {r['vram_peak_mb']} MB")
        val = r.get("validation", {})
        print(f"  • ASR Avg Similarity:  {val.get('avg_similarity'):.3f} (Min: {val.get('min_similarity'):.3f})")
        print(f"  • ASR Flagged Lines:   {val.get('flagged_lines')} / {val.get('scored_lines')} (Perfect: {val.get('perfect_matches')})")

    report_json = CHAPTER_DIR / "tts_3way_benchmark_report.json"
    report_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote full 3-way report JSON to: {report_json}\n", flush=True)


if __name__ == "__main__":
    main()
