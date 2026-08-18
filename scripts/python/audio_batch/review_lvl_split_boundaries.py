#!/usr/bin/env python3
"""Review and manually correct split boundaries in a lvl-audio batch.

The UI follows the existing character split-boundary reviewer: zoom the full
waveform, click a clip to inspect/play it, drag adjacent split markers, then
save all updated WAVs and metadata explicitly.
"""

from __future__ import annotations

import argparse
import json
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

DEFAULT_BATCH_ROOT = (
    Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources")
    / "Primal-Hunter"
    / "Book-18"
    / "_lvl_audio_batches"
)


@dataclass
class ClipRegion:
    line_id: int
    index: int
    text: str
    start_sec: float
    end_sec: float
    audio_path: Path
    validation: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def stop_audio() -> None:
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def play_wav(path: Path) -> None:
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


class LvlSplitBoundaryReviewApp:
    def __init__(self, root: tk.Tk, batch_dir: Path) -> None:
        self.root = root
        self.batch_dir = batch_dir.resolve()
        self.manifest_path = self.batch_dir / "audio_test_manifest.json"
        self.validation_path = self.batch_dir / "split_validation_report.json"
        self.manifest_payload = read_json(self.manifest_path)
        self.validation_payload = read_json(self.validation_path)
        self.full_audio_path = self._resolve_full_audio_path()
        self.regions = self._load_regions()
        self.original_regions = deepcopy(self.regions)
        self.full_audio, self.sample_rate = sf.read(
            str(self.full_audio_path), dtype="float32", always_2d=True
        )
        if self.full_audio.size == 0:
            raise ValueError(f"Full audio is empty: {self.full_audio_path}")
        self.wave_mono = self.full_audio.mean(axis=1)
        self.duration_sec = len(self.wave_mono) / float(self.sample_rate)
        self.wave_peak_cache: dict[int, np.ndarray] = {}

        self.root.title("Lvl Audio Split Boundary Reviewer")
        self.root.geometry("1520x900")
        self.status_var = tk.StringVar(
            value=f"Loaded {len(self.regions)} clips from {self.batch_dir.name}"
        )
        self.selection_var = tk.StringVar(value="No clip selected")
        self.autoplay_var = tk.BooleanVar(value=True)
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.min_clip_ms_var = tk.IntVar(value=90)
        self.selected_index: int | None = 0 if self.regions else None
        self.drag_boundary_index: int | None = None
        self.dirty = False

        self.playback_active = False
        self.playback_wave_sec: float | None = None
        self.playback_after_id: str | None = None
        self.playback_start_sec = 0.0
        self.playback_end_sec = 0.0
        self.playback_started_monotonic = 0.0
        self.preview_path = self.batch_dir / "_manual_split_preview.wav"

        self.wave_canvas: tk.Canvas | None = None
        self.info_header_label: tk.Label | None = None
        self.info_expected_text: ScrolledText | None = None
        self.info_validation_text: ScrolledText | None = None
        self._build_ui()
        self._sync_selection_text()
        self._redraw_waveform()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _resolve_full_audio_path(self) -> Path:
        full_audio = self.manifest_payload.get("fullAudio")
        if isinstance(full_audio, str) and Path(full_audio).exists():
            return Path(full_audio).resolve()
        candidates = sorted(self.batch_dir.glob("*-full.wav"))
        if candidates:
            return candidates[0].resolve()
        raise FileNotFoundError(f"No full audio WAV found in {self.batch_dir}")

    def _load_regions(self) -> list[ClipRegion]:
        manifest_lines = self.manifest_payload.get("lines")
        validation_lines = self.validation_payload.get("lines")
        if not isinstance(manifest_lines, list):
            raise ValueError(f"Invalid manifest (missing lines[]): {self.manifest_path}")
        validation_by_id = (
            {
                int(entry["lineId"]): entry
                for entry in validation_lines
                if isinstance(entry, dict) and isinstance(entry.get("lineId"), int)
            }
            if isinstance(validation_lines, list)
            else {}
        )
        regions: list[ClipRegion] = []
        for index, entry in enumerate(manifest_lines):
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), int):
                continue
            start_sec, end_sec, audio_path = (
                entry.get("startSec"),
                entry.get("endSec"),
                entry.get("audioPath"),
            )
            if (
                not isinstance(start_sec, (int, float))
                or not isinstance(end_sec, (int, float))
                or not isinstance(audio_path, str)
                or float(end_sec) <= float(start_sec)
            ):
                continue
            line_id = int(entry["id"])
            regions.append(
                ClipRegion(
                    line_id=line_id,
                    index=index,
                    text=str(entry.get("text") or ""),
                    start_sec=float(start_sec),
                    end_sec=float(end_sec),
                    audio_path=Path(audio_path).resolve(),
                    validation=validation_by_id.get(line_id, {}),
                )
            )
        if not regions:
            raise ValueError("No editable lines with startSec/endSec/audioPath were found.")
        return sorted(regions, key=lambda region: (region.start_sec, region.index))

    def _build_ui(self) -> None:
        header = tk.LabelFrame(self.root, text="Review", padx=10, pady=8)
        header.pack(fill="x", padx=10, pady=10)
        tk.Label(header, text=f"Batch: {self.batch_dir}", anchor="w").pack(fill="x")
        tk.Label(header, text=f"Full audio: {self.full_audio_path.name}", anchor="w").pack(fill="x")

        controls = tk.Frame(self.root)
        controls.pack(fill="x", padx=10, pady=(0, 8))
        tk.Button(controls, text="Stop Audio", command=lambda: self._stop_playback(False)).pack(side="left")
        tk.Button(controls, text="Play Selected", command=self._play_selected).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Prev Clip", command=lambda: self._step_selected(-1)).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Next Clip", command=lambda: self._step_selected(1)).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Save Changes", command=self._save_changes).pack(side="left", padx=(14, 0))
        tk.Checkbutton(controls, text="Autoplay on select", variable=self.autoplay_var).pack(side="left", padx=(14, 0))
        tk.Label(controls, text="Min clip (ms)").pack(side="left", padx=(14, 4))
        tk.Spinbox(controls, from_=40, to=600, increment=5, textvariable=self.min_clip_ms_var, width=6).pack(side="left")
        tk.Label(controls, text="Zoom").pack(side="left", padx=(14, 4))
        tk.Scale(controls, variable=self.zoom_var, from_=1.0, to=16.0, resolution=0.5, orient="horizontal", length=240, command=lambda _value: self._redraw_waveform()).pack(side="left")
        tk.Label(controls, textvariable=self.status_var, fg="#14532d").pack(side="right")

        info_frame = tk.LabelFrame(self.root, text="Selected Clip", padx=10, pady=8)
        info_frame.pack(fill="x", padx=10, pady=(0, 8))
        self.info_header_label = tk.Label(info_frame, text="No clip selected", font=("Segoe UI", 10, "bold"), anchor="w", justify="left")
        self.info_header_label.pack(fill="x")
        grid = tk.Frame(info_frame)
        grid.pack(fill="x", pady=(8, 0))
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        tk.Label(grid, text="Generated text", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(grid, text="Split validation", font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w")
        self.info_expected_text = ScrolledText(grid, height=5, wrap="word")
        self.info_expected_text.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.info_expected_text.configure(state="disabled")
        self.info_validation_text = ScrolledText(grid, height=5, wrap="word")
        self.info_validation_text.grid(row=1, column=1, sticky="nsew")
        self.info_validation_text.configure(state="disabled")

        panel = tk.LabelFrame(self.root, text="Full Batch Waveform (click a clip to select/play; drag amber boundaries)", padx=10, pady=10)
        panel.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.wave_canvas = tk.Canvas(panel, bg="#111827", height=360, highlightthickness=1, highlightbackground="#374151", xscrollincrement=1)
        self.wave_canvas.pack(fill="both", expand=True, side="top")
        self.wave_canvas.bind("<ButtonPress-1>", self._on_wave_press)
        self.wave_canvas.bind("<B1-Motion>", self._on_wave_drag)
        self.wave_canvas.bind("<ButtonRelease-1>", self._on_wave_release)
        self.wave_canvas.bind("<Configure>", lambda _event: self._redraw_waveform())
        scrollbar = tk.Scrollbar(panel, orient="horizontal", command=self.wave_canvas.xview)
        scrollbar.pack(fill="x", side="bottom")
        self.wave_canvas.configure(xscrollcommand=scrollbar.set)
        tk.Label(self.root, textvariable=self.selection_var, anchor="w").pack(fill="x", padx=10, pady=(0, 10))

    def _set_text(self, widget: ScrolledText | None, text: str) -> None:
        if widget is None:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _timeline_width_px(self) -> int:
        if self.wave_canvas is None:
            return 1200
        viewport_width = max(1200, self.wave_canvas.winfo_width())
        return int(max(viewport_width, viewport_width * float(self.zoom_var.get())))

    def _time_to_x(self, seconds: float) -> float:
        if self.duration_sec <= 0:
            return 0.0
        return max(0.0, min(float(self._timeline_width_px()), (seconds / self.duration_sec) * self._timeline_width_px()))

    def _x_to_time(self, x_value: float) -> float:
        if self.duration_sec <= 0:
            return 0.0
        return max(0.0, min(self.duration_sec, (x_value / self._timeline_width_px()) * self.duration_sec))

    def _boundaries(self) -> list[float]:
        return [self.regions[0].start_sec, *[region.end_sec for region in self.regions]]

    def _selected_region(self) -> ClipRegion | None:
        if self.selected_index is None or not 0 <= self.selected_index < len(self.regions):
            return None
        return self.regions[self.selected_index]

    def _segment_index_at_time(self, seconds: float) -> int | None:
        for index, region in enumerate(self.regions):
            if region.start_sec <= seconds <= region.end_sec:
                return index
        return None

    def _nearest_boundary_index(self, canvas_x: float, tolerance_px: float = 8.0) -> int | None:
        distances = [(abs(self._time_to_x(seconds) - canvas_x), index) for index, seconds in enumerate(self._boundaries())]
        if not distances:
            return None
        distance, index = min(distances)
        return index if distance <= tolerance_px else None

    def _clip_peaks(self, width: int) -> np.ndarray:
        cached = self.wave_peak_cache.get(width)
        if cached is not None:
            return cached
        edges = np.linspace(0, len(self.wave_mono), num=width + 1, dtype=np.int64)
        peaks = np.zeros(width, dtype=np.float32)
        absolute = np.abs(self.wave_mono)
        for index in range(width):
            start, end = int(edges[index]), int(edges[index + 1])
            if end > start:
                peaks[index] = float(np.max(absolute[start:end]))
        maximum = float(np.max(peaks))
        if maximum > 1e-8:
            peaks /= maximum
        self.wave_peak_cache[width] = peaks
        return peaks

    def _redraw_waveform(self) -> None:
        if self.wave_canvas is None:
            return
        canvas = self.wave_canvas
        canvas.delete("all")
        width, height = self._timeline_width_px(), max(120, canvas.winfo_height())
        midpoint = height / 2.0
        canvas.configure(scrollregion=(0, 0, width, height))
        canvas.create_rectangle(0, 0, width, height, fill="#111827", outline="")
        canvas.create_line(0, midpoint, width, midpoint, fill="#1f2937")
        peaks = self._clip_peaks(width)
        amplitude = height * 0.34
        top, bottom = [], []
        for x_value, peak in enumerate(peaks):
            top.extend([float(x_value), midpoint - peak * amplitude])
            bottom.extend([float(x_value), midpoint + peak * amplitude])
        if len(top) >= 4:
            canvas.create_line(*top, fill="#34d399")
            canvas.create_line(*bottom, fill="#34d399")
        for index, region in enumerate(self.regions):
            left, right = self._time_to_x(region.start_sec), self._time_to_x(region.end_sec)
            flagged = bool(region.validation.get("needsBoundaryAdjustment")) or str(region.validation.get("status", "")).lower() == "flagged"
            selected = index == self.selected_index
            fill, outline = ("#60a5fa", "#bfdbfe") if selected else (("#f97316", "#fed7aa") if flagged else ("#2563eb", "#93c5fd"))
            canvas.create_rectangle(left, 24, right, height - 24, fill=fill, outline=outline, stipple="gray50")
            if right - left >= 26:
                canvas.create_text(left + 4, 28, text=f"#{region.line_id}", fill="#f8fafc", font=("Segoe UI", 8, "bold"), anchor="nw")
        for index, seconds in enumerate(self._boundaries()):
            x_value = self._time_to_x(seconds)
            color = "#fbbf24" if index == self.drag_boundary_index else "#f59e0b"
            canvas.create_line(x_value, 0, x_value, height, fill=color, width=3 if index == self.drag_boundary_index else 2)
        if self.playback_wave_sec is not None:
            x_value = self._time_to_x(self.playback_wave_sec)
            color = "#ef4444" if self.playback_active else "#fca5a5"
            canvas.create_line(x_value, 0, x_value, height, fill=color, width=3 if self.playback_active else 2)
            canvas.create_text(min(width - 4, x_value + 4), 6, text=f"{self.playback_wave_sec:.2f}s", fill=color, anchor="nw", font=("Segoe UI", 8, "bold"))

    def _sync_selection_text(self) -> None:
        region = self._selected_region()
        if region is None:
            self.selection_var.set("No clip selected")
            self._set_text(self.info_expected_text, "")
            self._set_text(self.info_validation_text, "")
            return
        header = f"Clip {self.selected_index + 1}/{len(self.regions)} | lineId={region.line_id} | {region.start_sec:.4f}s -> {region.end_sec:.4f}s | duration={region.duration_sec:.4f}s"
        self.selection_var.set(header)
        if self.info_header_label is not None:
            self.info_header_label.config(text=header)
        self._set_text(self.info_expected_text, region.text)
        validation = region.validation
        self._set_text(self.info_validation_text, "\n".join([
            f"status: {validation.get('status', 'unknown')}",
            f"needsBoundaryAdjustment: {bool(validation.get('needsBoundaryAdjustment'))}",
            f"expectedSimilarity: {validation.get('expectedSimilarity', 'n/a')}",
            f"transcript: {validation.get('transcriptText', '')}",
        ]))

    def _on_wave_press(self, event: tk.Event) -> None:
        if self.wave_canvas is None:
            return
        canvas_x = float(self.wave_canvas.canvasx(event.x))
        self.drag_boundary_index = self._nearest_boundary_index(canvas_x)
        if self.drag_boundary_index is not None:
            if self.playback_active:
                self._stop_playback(False)
            self.selected_index = min(max(0, self.drag_boundary_index), len(self.regions) - 1)
            self._sync_selection_text()
            self._redraw_waveform()
            return
        index = self._segment_index_at_time(self._x_to_time(canvas_x))
        if index is None:
            return
        self.selected_index = index
        self._sync_selection_text()
        self._redraw_waveform()
        if self.autoplay_var.get():
            self._play_selected()

    def _on_wave_drag(self, event: tk.Event) -> None:
        if self.drag_boundary_index is None or self.wave_canvas is None:
            return
        proposed = self._x_to_time(float(self.wave_canvas.canvasx(event.x)))
        minimum = max(0.02, float(self.min_clip_ms_var.get()) / 1000.0)
        boundary = self.drag_boundary_index
        if boundary == 0:
            self.regions[0].start_sec = max(0.0, min(self.regions[0].end_sec - minimum, proposed))
        elif boundary == len(self.regions):
            self.regions[-1].end_sec = min(self.duration_sec, max(self.regions[-1].start_sec + minimum, proposed))
        else:
            left, right = self.regions[boundary - 1], self.regions[boundary]
            new_time = max(left.start_sec + minimum, min(right.end_sec - minimum, proposed))
            left.end_sec = new_time
            right.start_sec = new_time
        self.dirty = True
        self._sync_selection_text()
        self._redraw_waveform()

    def _on_wave_release(self, _event: tk.Event) -> None:
        self.drag_boundary_index = None
        self._redraw_waveform()

    def _cancel_playback_timer(self) -> None:
        if self.playback_after_id is not None:
            self.root.after_cancel(self.playback_after_id)
            self.playback_after_id = None

    def _stop_playback(self, clear_playhead: bool) -> None:
        stop_audio()
        self.playback_active = False
        self._cancel_playback_timer()
        if clear_playhead:
            self.playback_wave_sec = None
        self._redraw_waveform()

    def _playback_tick(self) -> None:
        self.playback_after_id = None
        if not self.playback_active:
            return
        current = self.playback_start_sec + (time.monotonic() - self.playback_started_monotonic)
        if current >= self.playback_end_sec:
            self.playback_wave_sec = self.playback_end_sec
            self.playback_active = False
            self._redraw_waveform()
            return
        self.playback_wave_sec = current
        self._redraw_waveform()
        self.playback_after_id = self.root.after(33, self._playback_tick)

    def _play_selected(self) -> None:
        region = self._selected_region()
        if region is None:
            return
        start = int(max(0, np.floor(region.start_sec * self.sample_rate)))
        end = int(min(len(self.full_audio), np.ceil(region.end_sec * self.sample_rate)))
        if end <= start:
            return
        sf.write(str(self.preview_path), self.full_audio[start:end], self.sample_rate)
        self._stop_playback(False)
        play_wav(self.preview_path)
        self.playback_active = True
        self.playback_start_sec = region.start_sec
        self.playback_end_sec = region.end_sec
        self.playback_started_monotonic = time.monotonic()
        self.playback_wave_sec = region.start_sec
        self.playback_after_id = self.root.after(33, self._playback_tick)
        self._redraw_waveform()

    def _step_selected(self, step: int) -> None:
        self.selected_index = 0 if self.selected_index is None else max(0, min(len(self.regions) - 1, self.selected_index + step))
        self._sync_selection_text()
        self._redraw_waveform()
        if self.autoplay_var.get():
            self._play_selected()

    def _save_changes(self) -> None:
        if not self.dirty:
            self.status_var.set("No pending boundary edits to save.")
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        manifest_backup = self.manifest_path.with_name(f"{self.manifest_path.name}.bak-{timestamp}")
        validation_backup = self.validation_path.with_name(f"{self.validation_path.name}.bak-{timestamp}")
        try:
            manifest_backup.write_text(self.manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
            validation_backup.write_text(self.validation_path.read_text(encoding="utf-8"), encoding="utf-8")
            manifest_lines = self.manifest_payload["lines"]
            validation_lines = self.validation_payload.get("lines", [])
            by_line_id = {entry.get("lineId"): entry for entry in validation_lines if isinstance(entry, dict)} if isinstance(validation_lines, list) else {}
            saved_at = datetime.now(timezone.utc).isoformat()
            for region in self.regions:
                start_sec, end_sec = round(region.start_sec, 4), round(region.end_sec, 4)
                start = int(max(0, np.floor(start_sec * self.sample_rate)))
                end = int(min(len(self.full_audio), np.ceil(end_sec * self.sample_rate)))
                if end <= start:
                    raise ValueError(f"Invalid sample range for line {region.line_id}")
                sf.write(str(region.audio_path), self.full_audio[start:end], self.sample_rate)
                manifest_line = manifest_lines[region.index]
                if isinstance(manifest_line, dict):
                    manifest_line.update({"startSec": start_sec, "endSec": end_sec, "durationSec": round(end_sec - start_sec, 4), "manuallyAdjustedAt": saved_at})
                validation_line = by_line_id.get(region.line_id)
                if isinstance(validation_line, dict):
                    validation_line.update({"audioPath": str(region.audio_path), "manualStartSec": start_sec, "manualEndSec": end_sec, "manualDuration": round(end_sec - start_sec, 4), "manuallyAdjustedAt": saved_at})
            self.manifest_path.write_text(json.dumps(self.manifest_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self.validation_path.write_text(json.dumps(self.validation_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Save failed", f"Could not persist changes:\n{exc}")
            return
        self.original_regions = deepcopy(self.regions)
        self.dirty = False
        self.status_var.set(f"Saved {len(self.regions)} clips. Backups: {manifest_backup.name}, {validation_backup.name}")

    def _on_close(self) -> None:
        if self.dirty and not messagebox.askyesno("Unsaved edits", "Discard unsaved boundary edits and close?"):
            return
        self._stop_playback(True)
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review and drag split boundaries for lvl-audio batches.")
    parser.add_argument("--batch-dir", type=Path, default=DEFAULT_BATCH_ROOT)
    return parser.parse_args()


def resolve_batch_dir(path: Path) -> Path:
    if (path / "audio_test_manifest.json").exists():
        return path.resolve()
    batches = sorted(candidate for candidate in path.glob("batch_*") if (candidate / "audio_test_manifest.json").exists())
    if batches:
        return batches[0].resolve()
    raise FileNotFoundError(f"Could not find a batch directory under {path}")


def main() -> int:
    args = parse_args()
    root = tk.Tk()
    app = LvlSplitBoundaryReviewApp(root, resolve_batch_dir(args.batch_dir.resolve()))
    _ = app
    root.mainloop()
    return 0


if __name__ == "__main__":
    main()
