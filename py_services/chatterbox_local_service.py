"""Local service wrapper for Chatterbox-Turbo TTS generation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional


def find_chatterbox_python() -> Path:
    """Locate python executable in chatterbox_env virtual environment."""
    candidates = [
        # Relative to project root
        Path(__file__).resolve().parents[1] / "chatterbox_env" / "Scripts" / "python.exe",
        Path(__file__).resolve().parents[1] / "chatterbox_env" / "bin" / "python",
        # Sibling directories
        Path(sys.prefix).parent / "chatterbox_env" / "Scripts" / "python.exe",
        Path(sys.prefix).parent / "chatterbox_env" / "bin" / "python",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c

    # Fallback to current python
    return Path(sys.executable)


class ChatterboxLocalService:
    """Chatterbox TTS service running in-process or via persistent worker."""

    def __init__(
        self,
        variant: str = "turbo",
        device: str = "cuda",
        temperature: float = 0.8,
        repetition_penalty: float = 1.2,
        python_executable: Optional[str | Path] = None,
    ) -> None:
        self.variant = variant.lower().strip()
        self.device = device
        self.temperature = temperature
        self.repetition_penalty = repetition_penalty
        self.python_executable = (
            Path(python_executable) if python_executable else find_chatterbox_python()
        )
        self.worker_process: Optional[subprocess.Popen] = None
        self._in_process_model = None
        self._cached_sample_path: Optional[str] = None
        self.stats: list[dict[str, Any]] = []

        self._init_backend()

    def _init_backend(self) -> None:
        # Only attempt in-process load if running directly inside chatterbox_env
        if "chatterbox_env" in sys.prefix:
            try:
                import torch
                if self.variant == "base":
                    from chatterbox.tts import ChatterboxTTS as ModelClass
                else:
                    from chatterbox.tts_turbo import ChatterboxTurboTTS as ModelClass

                actual_device = self.device
                if actual_device == "cuda" and not torch.cuda.is_available():
                    actual_device = "cpu"

                t0 = time.perf_counter()
                self._in_process_model = ModelClass.from_pretrained(device=actual_device)
                load_sec = time.perf_counter() - t0
                print(f"[ChatterboxLocalService] Loaded in-process ({self.variant}) on {actual_device} in {load_sec:.1f}s")
                return
            except Exception as exc:
                print(f"[ChatterboxLocalService] In-process load failed ({exc}), falling back to worker...")

        # Otherwise, launch worker process using chatterbox_env python
        worker_script = Path(__file__).resolve().parent / "chatterbox_worker.py"
        if not worker_script.exists():
            raise FileNotFoundError(f"Chatterbox worker script not found: {worker_script}")

        if not self.python_executable.exists():
            raise FileNotFoundError(
                f"Chatterbox python interpreter not found: {self.python_executable}. "
                "Please ensure chatterbox_env virtualenv exists."
            )

        cmd = [
            str(self.python_executable),
            str(worker_script),
            "--device",
            self.device,
            "--variant",
            self.variant,
        ]
        print(f"[ChatterboxLocalService] Spawning worker: {' '.join(cmd)}")
        self.worker_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,  # stream worker progress bars & logs to terminal
            text=True,
            bufsize=1,
            encoding="utf-8",
        )

        # Wait for ready signal from worker (skipping library print chatter)
        ready_payload = None
        while self.worker_process.poll() is None:
            line = self.worker_process.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.startswith("{"):
                try:
                    data = json.loads(line_str)
                    if data.get("status") == "ready":
                        ready_payload = data
                        break
                    elif data.get("status") == "error":
                        raise RuntimeError(f"Chatterbox worker failed to start: {data.get('error')}")
                except json.JSONDecodeError:
                    pass

        if ready_payload is None:
            raise RuntimeError(
                "Chatterbox worker exited without sending ready signal."
            )

        print(
            f"[ChatterboxLocalService] Worker ready on {ready_payload.get('device')} "
            f"(variant={ready_payload.get('variant')}, sample_rate={ready_payload.get('sr')}, loaded in {ready_payload.get('load_time_sec')}s)"
        )

    def generate_audio(
        self,
        text: str,
        sample_path: str,
        output_path: str,
        seed: Optional[int] = None,
    ) -> None:
        """Generate audio for text using sample_path as reference voice."""
        sample_resolved = str(Path(sample_path).resolve())
        out_resolved = str(Path(output_path).resolve())

        if self._in_process_model is not None:
            import torch
            import torchaudio as ta

            if seed is not None:
                torch.manual_seed(seed)
                if torch.cuda.is_available():
                    torch.cuda.manual_seed_all(seed)

            if self._cached_sample_path != sample_resolved or self._in_process_model.conds is None:
                self._in_process_model.prepare_conditionals(sample_resolved)
                self._cached_sample_path = sample_resolved

            t0 = time.perf_counter()
            wav = self._in_process_model.generate(
                text,
                repetition_penalty=self.repetition_penalty,
                temperature=self.temperature,
            )
            gen_sec = time.perf_counter() - t0
            dur_sec = float(wav.numel()) / float(self._in_process_model.sr)
            ta.save(out_resolved, wav, self._in_process_model.sr)
            self.stats.append({
                "duration_sec": dur_sec,
                "gen_time_sec": gen_sec,
                "rtf": gen_sec / dur_sec if dur_sec > 0 else 0,
            })
            return

        if self.worker_process is None or self.worker_process.poll() is not None:
            raise RuntimeError("Chatterbox worker process is not running.")

        req = {
            "action": "generate",
            "text": text,
            "sample_path": sample_resolved,
            "output_path": out_resolved,
            "seed": seed,
            "temperature": self.temperature,
            "repetition_penalty": self.repetition_penalty,
        }

        self.worker_process.stdin.write(json.dumps(req) + "\n")
        self.worker_process.stdin.flush()

        response = None
        while self.worker_process.poll() is None:
            line = self.worker_process.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.startswith("{"):
                try:
                    data = json.loads(line_str)
                    response = data
                    break
                except json.JSONDecodeError:
                    pass

        if response is None or response.get("status") != "ok":
            err = response.get("error") if response else "Unknown worker error"
            raise RuntimeError(f"Chatterbox generation failed: {err}")

        self.stats.append({
            "duration_sec": response.get("duration_sec", 0.0),
            "gen_time_sec": response.get("gen_time_sec", 0.0),
            "rtf": response.get("rtf", 0.0),
        })

    def close(self) -> None:
        """Shut down worker or clear in-process model to free GPU memory."""
        if self.worker_process is not None:
            try:
                self.worker_process.stdin.write(json.dumps({"action": "close"}) + "\n")
                self.worker_process.stdin.flush()
                self.worker_process.wait(timeout=5)
            except Exception:
                self.worker_process.kill()
            finally:
                self.worker_process = None
                print("[ChatterboxLocalService] Worker process terminated and GPU memory freed.")

        if self._in_process_model is not None:
            self._in_process_model = None
            try:
                import gc
                import torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    def __enter__(self) -> ChatterboxLocalService:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
