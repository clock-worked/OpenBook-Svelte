"""Persistent worker for Chatterbox-Turbo TTS generation over JSON-lines IPC."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Chatterbox TTS worker process.")
    parser.add_argument("--device", default="cuda", help="Target device (cuda or cpu)")
    parser.add_argument(
        "--variant",
        choices=["turbo", "base"],
        default="turbo",
        help="Model variant: 'turbo' (ChatterboxTurboTTS) or 'base' (ChatterboxTTS)",
    )
    args = parser.parse_args()

    # Preserve original stdout for IPC responses, redirect general stdout to stderr
    ipc_out = sys.stdout
    sys.stdout = sys.stderr

    try:
        import torch
        import torchaudio as ta
        if args.variant == "base":
            from chatterbox.tts import ChatterboxTTS as ModelClass
        else:
            from chatterbox.tts_turbo import ChatterboxTurboTTS as ModelClass
    except Exception as exc:
        ipc_out.write(json.dumps({"status": "error", "error": f"Import failed: {exc}"}) + "\n")
        ipc_out.flush()
        sys.exit(1)

    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"

    try:
        t0 = time.perf_counter()
        model = ModelClass.from_pretrained(device=device)
        load_time = time.perf_counter() - t0
        cached_sample_path: str | None = None

        ipc_out.write(
            json.dumps({
                "status": "ready",
                "variant": args.variant,
                "device": str(model.device),
                "sr": model.sr,
                "load_time_sec": round(load_time, 3),
            })
            + "\n"
        )
        ipc_out.flush()
    except Exception as exc:
        ipc_out.write(json.dumps({"status": "error", "error": f"Model load failed: {exc}"}) + "\n")
        ipc_out.flush()
        sys.exit(1)

    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue

        try:
            req = json.loads(raw)
        except Exception as exc:
            ipc_out.write(json.dumps({"status": "error", "error": f"Malformed JSON: {exc}"}) + "\n")
            ipc_out.flush()
            continue

        action = req.get("action")
        if action in ("close", "exit", "quit"):
            ipc_out.write(json.dumps({"status": "closed"}) + "\n")
            ipc_out.flush()
            break

        if action == "ping":
            ipc_out.write(json.dumps({"status": "pong"}) + "\n")
            ipc_out.flush()
            continue

        if action == "generate":
            text = str(req.get("text", "")).strip()
            sample_path = str(req.get("sample_path", "")).strip()
            output_path = str(req.get("output_path", "")).strip()
            seed = req.get("seed")
            temperature = float(req.get("temperature", 0.8))
            repetition_penalty = float(req.get("repetition_penalty", 1.2))

            if not text:
                ipc_out.write(json.dumps({"status": "error", "error": "Empty text"}) + "\n")
                ipc_out.flush()
                continue

            if not sample_path or not os.path.exists(sample_path):
                ipc_out.write(
                    json.dumps({"status": "error", "error": f"Sample not found: {sample_path}"})
                    + "\n"
                )
                ipc_out.flush()
                continue

            try:
                if seed is not None:
                    torch.manual_seed(int(seed))
                    if torch.cuda.is_available():
                        torch.cuda.manual_seed_all(int(seed))

                # Cache conditionals if sample hasn't changed
                if cached_sample_path != sample_path or model.conds is None:
                    model.prepare_conditionals(sample_path)
                    cached_sample_path = sample_path

                t_start = time.perf_counter()
                gen_kwargs = {
                    "repetition_penalty": repetition_penalty,
                    "temperature": temperature,
                }
                if args.variant == "base":
                    if "exaggeration" in req:
                        gen_kwargs["exaggeration"] = float(req["exaggeration"])
                    if "cfg_weight" in req:
                        gen_kwargs["cfg_weight"] = float(req["cfg_weight"])

                wav = model.generate(text, **gen_kwargs)
                gen_sec = time.perf_counter() - t_start

                out_p = Path(output_path)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                ta.save(str(out_p), wav, model.sr)

                dur_sec = float(wav.numel()) / float(model.sr)
                rtf = gen_sec / dur_sec if dur_sec > 0 else 0.0

                ipc_out.write(
                    json.dumps({
                        "status": "ok",
                        "duration_sec": round(dur_sec, 4),
                        "gen_time_sec": round(gen_sec, 4),
                        "rtf": round(rtf, 4),
                        "sample_rate": model.sr,
                        "output_path": str(out_p),
                    })
                    + "\n"
                )
                ipc_out.flush()
            except Exception as exc:
                ipc_out.write(
                    json.dumps({"status": "error", "error": f"Generation failed: {exc}"}) + "\n"
                )
                ipc_out.flush()
        else:
            ipc_out.write(json.dumps({"status": "error", "error": f"Unknown action: {action}"}) + "\n")
            ipc_out.flush()


if __name__ == "__main__":
    main()
