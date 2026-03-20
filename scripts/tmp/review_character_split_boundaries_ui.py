#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


DEFAULT_BOOK_DIR = Path(
    "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/A-Practical-Guide-To-Evil/Book-1"
)


def chapter_sort_key(chapter_name: str) -> tuple[int, str]:
    if len(chapter_name) >= 2 and chapter_name[:2].isdigit():
        return int(chapter_name[:2]), chapter_name
    return 999, chapter_name


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stop_audio() -> None:
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def play_wav(path: Path | None) -> None:
    if path is None:
        return
    if not path.exists() or path.suffix.lower() != ".wav":
        return

    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


@dataclass
class ClipRegion:
    line_id: int
    clip_index: int
    text: str
    audio_file: str
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


@dataclass
class CharacterContext:
    chapter_name: str
    chapter_dir: Path
    character_name: str
    character_dir: Path
    manifest_path: Path
    full_audio_path: Path
    dialogue_path: Path | None
    split_validation_path: Path | None
    manifest_payload: dict[str, Any]
    regions: list[ClipRegion]
    dialogue_by_id: dict[int, dict[str, Any]]
    validation_by_id: dict[int, dict[str, Any]]


class SplitBoundaryReviewApp:
    def __init__(self, root: tk.Tk, *, book_dir: Path) -> None:
        self.root = root
        self.book_dir = book_dir.resolve()

        self.root.title("Character Split Boundary Reviewer")
        self.root.geometry("1520x900")

        self.chapter_var = tk.StringVar(value="")
        self.character_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Select chapter + character, then click Start.")
        self.selection_var = tk.StringVar(value="No clip selected")
        self.autoplay_var = tk.BooleanVar(value=True)
        self.selected_clip_edge_only_var = tk.BooleanVar(value=False)
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.min_clip_ms_var = tk.IntVar(value=90)

        self.context: CharacterContext | None = None
        self.original_regions: list[ClipRegion] = []
        self.dirty = False

        self.sample_rate: int = 0
        self.full_audio_2d: np.ndarray | None = None
        self.wave_mono: np.ndarray | None = None
        self.audio_duration_sec: float = 0.0
        self.wave_peak_cache: dict[int, np.ndarray] = {}

        self.selected_region_index: int | None = None
        self.drag_boundary_index: int | None = None
        self.drag_selected_edge: str | None = None

        self.playback_active = False
        self.playback_wave_sec: float | None = None
        self.playback_after_id: str | None = None
        self.playback_region_index: int | None = None
        self.playback_region_start_sec = 0.0
        self.playback_region_end_sec = 0.0
        self.playback_seek_offset_sec = 0.0
        self.playback_started_monotonic = 0.0

        self.preview_path: Path | None = None

        self.chapter_dirs: dict[str, Path] = {}
        self.character_dirs: dict[str, Path] = {}

        self.wave_canvas: tk.Canvas | None = None
        self.wave_scroll_x: tk.Scrollbar | None = None
        self.chapter_combo: ttk.Combobox | None = None
        self.character_combo: ttk.Combobox | None = None
        self.info_header_label: tk.Label | None = None
        self.info_expected_text: ScrolledText | None = None
        self.info_dialogue_text: ScrolledText | None = None
        self.info_validation_text: ScrolledText | None = None

        self._build_ui()
        self._refresh_chapters()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        self._stop_playback(clear_playhead=True)
        self.root.destroy()

    def _build_ui(self) -> None:
        picker = tk.LabelFrame(self.root, text="Start Review", padx=10, pady=10)
        picker.pack(fill="x", padx=10, pady=10)

        tk.Label(picker, text="Chapter").grid(row=0, column=0, sticky="w")
        self.chapter_combo = ttk.Combobox(
            picker,
            textvariable=self.chapter_var,
            state="readonly",
            width=56,
        )
        self.chapter_combo.grid(row=1, column=0, sticky="w", padx=(0, 12))
        self.chapter_combo.bind("<<ComboboxSelected>>", lambda _evt: self._on_chapter_selected())

        tk.Label(picker, text="Character").grid(row=0, column=1, sticky="w")
        self.character_combo = ttk.Combobox(
            picker,
            textvariable=self.character_var,
            state="readonly",
            width=40,
        )
        self.character_combo.grid(row=1, column=1, sticky="w", padx=(0, 12))

        tk.Button(picker, text="Start", command=self._start_selected).grid(row=1, column=2, padx=(0, 8))
        tk.Button(picker, text="Reload Choices", command=self._refresh_chapters).grid(row=1, column=3, padx=(0, 8))
        tk.Button(picker, text="Reload Current", command=self._reload_current).grid(row=1, column=4, padx=(0, 8))
        tk.Button(picker, text="Save Changes", command=self._save_changes).grid(row=1, column=5)

        for col in range(6):
            picker.grid_columnconfigure(col, weight=0)

        controls = tk.Frame(self.root)
        controls.pack(fill="x", padx=10, pady=(0, 8))

        tk.Button(
            controls,
            text="Stop Audio",
            command=lambda: self._stop_playback(clear_playhead=False),
        ).pack(side="left")
        tk.Button(controls, text="Play Selected", command=self._play_selected).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Prev Clip", command=lambda: self._step_selected(-1)).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Next Clip", command=lambda: self._step_selected(1)).pack(side="left", padx=(8, 0))

        tk.Checkbutton(
            controls,
            text="Autoplay on select",
            variable=self.autoplay_var,
        ).pack(side="left", padx=(14, 0))

        tk.Checkbutton(
            controls,
            text="Drag selected clip edges only (allow gaps)",
            variable=self.selected_clip_edge_only_var,
            command=self._redraw_waveform,
        ).pack(side="left", padx=(14, 0))

        tk.Label(controls, text="Min clip (ms)").pack(side="left", padx=(14, 4))
        tk.Spinbox(
            controls,
            from_=40,
            to=600,
            increment=5,
            textvariable=self.min_clip_ms_var,
            width=6,
        ).pack(side="left")

        tk.Label(controls, text="Zoom").pack(side="left", padx=(14, 4))
        tk.Scale(
            controls,
            variable=self.zoom_var,
            from_=1.0,
            to=16.0,
            resolution=0.5,
            orient="horizontal",
            length=240,
            command=lambda _value: self._redraw_waveform(),
        ).pack(side="left")

        tk.Label(controls, textvariable=self.status_var, fg="#14532d").pack(side="right")

        info_frame = tk.LabelFrame(self.root, text="Selected Clip Info", padx=10, pady=8)
        info_frame.pack(fill="x", padx=10, pady=(0, 8))

        self.info_header_label = tk.Label(info_frame, text="No clip selected", font=("Segoe UI", 10, "bold"), justify="left", anchor="w")
        self.info_header_label.pack(fill="x")

        info_grid = tk.Frame(info_frame)
        info_grid.pack(fill="x", pady=(8, 0))
        info_grid.grid_columnconfigure(0, weight=1)
        info_grid.grid_columnconfigure(1, weight=1)
        info_grid.grid_columnconfigure(2, weight=1)

        tk.Label(info_grid, text="Manifest clip text", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(info_grid, text="Dialogue line", font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(info_grid, text="Split validation", font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky="w")

        self.info_expected_text = ScrolledText(info_grid, height=5, wrap="word")
        self.info_expected_text.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.info_expected_text.configure(state="disabled")

        self.info_dialogue_text = ScrolledText(info_grid, height=5, wrap="word")
        self.info_dialogue_text.grid(row=1, column=1, sticky="nsew", padx=(0, 8))
        self.info_dialogue_text.configure(state="disabled")

        self.info_validation_text = ScrolledText(info_grid, height=5, wrap="word")
        self.info_validation_text.grid(row=1, column=2, sticky="nsew")
        self.info_validation_text.configure(state="disabled")

        wave_panel = tk.LabelFrame(
            self.root,
            text="Full Character Waveform (drag vertical split boundaries to adjust clips)",
            padx=10,
            pady=10,
        )
        wave_panel.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.wave_canvas = tk.Canvas(
            wave_panel,
            bg="#111827",
            height=360,
            highlightthickness=1,
            highlightbackground="#374151",
            xscrollincrement=1,
        )
        self.wave_canvas.pack(fill="both", expand=True, side="top")
        self.wave_canvas.bind("<ButtonPress-1>", self._on_wave_press)
        self.wave_canvas.bind("<B1-Motion>", self._on_wave_drag)
        self.wave_canvas.bind("<ButtonRelease-1>", self._on_wave_release)
        self.wave_canvas.bind("<Configure>", lambda _evt: self._redraw_waveform())

        self.wave_scroll_x = tk.Scrollbar(wave_panel, orient="horizontal", command=self.wave_canvas.xview)
        self.wave_scroll_x.pack(fill="x", side="bottom")
        self.wave_canvas.configure(xscrollcommand=self.wave_scroll_x.set)

        tk.Label(self.root, textvariable=self.selection_var, anchor="w").pack(fill="x", padx=10, pady=(0, 10))

    def _set_text_widget(self, widget: ScrolledText | None, text: str) -> None:
        if widget is None:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _refresh_chapters(self) -> None:
        self.chapter_dirs = {}
        self.character_dirs = {}
        self.chapter_var.set("")
        self.character_var.set("")

        if not self.book_dir.exists():
            self.status_var.set(f"Book dir does not exist: {self.book_dir}")
            if self.chapter_combo is not None:
                self.chapter_combo["values"] = []
            if self.character_combo is not None:
                self.character_combo["values"] = []
            return

        chapter_names: list[str] = []
        for chapter_dir in sorted([item for item in self.book_dir.iterdir() if item.is_dir()], key=lambda item: chapter_sort_key(item.name)):
            audio_lines = chapter_dir / "audio_lines"
            if not audio_lines.exists() or not audio_lines.is_dir():
                continue

            has_pipeline_character = False
            for character_dir in [item for item in audio_lines.iterdir() if item.is_dir()]:
                if (character_dir / "_chunk_pipeline" / "full_character.wav").exists() and (character_dir / "manifest.json").exists():
                    has_pipeline_character = True
                    break

            if has_pipeline_character:
                chapter_names.append(chapter_dir.name)
                self.chapter_dirs[chapter_dir.name] = chapter_dir

        if self.chapter_combo is not None:
            self.chapter_combo["values"] = chapter_names

        if chapter_names:
            self.chapter_var.set(chapter_names[0])
            self._on_chapter_selected()
            self.status_var.set(f"Loaded {len(chapter_names)} chapter(s) with _chunk_pipeline audio.")
        else:
            self.status_var.set("No chapters found with _chunk_pipeline/full_character.wav outputs.")

    def _on_chapter_selected(self) -> None:
        chapter_name = self.chapter_var.get().strip()
        chapter_dir = self.chapter_dirs.get(chapter_name)

        self.character_dirs = {}
        character_names: list[str] = []

        if chapter_dir is not None:
            audio_lines = chapter_dir / "audio_lines"
            for character_dir in sorted([item for item in audio_lines.iterdir() if item.is_dir()], key=lambda item: item.name.lower()):
                full_audio = character_dir / "_chunk_pipeline" / "full_character.wav"
                manifest = character_dir / "manifest.json"
                if full_audio.exists() and manifest.exists():
                    character_names.append(character_dir.name)
                    self.character_dirs[character_dir.name] = character_dir

        if self.character_combo is not None:
            self.character_combo["values"] = character_names

        if character_names:
            self.character_var.set(character_names[0])
        else:
            self.character_var.set("")

    def _confirm_discard_if_dirty(self) -> bool:
        if not self.dirty:
            return True
        return bool(
            messagebox.askyesno(
                "Unsaved edits",
                "You have unsaved boundary edits. Discard them and continue?",
            )
        )

    def _start_selected(self) -> None:
        if not self._confirm_discard_if_dirty():
            return

        self._stop_playback(clear_playhead=True)

        chapter_name = self.chapter_var.get().strip()
        character_name = self.character_var.get().strip()
        chapter_dir = self.chapter_dirs.get(chapter_name)
        character_dir = self.character_dirs.get(character_name)

        if chapter_dir is None or character_dir is None:
            messagebox.showwarning("Selection required", "Choose both chapter and character before starting.")
            return

        try:
            context = self._load_character_context(
                chapter_name=chapter_name,
                chapter_dir=chapter_dir,
                character_name=character_name,
                character_dir=character_dir,
            )
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return

        try:
            full_audio, sample_rate = sf.read(str(context.full_audio_path), dtype="float32", always_2d=True)
        except Exception as exc:
            messagebox.showerror("Audio load failed", f"Could not load waveform:\n{context.full_audio_path}\n\n{exc}")
            return

        if full_audio.size == 0:
            messagebox.showerror("Audio load failed", f"Waveform has no samples:\n{context.full_audio_path}")
            return

        self.context = context
        self.original_regions = [deepcopy(item) for item in context.regions]
        self.full_audio_2d = full_audio
        self.sample_rate = int(sample_rate)
        self.wave_mono = full_audio.mean(axis=1)
        self.audio_duration_sec = float(len(self.wave_mono)) / float(self.sample_rate)
        self.wave_peak_cache = {}

        self.selected_region_index = 0 if context.regions else None
        self.drag_boundary_index = None
        self.drag_selected_edge = None
        self.dirty = False

        if self.selected_region_index is not None:
            self.playback_wave_sec = context.regions[self.selected_region_index].start_sec
        else:
            self.playback_wave_sec = None

        self.preview_path = context.character_dir / "_chunk_pipeline" / "_manual_split_preview.wav"
        self.status_var.set(
            f"Loaded {chapter_name} / {character_name}: {len(context.regions)} clips | {self.audio_duration_sec:.2f}s"
        )
        self._sync_selection_text()
        self._redraw_waveform()

    def _reload_current(self) -> None:
        if self.context is None:
            return
        if not self._confirm_discard_if_dirty():
            return
        self._start_selected()

    def _load_character_context(
        self,
        *,
        chapter_name: str,
        chapter_dir: Path,
        character_name: str,
        character_dir: Path,
    ) -> CharacterContext:
        manifest_path = character_dir / "manifest.json"
        full_audio_path = character_dir / "_chunk_pipeline" / "full_character.wav"
        split_validation_path = character_dir / "_chunk_pipeline" / "split_validation_report.json"
        dialogue_path = chapter_dir / "dialogue.json"

        if not manifest_path.exists():
            raise FileNotFoundError(f"Missing manifest: {manifest_path}")
        if not full_audio_path.exists():
            raise FileNotFoundError(f"Missing full audio: {full_audio_path}")

        manifest_payload = read_json(manifest_path)
        clips = manifest_payload.get("clips")
        if not isinstance(clips, list):
            raise ValueError(f"Invalid manifest format (missing clips array): {manifest_path}")

        regions: list[ClipRegion] = []
        for clip_index, clip in enumerate(clips):
            if not isinstance(clip, dict):
                continue

            clip_chapter = str(clip.get("chapter") or "")
            if clip_chapter and clip_chapter != chapter_name:
                continue

            line_id = clip.get("id")
            if not isinstance(line_id, int):
                continue

            metadata = clip.get("metadata") if isinstance(clip.get("metadata"), dict) else {}
            start_raw = metadata.get("startSec")
            end_raw = metadata.get("endSec")
            duration_raw = metadata.get("duration")

            if start_raw is None or end_raw is None:
                if isinstance(start_raw, (int, float)) and isinstance(duration_raw, (int, float)):
                    end_raw = float(start_raw) + float(duration_raw)
                else:
                    continue

            start_sec = float(start_raw)
            end_sec = float(end_raw)
            if end_sec <= start_sec:
                continue

            audio_file = str(clip.get("audioFile") or f"{line_id}-{character_name}.wav")
            text = str(clip.get("text") or "")
            regions.append(
                ClipRegion(
                    line_id=line_id,
                    clip_index=clip_index,
                    text=text,
                    audio_file=audio_file,
                    start_sec=start_sec,
                    end_sec=end_sec,
                )
            )

        if not regions:
            raise ValueError("No editable clips with startSec/endSec found in manifest.")

        regions.sort(key=lambda item: (item.start_sec, item.line_id))

        dialogue_by_id: dict[int, dict[str, Any]] = {}
        if dialogue_path.exists():
            try:
                payload = read_json(dialogue_path)
                lines = payload.get("lines") if isinstance(payload, dict) else None
                if isinstance(lines, list):
                    for entry in lines:
                        if not isinstance(entry, dict):
                            continue
                        line_id = entry.get("id")
                        if isinstance(line_id, int):
                            dialogue_by_id[line_id] = entry
            except Exception:
                dialogue_by_id = {}

        validation_by_id: dict[int, dict[str, Any]] = {}
        if split_validation_path.exists():
            try:
                payload = read_json(split_validation_path)
                lines = payload.get("lines") if isinstance(payload, dict) else None
                if isinstance(lines, list):
                    for entry in lines:
                        if not isinstance(entry, dict):
                            continue
                        line_id = entry.get("lineId")
                        if isinstance(line_id, int):
                            validation_by_id[line_id] = entry
            except Exception:
                validation_by_id = {}

        return CharacterContext(
            chapter_name=chapter_name,
            chapter_dir=chapter_dir,
            character_name=character_name,
            character_dir=character_dir,
            manifest_path=manifest_path,
            full_audio_path=full_audio_path,
            dialogue_path=dialogue_path if dialogue_path.exists() else None,
            split_validation_path=split_validation_path if split_validation_path.exists() else None,
            manifest_payload=manifest_payload,
            regions=regions,
            dialogue_by_id=dialogue_by_id,
            validation_by_id=validation_by_id,
        )

    def _timeline_width_px(self) -> int:
        if self.wave_canvas is None:
            return 1200
        viewport_width = max(1200, self.wave_canvas.winfo_width())
        return int(max(viewport_width, viewport_width * float(self.zoom_var.get())))

    def _time_to_x(self, sec: float) -> float:
        width = max(1, self._timeline_width_px())
        if self.audio_duration_sec <= 0.0:
            return 0.0
        ratio = max(0.0, min(1.0, sec / self.audio_duration_sec))
        return ratio * width

    def _x_to_time(self, x: float) -> float:
        width = max(1, self._timeline_width_px())
        if self.audio_duration_sec <= 0.0:
            return 0.0
        return max(0.0, min(self.audio_duration_sec, (x / width) * self.audio_duration_sec))

    def _boundaries(self) -> list[float]:
        if self.context is None or not self.context.regions:
            return []
        return [self.context.regions[0].start_sec, *[item.end_sec for item in self.context.regions]]

    def _segment_index_at_time(self, sec: float) -> int | None:
        if self.context is None:
            return None
        for idx, region in enumerate(self.context.regions):
            if region.start_sec <= sec <= region.end_sec:
                return idx
        return None

    def _nearest_boundary_index_at_x(self, canvas_x: float, tolerance_px: float = 8.0) -> int | None:
        boundaries = self._boundaries()
        if not boundaries:
            return None

        nearest_idx: int | None = None
        nearest_distance: float | None = None
        for idx, boundary_sec in enumerate(boundaries):
            boundary_x = self._time_to_x(boundary_sec)
            dist = abs(boundary_x - canvas_x)
            if nearest_distance is None or dist < nearest_distance:
                nearest_distance = dist
                nearest_idx = idx

        if nearest_distance is None or nearest_idx is None:
            return None
        return nearest_idx if nearest_distance <= tolerance_px else None

    def _selected_edge_at_x(self, canvas_x: float, tolerance_px: float = 8.0) -> str | None:
        region = self._selected_region()
        if region is None:
            return None

        left_x = self._time_to_x(region.start_sec)
        right_x = self._time_to_x(region.end_sec)
        left_dist = abs(canvas_x - left_x)
        right_dist = abs(canvas_x - right_x)

        if left_dist <= tolerance_px and left_dist <= right_dist:
            return "left"
        if right_dist <= tolerance_px:
            return "right"
        return None

    def _clip_peaks(self, width: int) -> np.ndarray:
        assert self.wave_mono is not None
        if width in self.wave_peak_cache:
            return self.wave_peak_cache[width]

        mono = self.wave_mono
        total = len(mono)
        if total == 0:
            peaks = np.zeros(width, dtype=np.float32)
            self.wave_peak_cache[width] = peaks
            return peaks

        edges = np.linspace(0, total, num=width + 1, dtype=np.int64)
        peaks = np.zeros(width, dtype=np.float32)
        abs_mono = np.abs(mono)
        for index in range(width):
            start = int(edges[index])
            end = int(edges[index + 1])
            if end <= start:
                peaks[index] = 0.0
            else:
                peaks[index] = float(np.max(abs_mono[start:end]))

        peak_max = float(np.max(peaks))
        if peak_max > 1e-8:
            peaks = peaks / peak_max

        self.wave_peak_cache[width] = peaks
        return peaks

    def _redraw_waveform(self) -> None:
        if self.wave_canvas is None:
            return

        canvas = self.wave_canvas
        canvas.delete("all")

        width = self._timeline_width_px()
        height = max(120, canvas.winfo_height())
        mid_y = height / 2.0
        canvas.configure(scrollregion=(0, 0, width, height))

        canvas.create_rectangle(0, 0, width, height, fill="#111827", outline="")
        canvas.create_line(0, mid_y, width, mid_y, fill="#1f2937")

        if self.context is None or self.wave_mono is None:
            canvas.create_text(width / 2, height / 2, text="Start a chapter + character to load waveform", fill="#9ca3af")
            return

        edge_only_mode = bool(self.selected_clip_edge_only_var.get())

        peaks = self._clip_peaks(width)
        if peaks.size > 0:
            top_points: list[float] = []
            bottom_points: list[float] = []
            amp_scale = height * 0.34
            for x, amp in enumerate(peaks):
                top_points.extend([float(x), float(mid_y - (amp * amp_scale))])
                bottom_points.extend([float(x), float(mid_y + (amp * amp_scale))])
            if len(top_points) >= 4:
                canvas.create_line(*top_points, fill="#34d399")
                canvas.create_line(*bottom_points, fill="#34d399")

        regions = self.context.regions
        for idx, region in enumerate(regions):
            x1 = self._time_to_x(region.start_sec)
            x2 = self._time_to_x(region.end_sec)
            if x2 <= x1:
                continue

            validation = self.context.validation_by_id.get(region.line_id, {})
            flagged = bool(validation.get("needsBoundaryAdjustment")) or str(validation.get("status") or "").lower() == "flagged"
            is_selected = idx == self.selected_region_index

            if is_selected:
                fill = "#60a5fa"
                outline = "#bfdbfe"
            elif flagged:
                fill = "#f97316"
                outline = "#fed7aa"
            else:
                fill = "#2563eb"
                outline = "#93c5fd"

            canvas.create_rectangle(
                x1,
                24,
                x2,
                height - 24,
                fill=fill,
                outline=outline,
                stipple="gray50",
            )

            region_width = x2 - x1
            if region_width >= 26:
                canvas.create_text(
                    x1 + 4,
                    28,
                    text=f"#{region.line_id}",
                    fill="#f8fafc",
                    font=("Segoe UI", 8, "bold"),
                    anchor="nw",
                )

        for boundary_index, boundary_sec in enumerate(self._boundaries()):
            boundary_x = self._time_to_x(boundary_sec)
            is_drag = boundary_index == self.drag_boundary_index
            if edge_only_mode:
                color = "#6b7280"
                width_px = 1
            else:
                color = "#fbbf24" if is_drag else "#f59e0b"
                width_px = 3 if is_drag else 2
            canvas.create_line(boundary_x, 0, boundary_x, height, fill=color, width=width_px)

        if edge_only_mode and self.selected_region_index is not None:
            if 0 <= self.selected_region_index < len(regions):
                selected_region = regions[self.selected_region_index]
                left_x = self._time_to_x(selected_region.start_sec)
                right_x = self._time_to_x(selected_region.end_sec)
                left_color = "#fbbf24" if self.drag_selected_edge == "left" else "#fde68a"
                right_color = "#fbbf24" if self.drag_selected_edge == "right" else "#fde68a"
                canvas.create_line(left_x, 0, left_x, height, fill=left_color, width=4)
                canvas.create_line(right_x, 0, right_x, height, fill=right_color, width=4)

        if self.playback_wave_sec is not None:
            play_sec = max(0.0, min(self.audio_duration_sec, float(self.playback_wave_sec)))
            play_x = self._time_to_x(play_sec)
            play_color = "#ef4444" if self.playback_active else "#fca5a5"
            play_width = 3 if self.playback_active else 2
            canvas.create_line(play_x, 0, play_x, height, fill=play_color, width=play_width)
            canvas.create_text(
                min(width - 4, play_x + 4),
                6,
                text=f"{play_sec:.2f}s",
                fill=play_color,
                anchor="nw",
                font=("Segoe UI", 8, "bold"),
            )

    def _selected_region(self) -> ClipRegion | None:
        if self.context is None or self.selected_region_index is None:
            return None
        if self.selected_region_index < 0 or self.selected_region_index >= len(self.context.regions):
            return None
        return self.context.regions[self.selected_region_index]

    def _sync_selection_text(self) -> None:
        if self.context is None:
            self.selection_var.set("No clip selected")
            if self.info_header_label is not None:
                self.info_header_label.config(text="No clip selected")
            self._set_text_widget(self.info_expected_text, "")
            self._set_text_widget(self.info_dialogue_text, "")
            self._set_text_widget(self.info_validation_text, "")
            return

        region = self._selected_region()
        if region is None:
            self.selection_var.set("No clip selected")
            if self.info_header_label is not None:
                self.info_header_label.config(text="No clip selected")
            self._set_text_widget(self.info_expected_text, "")
            self._set_text_widget(self.info_dialogue_text, "")
            self._set_text_widget(self.info_validation_text, "")
            return

        idx = int(self.selected_region_index or 0)
        total = len(self.context.regions)
        header = (
            f"Clip {idx + 1}/{total} | lineId={region.line_id} | "
            f"{region.start_sec:.4f}s → {region.end_sec:.4f}s | "
            f"duration={region.duration_sec:.4f}s"
        )
        self.selection_var.set(header)
        if self.info_header_label is not None:
            self.info_header_label.config(text=header)

        self._set_text_widget(self.info_expected_text, region.text or "")

        dialogue_entry = self.context.dialogue_by_id.get(region.line_id, {})
        dialogue_text = ""
        if dialogue_entry:
            character = str(dialogue_entry.get("characterId") or "")
            line_text = str(dialogue_entry.get("text") or "")
            dialogue_text = (
                f"lineId: {region.line_id}\n"
                f"characterId: {character}\n\n"
                f"{line_text}"
            ).strip()
        else:
            dialogue_text = f"lineId: {region.line_id}\n\n(No dialogue entry found)"
        self._set_text_widget(self.info_dialogue_text, dialogue_text)

        validation_entry = self.context.validation_by_id.get(region.line_id)
        if validation_entry is None:
            self._set_text_widget(self.info_validation_text, "No split validation entry for this line.")
        else:
            status = str(validation_entry.get("status") or "")
            needs_adjust = bool(validation_entry.get("needsBoundaryAdjustment"))
            similarity = validation_entry.get("expectedSimilarity")
            transcript = str(validation_entry.get("transcriptText") or "")
            lines = [
                f"status: {status}",
                f"needsBoundaryAdjustment: {needs_adjust}",
                f"expectedSimilarity: {similarity}",
                "",
                transcript,
            ]
            self._set_text_widget(self.info_validation_text, "\n".join(lines))

    def _on_wave_press(self, event: tk.Event) -> None:
        if self.context is None or self.wave_canvas is None:
            return

        canvas_x = float(self.wave_canvas.canvasx(event.x))
        self.drag_boundary_index = None
        self.drag_selected_edge = None
        edge_only_mode = bool(self.selected_clip_edge_only_var.get())

        if edge_only_mode:
            edge = self._selected_edge_at_x(canvas_x)
            if edge is not None:
                if self.playback_active:
                    self._stop_playback(clear_playhead=False)
                self.drag_selected_edge = edge
                self.drag_boundary_index = None
                self._redraw_waveform()
                return

        boundary_idx = self._nearest_boundary_index_at_x(canvas_x)

        if boundary_idx is not None and not edge_only_mode:
            if self.playback_active:
                self._stop_playback(clear_playhead=False)
            self.drag_boundary_index = boundary_idx
            self.drag_selected_edge = None
            if boundary_idx == 0:
                self.selected_region_index = 0
            elif boundary_idx >= len(self.context.regions):
                self.selected_region_index = len(self.context.regions) - 1
            else:
                self.selected_region_index = boundary_idx
            self._sync_selection_text()
            self._redraw_waveform()
            return

        sec = self._x_to_time(canvas_x)
        idx = self._segment_index_at_time(sec)
        if idx is None:
            return

        self.selected_region_index = idx
        self._sync_selection_text()

        if edge_only_mode:
            edge_after_select = self._selected_edge_at_x(canvas_x)
            if edge_after_select is not None:
                if self.playback_active:
                    self._stop_playback(clear_playhead=False)
                self.drag_selected_edge = edge_after_select
                self.drag_boundary_index = None

        if self.playback_active:
            self._seek_playback_to_wave_time(sec)
            return

        self._redraw_waveform()
        if self.autoplay_var.get():
            self._play_selected()

    def _on_wave_drag(self, event: tk.Event) -> None:
        if self.context is None or self.wave_canvas is None:
            return

        regions = self.context.regions
        if not regions:
            return

        min_duration_sec = max(0.02, float(self.min_clip_ms_var.get()) / 1000.0)
        proposed_sec = self._x_to_time(float(self.wave_canvas.canvasx(event.x)))

        if self.drag_selected_edge is not None and self.selected_region_index is not None:
            if self.selected_region_index < 0 or self.selected_region_index >= len(regions):
                return

            region = regions[self.selected_region_index]
            prev_end = regions[self.selected_region_index - 1].end_sec if self.selected_region_index > 0 else 0.0
            next_start = (
                regions[self.selected_region_index + 1].start_sec
                if self.selected_region_index + 1 < len(regions)
                else self.audio_duration_sec
            )

            if self.drag_selected_edge == "left":
                min_sec = prev_end
                max_sec = region.end_sec - min_duration_sec
                if max_sec < min_sec:
                    return
                region.start_sec = max(min_sec, min(max_sec, proposed_sec))
            elif self.drag_selected_edge == "right":
                min_sec = region.start_sec + min_duration_sec
                max_sec = next_start
                if max_sec < min_sec:
                    return
                region.end_sec = max(min_sec, min(max_sec, proposed_sec))

            self.dirty = True
            self._sync_selection_text()
            self._redraw_waveform()
            return

        if self.drag_boundary_index is None:
            return

        boundary_idx = self.drag_boundary_index

        if boundary_idx == 0:
            max_sec = regions[0].end_sec - min_duration_sec
            new_sec = max(0.0, min(max_sec, proposed_sec))
            regions[0].start_sec = new_sec
        elif boundary_idx == len(regions):
            min_sec = regions[-1].start_sec + min_duration_sec
            new_sec = min(self.audio_duration_sec, max(min_sec, proposed_sec))
            regions[-1].end_sec = new_sec
        else:
            left = regions[boundary_idx - 1]
            right = regions[boundary_idx]
            min_sec = left.start_sec + min_duration_sec
            max_sec = right.end_sec - min_duration_sec
            if max_sec < min_sec:
                return
            new_sec = max(min_sec, min(max_sec, proposed_sec))
            left.end_sec = new_sec
            right.start_sec = new_sec

        self.dirty = True
        self._sync_selection_text()
        self._redraw_waveform()

    def _on_wave_release(self, _event: tk.Event) -> None:
        if self.drag_boundary_index is None and self.drag_selected_edge is None:
            return
        self.drag_boundary_index = None
        self.drag_selected_edge = None
        self._redraw_waveform()

    def _cancel_playback_timer(self) -> None:
        if self.playback_after_id is None:
            return
        try:
            self.root.after_cancel(self.playback_after_id)
        except Exception:
            pass
        self.playback_after_id = None

    def _stop_playback(self, *, clear_playhead: bool) -> None:
        stop_audio()
        self.playback_active = False
        self._cancel_playback_timer()
        if clear_playhead:
            self.playback_wave_sec = None
            self.playback_region_index = None
        self._redraw_waveform()

    def _playback_tick(self) -> None:
        self.playback_after_id = None
        if not self.playback_active:
            return

        elapsed = max(0.0, time.monotonic() - self.playback_started_monotonic)
        current_sec = self.playback_region_start_sec + self.playback_seek_offset_sec + elapsed

        if current_sec >= self.playback_region_end_sec:
            self.playback_wave_sec = self.playback_region_end_sec
            self.playback_active = False
            self._redraw_waveform()
            return

        self.playback_wave_sec = current_sec
        self._redraw_waveform()
        self.playback_after_id = self.root.after(33, self._playback_tick)

    def _start_playback_from_region(self, region_index: int, *, start_wave_sec: float | None = None) -> None:
        if self.context is None:
            return
        if region_index < 0 or region_index >= len(self.context.regions):
            return

        region = self.context.regions[region_index]
        effective_start = region.start_sec if start_wave_sec is None else float(start_wave_sec)
        effective_start = max(region.start_sec, min(region.end_sec, effective_start))

        min_step = 1.0 / float(self.sample_rate) if self.sample_rate > 0 else 0.0005
        if effective_start >= region.end_sec:
            effective_start = max(region.start_sec, region.end_sec - min_step)

        preview_path = self._write_preview_clip(region, start_wave_sec=effective_start)
        if preview_path is None:
            messagebox.showwarning(
                "Preview failed",
                "Could not render preview clip from current boundaries.",
            )
            return

        self._stop_playback(clear_playhead=False)
        play_wav(preview_path)

        self.playback_active = True
        self.playback_region_index = region_index
        self.playback_region_start_sec = region.start_sec
        self.playback_region_end_sec = region.end_sec
        self.playback_seek_offset_sec = max(0.0, effective_start - region.start_sec)
        self.playback_started_monotonic = time.monotonic()
        self.playback_wave_sec = effective_start

        self._cancel_playback_timer()
        self.playback_after_id = self.root.after(33, self._playback_tick)
        self._redraw_waveform()

    def _seek_playback_to_wave_time(self, wave_sec: float) -> None:
        if self.context is None or self.selected_region_index is None:
            return
        if self.selected_region_index < 0 or self.selected_region_index >= len(self.context.regions):
            return

        region = self.context.regions[self.selected_region_index]
        target_sec = max(region.start_sec, min(region.end_sec, wave_sec))
        self._start_playback_from_region(
            self.selected_region_index,
            start_wave_sec=target_sec,
        )

    def _write_preview_clip(self, region: ClipRegion, *, start_wave_sec: float | None = None) -> Path | None:
        if self.context is None or self.full_audio_2d is None or self.sample_rate <= 0:
            return None

        if self.preview_path is None:
            self.preview_path = self.context.character_dir / "_chunk_pipeline" / "_manual_split_preview.wav"

        effective_start = region.start_sec if start_wave_sec is None else float(start_wave_sec)
        effective_start = max(region.start_sec, min(region.end_sec, effective_start))
        start_idx = int(max(0, np.floor(effective_start * self.sample_rate)))
        end_idx = int(min(len(self.full_audio_2d), np.ceil(region.end_sec * self.sample_rate)))
        if end_idx <= start_idx:
            end_idx = min(len(self.full_audio_2d), start_idx + 1)
        if end_idx <= start_idx:
            return None

        clip = self.full_audio_2d[start_idx:end_idx]
        self.preview_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(self.preview_path), clip, self.sample_rate)
        return self.preview_path

    def _play_selected(self) -> None:
        if self.selected_region_index is None:
            return
        self._start_playback_from_region(self.selected_region_index)

    def _step_selected(self, step: int) -> None:
        if self.context is None or not self.context.regions:
            return
        if self.selected_region_index is None:
            self.selected_region_index = 0
        else:
            self.selected_region_index = max(0, min(len(self.context.regions) - 1, self.selected_region_index + step))
        self._sync_selection_text()
        self._redraw_waveform()
        if self.autoplay_var.get():
            self._play_selected()

    def _save_changes(self) -> None:
        if self.context is None:
            return
        if self.full_audio_2d is None or self.sample_rate <= 0:
            messagebox.showerror("Cannot save", "No audio loaded.")
            return

        if not self.dirty:
            self.status_var.set("No pending boundary edits to save.")
            return

        regions = self.context.regions
        for idx in range(len(regions) - 1):
            left = regions[idx]
            right = regions[idx + 1]
            if left.end_sec > right.start_sec + 1e-7:
                messagebox.showerror("Invalid boundaries", "One or more clips overlap. Fix boundaries before saving.")
                return

        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        manifest_backup = self.context.manifest_path.with_name(f"{self.context.manifest_path.name}.bak-{timestamp}")

        before_by_line = {item.line_id: {"startSec": item.start_sec, "endSec": item.end_sec} for item in self.original_regions}
        edits_payload = {
            "savedAt": datetime.utcnow().isoformat() + "Z",
            "chapter": self.context.chapter_name,
            "character": self.context.character_name,
            "fullAudio": str(self.context.full_audio_path),
            "manifest": str(self.context.manifest_path),
            "changes": [],
        }

        try:
            manifest_backup.write_text(self.context.manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

            clips = self.context.manifest_payload.get("clips")
            if not isinstance(clips, list):
                raise ValueError("Manifest clips array is invalid while saving.")

            for region in regions:
                clip = clips[region.clip_index]
                if not isinstance(clip, dict):
                    continue

                metadata = clip.get("metadata") if isinstance(clip.get("metadata"), dict) else {}
                clip["metadata"] = metadata

                start_sec = round(float(region.start_sec), 4)
                end_sec = round(float(region.end_sec), 4)
                duration_sec = round(max(0.0, end_sec - start_sec), 4)

                metadata["startSec"] = start_sec
                metadata["endSec"] = end_sec
                metadata["duration"] = duration_sec
                metadata["generatedAt"] = datetime.utcnow().isoformat() + "Z"

                out_path = self.context.character_dir / region.audio_file
                out_path.parent.mkdir(parents=True, exist_ok=True)

                start_idx = int(max(0, np.floor(start_sec * self.sample_rate)))
                end_idx = int(min(len(self.full_audio_2d), np.ceil(end_sec * self.sample_rate)))
                if end_idx <= start_idx:
                    end_idx = min(len(self.full_audio_2d), start_idx + 1)
                if end_idx <= start_idx:
                    raise ValueError(f"Invalid sample range while saving line {region.line_id}")

                clip_audio = self.full_audio_2d[start_idx:end_idx]
                sf.write(str(out_path), clip_audio, self.sample_rate)

                before = before_by_line.get(region.line_id)
                edits_payload["changes"].append(
                    {
                        "lineId": region.line_id,
                        "audioFile": region.audio_file,
                        "before": before,
                        "after": {
                            "startSec": start_sec,
                            "endSec": end_sec,
                            "duration": duration_sec,
                        },
                    }
                )

            self.context.manifest_path.write_text(
                json.dumps(self.context.manifest_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            edits_path = self.context.character_dir / "_chunk_pipeline" / f"manual_split_edits_{timestamp}.json"
            edits_path.parent.mkdir(parents=True, exist_ok=True)
            edits_path.write_text(json.dumps(edits_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        except Exception as exc:
            messagebox.showerror("Save failed", f"Could not persist edits:\n{exc}")
            return

        self.original_regions = [deepcopy(item) for item in self.context.regions]
        self.dirty = False
        self.status_var.set(
            f"Saved {len(regions)} clips. Manifest backup: {manifest_backup.name}"
        )
        self._sync_selection_text()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual review UI to adjust character split boundaries from _chunk_pipeline/full_character.wav"
    )
    parser.add_argument(
        "--book-dir",
        type=Path,
        default=DEFAULT_BOOK_DIR,
        help="Book directory containing chapter folders with audio_lines outputs.",
    )
    args = parser.parse_args()

    root = tk.Tk()
    app = SplitBoundaryReviewApp(root, book_dir=args.book_dir)
    _ = app
    root.mainloop()


if __name__ == "__main__":
    main()
