#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import wave
from array import array
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk


def normalize_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def is_narrator(character: str | None) -> bool:
    return isinstance(character, str) and normalize_key(character) == "narrator"


def play_wav(path: Path) -> None:
    if not path.exists() or path.suffix.lower() != ".wav":
        return
    import winsound

    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)


def stop_audio() -> None:
    import winsound

    winsound.PlaySound(None, winsound.SND_PURGE)


def read_wav_mono_pcm16(path: Path) -> tuple[array, int]:
    import audioop

    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        raw = wav_file.readframes(frame_count)

    if channels > 1:
        raw = audioop.tomono(raw, sample_width, 0.5, 0.5)

    if sample_width != 2:
        raw = audioop.lin2lin(raw, sample_width, 2)

    samples = array("h")
    samples.frombytes(raw)
    return samples, frame_rate


def write_wav_mono_pcm16(path: Path, samples: array, frame_rate: int) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        wav_file.writeframes(samples.tobytes())


@dataclass
class CandidatePair:
    role: str
    line_id: int | None
    character: str
    text: str
    candidate_path: Path
    target_path: Path | None


@dataclass
class ReviewItem:
    chapter_dir: Path
    review_dir: Path
    report_path: Path
    merged_audio_path: Path | None
    pairs: list[CandidatePair]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_target_path(chapter_dir: Path, line_id: int | None, character: str) -> Path | None:
    if line_id is None:
        return None

    audio_lines = chapter_dir / "audio_lines"
    if not audio_lines.exists():
        return None

    direct_matches = sorted(audio_lines.glob(f"*/{line_id}-*.wav"))
    if direct_matches:
        return direct_matches[0].resolve()

    target_key = normalize_key(character)
    for folder in sorted([item for item in audio_lines.iterdir() if item.is_dir()]):
        if normalize_key(folder.name) == target_key:
            return (folder / f"{line_id}-{folder.name}.wav").resolve()

    return None


def normalized_path_key(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").rstrip("/").lower()


def line_token(line_id: Any, fallback: str) -> str:
    return str(line_id) if isinstance(line_id, int) else fallback


def build_review_dir_filter_from_hits(hits_path: Path) -> set[str]:
    payload = read_json(hits_path)
    matches = payload.get("matches", [])
    if not isinstance(matches, list):
        return set()

    review_dir_keys: set[str] = set()
    for match in matches:
        if not isinstance(match, dict):
            continue

        dialogue_path = match.get("dialoguePath")
        if not isinstance(dialogue_path, str) or not dialogue_path.strip():
            continue

        first = match.get("firstDialogue") if isinstance(match.get("firstDialogue"), dict) else {}
        narration = match.get("followingNarration") if isinstance(match.get("followingNarration"), dict) else {}
        followup = match.get("sameSpeakerFollowup") if isinstance(match.get("sameSpeakerFollowup"), dict) else None

        token1 = line_token(first.get("lineId"), "seg1")
        token2 = line_token(narration.get("lineId"), "seg2")
        token3 = line_token(followup.get("lineId") if followup else None, "none")
        folder_name = f"{token1}_{token2}_{token3}"

        chapter_dir = Path(dialogue_path).resolve().parent
        review_dir = chapter_dir / "review" / folder_name
        review_dir_keys.add(normalized_path_key(review_dir))

    return review_dir_keys


def collect_review_items(
    book_dir: Path,
    only_review_dir: Path | None = None,
    review_dir_filter_keys: set[str] | None = None,
    include_narrator_candidates: bool = False,
    review_folder_regex: str | None = r"^regen_.*_tail(?:_final_.*)?$",
) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    only_review_dir_resolved = only_review_dir.resolve() if only_review_dir is not None else None
    folder_filter = re.compile(review_folder_regex) if isinstance(review_folder_regex, str) and review_folder_regex.strip() else None
    for report_path in sorted(book_dir.glob("*/review/*/heuristic_multi_split_experiment.json")):
        review_dir = report_path.parent
        if folder_filter is not None and not folder_filter.search(review_dir.name):
            continue
        if only_review_dir_resolved is not None and review_dir.resolve() != only_review_dir_resolved:
            continue
        if review_dir_filter_keys is not None and normalized_path_key(review_dir) not in review_dir_filter_keys:
            continue
        chapter_dir = review_dir.parent.parent
        payload = read_json(report_path)
        artifacts = payload.get("artifacts", {})
        segment_files = payload.get("artifacts", {}).get("segmentFiles", [])
        if not isinstance(segment_files, list):
            continue

        merged_audio_path: Path | None = None
        merged_input = payload.get("inputs", {}).get("mergedAudio") if isinstance(payload, dict) else None
        if isinstance(merged_input, str) and merged_input.strip():
            candidate_merged = Path(merged_input).resolve()
            if candidate_merged.exists():
                merged_audio_path = candidate_merged
        if merged_audio_path is None:
            fallback_merged = review_dir / "merged_character_full.wav"
            if fallback_merged.exists():
                merged_audio_path = fallback_merged.resolve()

        candidates = []
        for segment in segment_files:
            if not isinstance(segment, dict):
                continue
            character = segment.get("character")
            if not isinstance(character, str) or not character.strip():
                continue
            if is_narrator(character) and not include_narrator_candidates:
                continue
            audio_path = segment.get("audioPath")
            if not isinstance(audio_path, str):
                continue
            line_id = segment.get("lineId") if isinstance(segment.get("lineId"), int) else None
            candidates.append(
                {
                    "line_id": line_id,
                    "character": character,
                    "text": segment.get("text") if isinstance(segment.get("text"), str) else "",
                    "candidate_path": Path(audio_path).resolve(),
                }
            )

        if not candidates:
            continue

        first = candidates[0]
        last = candidates[-1] if len(candidates) > 1 else None

        pairs = [
            CandidatePair(
                role="first",
                line_id=first["line_id"],
                character=first["character"],
                text=str(first.get("text", "")),
                candidate_path=first["candidate_path"],
                target_path=find_target_path(chapter_dir, first["line_id"], first["character"]),
            )
        ]

        if last is not None and (
            last["line_id"] != first["line_id"] or normalize_key(last["character"]) != normalize_key(first["character"])
        ):
            pairs.append(
                CandidatePair(
                    role="last",
                    line_id=last["line_id"],
                    character=last["character"],
                    text=str(last.get("text", "")),
                    candidate_path=last["candidate_path"],
                    target_path=find_target_path(chapter_dir, last["line_id"], last["character"]),
                )
            )

        items.append(
            ReviewItem(
                chapter_dir=chapter_dir,
                review_dir=review_dir,
                report_path=report_path,
                merged_audio_path=merged_audio_path,
                pairs=pairs,
            )
        )

    return items


class ReviewApp:
    def __init__(self, root: tk.Tk, items: list[ReviewItem], decisions_path: Path, state_path: Path) -> None:
        self.root = root
        self.items = items
        self.index = 0
        self.decisions_path = decisions_path
        self.state_path = state_path

        self.root.title("Split Review & Replace")
        self.root.geometry("1200x700")

        self.header_var = tk.StringVar(value="")
        self.path_var = tk.StringVar(value="")
        self.expected_text_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.waveform_source_var = tk.StringVar(value="")
        self.selection_var = tk.StringVar(value="No selection")

        self.replace_vars: dict[str, tk.BooleanVar] = {
            "first": tk.BooleanVar(value=False),
            "last": tk.BooleanVar(value=False),
        }

        self.widgets: dict[str, dict[str, tk.Label]] = {}
        self.wave_source_map: dict[str, Path] = {}

        self.waveform_canvas: tk.Canvas | None = None
        self.waveform_samples: array | None = None
        self.waveform_rate: int = 0
        self.waveform_path: Path | None = None
        self.waveform_duration: float = 0.0
        self.selection_start_x: float | None = None
        self.selection_end_x: float | None = None
        self.selection_clip_path: Path | None = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_resume_state()
        self._render_current()

    def _on_close(self) -> None:
        self._save_resume_state()
        self.root.destroy()

    def _save_resume_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "savedAt": datetime.utcnow().isoformat() + "Z",
            "index": self.index,
            "total": len(self.items),
            "currentReviewDir": str(self._current_item().review_dir) if self._current_item() else None,
        }
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_resume_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        saved_review_dir = payload.get("currentReviewDir")
        if isinstance(saved_review_dir, str) and saved_review_dir:
            for idx, item in enumerate(self.items):
                if str(item.review_dir) == saved_review_dir:
                    self.index = idx
                    return

        saved_index = payload.get("index")
        if isinstance(saved_index, int):
            self.index = max(0, min(saved_index, max(0, len(self.items) - 1)))

    def _build_ui(self) -> None:
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, textvariable=self.header_var, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(top, textvariable=self.path_var, font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))
        tk.Label(top, text="Expected Text:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 0))
        tk.Label(top, textvariable=self.expected_text_var, justify="left", wraplength=1150).pack(anchor="w")

        main = tk.Frame(self.root)
        main.pack(fill="x", padx=10, pady=10)

        for col_idx, role in enumerate(["first", "last"]):
            panel = tk.LabelFrame(main, text=role.capitalize(), padx=10, pady=10)
            panel.grid(row=0, column=col_idx, padx=8, sticky="nsew")
            main.grid_columnconfigure(col_idx, weight=1)

            line_lbl = tk.Label(panel, text="")
            line_lbl.pack(anchor="w")
            cand_lbl = tk.Label(panel, text="", justify="left", wraplength=520)
            cand_lbl.pack(anchor="w", pady=(6, 2))
            targ_lbl = tk.Label(panel, text="", justify="left", wraplength=520)
            targ_lbl.pack(anchor="w", pady=(2, 8))

            btn_row = tk.Frame(panel)
            btn_row.pack(anchor="w", pady=(4, 8))
            tk.Button(btn_row, text="Play Candidate", command=lambda r=role: self._play_candidate(r)).pack(side="left", padx=(0, 8))
            tk.Button(btn_row, text="Play Original", command=lambda r=role: self._play_original(r)).pack(side="left")

            tk.Checkbutton(
                panel,
                text="Replace original with candidate",
                variable=self.replace_vars[role],
            ).pack(anchor="w")

            self.widgets[role] = {
                "line": line_lbl,
                "candidate": cand_lbl,
                "target": targ_lbl,
            }

        wave_panel = tk.LabelFrame(self.root, text="Waveform Editor", padx=10, pady=10)
        wave_panel.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        source_row = tk.Frame(wave_panel)
        source_row.pack(fill="x", pady=(0, 8))

        tk.Label(source_row, text="Source:").pack(side="left")
        self.wave_source_combo = ttk.Combobox(source_row, textvariable=self.waveform_source_var, state="readonly", width=50)
        self.wave_source_combo.pack(side="left", padx=(8, 8))
        self.wave_source_combo.bind("<<ComboboxSelected>>", lambda _evt: self._load_selected_waveform())

        tk.Button(source_row, text="Load", command=self._load_selected_waveform).pack(side="left", padx=(0, 8))
        tk.Button(source_row, text="Play Loaded", command=self._play_loaded_waveform).pack(side="left", padx=(0, 8))
        tk.Button(source_row, text="Cut Selection", command=self._cut_selection).pack(side="left", padx=(0, 8))
        tk.Button(source_row, text="Play Cut", command=self._play_cut_selection).pack(side="left", padx=(0, 8))
        tk.Button(source_row, text="Use Cut as Clip One", command=self._use_cut_as_clip_one).pack(side="left")
        tk.Button(source_row, text="Use Cut as Clip Last", command=self._use_cut_as_clip_last).pack(side="left", padx=(8, 0))

        self.waveform_canvas = tk.Canvas(wave_panel, bg="#101318", height=220, highlightthickness=1, highlightbackground="#3b4252")
        self.waveform_canvas.pack(fill="both", expand=True)
        self.waveform_canvas.bind("<ButtonPress-1>", self._on_wave_press)
        self.waveform_canvas.bind("<B1-Motion>", self._on_wave_drag)
        self.waveform_canvas.bind("<ButtonRelease-1>", self._on_wave_release)
        self.waveform_canvas.bind("<Configure>", lambda _evt: self._redraw_waveform())

        tk.Label(wave_panel, textvariable=self.selection_var, anchor="w").pack(fill="x", pady=(6, 0))

        bottom = tk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        tk.Button(bottom, text="Stop Audio", command=stop_audio).pack(side="left")
        tk.Button(bottom, text="Back", command=self._back).pack(side="left", padx=(8, 0))
        tk.Button(bottom, text="Skip / Next", command=self._next_without_replace).pack(side="right", padx=(8, 0))
        tk.Button(bottom, text="Apply Replacements + Next", command=self._apply_and_next).pack(side="right")

        tk.Label(self.root, textvariable=self.status_var, anchor="w", fg="#14532d").pack(fill="x", padx=10, pady=(0, 10))

    def _current_item(self) -> ReviewItem | None:
        if self.index < 0 or self.index >= len(self.items):
            return None
        return self.items[self.index]

    def _pair_by_role(self, role: str) -> CandidatePair | None:
        item = self._current_item()
        if item is None:
            return None
        for pair in item.pairs:
            if pair.role == role:
                return pair
        return None

    def _render_current(self) -> None:
        item = self._current_item()
        if item is None:
            self.header_var.set("Review complete")
            self.path_var.set("No more items.")
            self.status_var.set("All done.")
            for role in ["first", "last"]:
                self.widgets[role]["line"].config(text="")
                self.widgets[role]["candidate"].config(text="")
                self.widgets[role]["target"].config(text="")
                self.replace_vars[role].set(False)
            self.wave_source_combo["values"] = []
            self.waveform_source_var.set("")
            self.waveform_samples = None
            self.waveform_path = None
            self.expected_text_var.set("")
            self.selection_var.set("No selection")
            self._redraw_waveform()
            return

        self.header_var.set(f"Item {self.index + 1}/{len(self.items)}")
        self.path_var.set(str(item.review_dir))
        self.status_var.set("")

        expected_lines = []
        for role in ["first", "last"]:
            pair = self._pair_by_role(role)
            if pair is None:
                continue
            expected_lines.append(
                f"{role.capitalize()} (line {pair.line_id}, {pair.character}): {pair.text}"
            )
        self.expected_text_var.set("\n".join(expected_lines))

        for role in ["first", "last"]:
            pair = self._pair_by_role(role)
            self.replace_vars[role].set(False)
            if pair is None:
                self.widgets[role]["line"].config(text="Not present")
                self.widgets[role]["candidate"].config(text="Candidate: n/a")
                self.widgets[role]["target"].config(text="Original: n/a")
                continue

            line_txt = f"lineId={pair.line_id} | character={pair.character}"
            cand_txt = f"Candidate: {pair.candidate_path}"
            target_txt = f"Original: {pair.target_path if pair.target_path else 'not found'}"
            self.widgets[role]["line"].config(text=line_txt)
            self.widgets[role]["candidate"].config(text=cand_txt)
            self.widgets[role]["target"].config(text=target_txt)

        self._setup_wave_sources()
        self._load_selected_waveform()
        self._save_resume_state()

    def _setup_wave_sources(self) -> None:
        self.wave_source_map = {}
        item = self._current_item()
        if item is not None and item.merged_audio_path is not None and item.merged_audio_path.exists():
            self.wave_source_map["Merged Character Full WAV"] = item.merged_audio_path

        first_pair = self._pair_by_role("first")
        if first_pair is not None:
            if first_pair.candidate_path.exists():
                self.wave_source_map["First Candidate (New Gen)"] = first_pair.candidate_path
            if first_pair.target_path is not None and first_pair.target_path.exists():
                self.wave_source_map["First Original (Old Audio)"] = first_pair.target_path

        last_pair = self._pair_by_role("last")
        if last_pair is not None:
            if last_pair.candidate_path.exists():
                self.wave_source_map["Last Candidate (New Gen)"] = last_pair.candidate_path
            if last_pair.target_path is not None and last_pair.target_path.exists():
                self.wave_source_map["Last Original (Old Audio)"] = last_pair.target_path

        if self.selection_clip_path is not None and self.selection_clip_path.exists():
            self.wave_source_map["Selection Clip"] = self.selection_clip_path

        values = list(self.wave_source_map.keys())
        self.wave_source_combo["values"] = values
        if values:
            if self.waveform_source_var.get() not in values:
                self.waveform_source_var.set(values[0])
        else:
            self.waveform_source_var.set("")

    def _load_selected_waveform(self) -> None:
        selection = self.waveform_source_var.get()
        source_path = self.wave_source_map.get(selection)
        self.selection_start_x = None
        self.selection_end_x = None
        self.selection_clip_path = None

        if source_path is None:
            self.waveform_samples = None
            self.waveform_path = None
            self.selection_var.set("No source loaded")
            self._redraw_waveform()
            return

        try:
            samples, rate = read_wav_mono_pcm16(source_path)
        except Exception as exc:
            self.waveform_samples = None
            self.waveform_path = None
            self.selection_var.set(f"Failed to load waveform: {exc}")
            self._redraw_waveform()
            return

        self.waveform_samples = samples
        self.waveform_rate = rate
        self.waveform_path = source_path
        self.waveform_duration = (len(samples) / float(rate)) if rate > 0 else 0.0
        self.selection_var.set(f"Loaded: {selection} | Click-drag on waveform to select a cut region")
        self._redraw_waveform()

    def _redraw_waveform(self) -> None:
        if self.waveform_canvas is None:
            return
        canvas = self.waveform_canvas
        canvas.delete("all")

        width = max(10, canvas.winfo_width())
        height = max(10, canvas.winfo_height())
        mid_y = height / 2.0

        canvas.create_line(0, mid_y, width, mid_y, fill="#2e3440")

        if not self.waveform_samples or len(self.waveform_samples) == 0:
            canvas.create_text(width / 2, height / 2, text="No waveform loaded", fill="#8fbcbb")
            return

        samples = self.waveform_samples
        total = len(samples)
        step = max(1, total // width)

        points: list[float] = []
        for x in range(width):
            start = x * step
            if start >= total:
                amp = 0
            else:
                end = min(total, start + step)
                window = samples[start:end]
                max_amp = max(abs(v) for v in window) if len(window) > 0 else 0
                amp = max_amp / 32768.0

            y_top = mid_y - (amp * (height * 0.45))
            y_bottom = mid_y + (amp * (height * 0.45))
            points.extend([x, y_top, x, y_bottom])

        canvas.create_line(*points, fill="#34d399")

        if self.selection_start_x is not None and self.selection_end_x is not None:
            x1 = max(0, min(width, self.selection_start_x))
            x2 = max(0, min(width, self.selection_end_x))
            left = min(x1, x2)
            right = max(x1, x2)
            canvas.create_rectangle(left, 0, right, height, fill="#60a5fa", outline="#93c5fd", stipple="gray25")

    def _on_wave_press(self, event: tk.Event) -> None:
        if self.waveform_samples is None:
            return
        self.selection_start_x = float(event.x)
        self.selection_end_x = float(event.x)
        self._update_selection_label()
        self._redraw_waveform()

    def _on_wave_drag(self, event: tk.Event) -> None:
        if self.waveform_samples is None or self.selection_start_x is None:
            return
        self.selection_end_x = float(event.x)
        self._update_selection_label()
        self._redraw_waveform()

    def _on_wave_release(self, event: tk.Event) -> None:
        if self.waveform_samples is None or self.selection_start_x is None:
            return
        self.selection_end_x = float(event.x)
        self._update_selection_label()
        self._redraw_waveform()

    def _selection_time_range(self) -> tuple[float, float] | None:
        if self.waveform_canvas is None:
            return None
        if self.selection_start_x is None or self.selection_end_x is None:
            return None
        if self.waveform_duration <= 0:
            return None

        width = max(1.0, float(self.waveform_canvas.winfo_width()))
        left = max(0.0, min(width, min(self.selection_start_x, self.selection_end_x)))
        right = max(0.0, min(width, max(self.selection_start_x, self.selection_end_x)))
        if right - left < 2:
            return None

        start_sec = (left / width) * self.waveform_duration
        end_sec = (right / width) * self.waveform_duration
        if end_sec <= start_sec:
            return None
        return start_sec, end_sec

    def _update_selection_label(self) -> None:
        window = self._selection_time_range()
        if window is None:
            self.selection_var.set("Selection too small or missing")
            return
        start_sec, end_sec = window
        self.selection_var.set(f"Selection: {start_sec:.3f}s - {end_sec:.3f}s  ({end_sec - start_sec:.3f}s)")

    def _cut_selection(self) -> None:
        item = self._current_item()
        if item is None or self.waveform_samples is None or self.waveform_path is None:
            return

        window = self._selection_time_range()
        if window is None:
            messagebox.showwarning("No selection", "Select a valid region on the waveform first.")
            return

        start_sec, end_sec = window
        start_idx = int(start_sec * self.waveform_rate)
        end_idx = int(end_sec * self.waveform_rate)
        start_idx = max(0, min(len(self.waveform_samples), start_idx))
        end_idx = max(start_idx + 1, min(len(self.waveform_samples), end_idx))

        cut = array("h", self.waveform_samples[start_idx:end_idx])
        out_path = item.review_dir / "_selection_clip.wav"
        write_wav_mono_pcm16(out_path, cut, self.waveform_rate)
        self.selection_clip_path = out_path
        self.status_var.set(f"Cut saved: {out_path.name}")
        self._setup_wave_sources()

    def _play_loaded_waveform(self) -> None:
        if self.waveform_path is None:
            return
        play_wav(self.waveform_path)

    def _play_cut_selection(self) -> None:
        if self.selection_clip_path is None or not self.selection_clip_path.exists():
            messagebox.showwarning("Missing cut", "No cut clip exists yet. Click 'Cut Selection' first.")
            return
        play_wav(self.selection_clip_path)

    def _use_cut_as_clip_one(self) -> None:
        if self.selection_clip_path is None or not self.selection_clip_path.exists():
            messagebox.showwarning("Missing cut", "No cut clip exists yet. Click 'Cut Selection' first.")
            return

        first_pair = self._pair_by_role("first")
        if first_pair is None:
            messagebox.showwarning("Missing first clip", "No first candidate exists for this item.")
            return

        backup_path = None
        if first_pair.candidate_path.exists():
            backup_path = first_pair.candidate_path.with_name(
                f"{first_pair.candidate_path.name}.bak-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            )
            shutil.copy2(first_pair.candidate_path, backup_path)

        shutil.copy2(self.selection_clip_path, first_pair.candidate_path)
        self.replace_vars["first"].set(True)
        self._write_decision_log(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "reviewDir": str(self._current_item().review_dir) if self._current_item() else None,
                "action": "use-cut-as-first",
                "source": str(self.selection_clip_path),
                "targetCandidate": str(first_pair.candidate_path),
                "backup": str(backup_path) if backup_path else None,
            }
        )
        self.status_var.set("Cut clip copied to first candidate and marked for replacement.")
        self._setup_wave_sources()

    def _use_cut_as_clip_last(self) -> None:
        if self.selection_clip_path is None or not self.selection_clip_path.exists():
            messagebox.showwarning("Missing cut", "No cut clip exists yet. Click 'Cut Selection' first.")
            return

        last_pair = self._pair_by_role("last")
        if last_pair is None:
            messagebox.showwarning("Missing last clip", "No last candidate exists for this item.")
            return

        backup_path = None
        if last_pair.candidate_path.exists():
            backup_path = last_pair.candidate_path.with_name(
                f"{last_pair.candidate_path.name}.bak-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            )
            shutil.copy2(last_pair.candidate_path, backup_path)

        shutil.copy2(self.selection_clip_path, last_pair.candidate_path)
        self.replace_vars["last"].set(True)
        self._write_decision_log(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "reviewDir": str(self._current_item().review_dir) if self._current_item() else None,
                "action": "use-cut-as-last",
                "source": str(self.selection_clip_path),
                "targetCandidate": str(last_pair.candidate_path),
                "backup": str(backup_path) if backup_path else None,
            }
        )
        self.status_var.set("Cut clip copied to last candidate and marked for replacement.")
        self._setup_wave_sources()

    def _back(self) -> None:
        if self.index <= 0:
            return
        self.index -= 1
        self.status_var.set("Moved back.")
        self._save_resume_state()
        self._render_current()

    def _play_candidate(self, role: str) -> None:
        pair = self._pair_by_role(role)
        if pair is None:
            return
        if not pair.candidate_path.exists():
            messagebox.showwarning("Missing file", f"Candidate file does not exist:\n{pair.candidate_path}")
            return
        play_wav(pair.candidate_path)

    def _play_original(self, role: str) -> None:
        pair = self._pair_by_role(role)
        if pair is None or pair.target_path is None:
            return
        if not pair.target_path.exists():
            messagebox.showwarning("Missing file", f"Original file does not exist:\n{pair.target_path}")
            return
        play_wav(pair.target_path)

    def _write_decision_log(self, payload: dict[str, Any]) -> None:
        self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        with self.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _replace_pair(self, pair: CandidatePair) -> dict[str, Any]:
        now = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        candidate = pair.candidate_path
        target = pair.target_path
        if target is None:
            return {
                "ok": False,
                "reason": "missing-target",
                "candidate": str(candidate),
                "target": None,
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if target.exists():
            backup_path = target.with_name(f"{target.name}.bak-{now}")
            shutil.copy2(target, backup_path)

        shutil.copy2(candidate, target)
        return {
            "ok": True,
            "candidate": str(candidate),
            "target": str(target),
            "backup": str(backup_path) if backup_path else None,
        }

    def _apply_and_next(self) -> None:
        item = self._current_item()
        if item is None:
            return

        actions = []
        for role in ["first", "last"]:
            if not self.replace_vars[role].get():
                continue
            pair = self._pair_by_role(role)
            if pair is None:
                continue
            result = self._replace_pair(pair)
            result["role"] = role
            result["lineId"] = pair.line_id
            result["character"] = pair.character
            actions.append(result)

        decision = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "reviewDir": str(item.review_dir),
            "chapterDir": str(item.chapter_dir),
            "action": "apply",
            "replacements": actions,
        }
        self._write_decision_log(decision)

        replaced_count = sum(1 for entry in actions if entry.get("ok"))
        self.status_var.set(f"Applied {replaced_count} replacement(s).")
        self.index += 1
        self._save_resume_state()
        self._render_current()

    def _next_without_replace(self) -> None:
        item = self._current_item()
        if item is None:
            return
        self._write_decision_log(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "reviewDir": str(item.review_dir),
                "chapterDir": str(item.chapter_dir),
                "action": "skip",
            }
        )
        self.status_var.set("Skipped.")
        self.index += 1
        self._save_resume_state()
        self._render_current()


def main() -> None:
    parser = argparse.ArgumentParser(description="Review split audio and replace original files interactively.")
    parser.add_argument(
        "--book-dir",
        type=Path,
        default=Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14"),
    )
    parser.add_argument(
        "--decisions-log",
        type=Path,
        default=Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/review_decisions.jsonl"),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/review_ui_state.json"),
    )
    parser.add_argument(
        "--only-review-dir",
        type=Path,
        default=None,
        help="Optional path to a single review/<line_ids> folder to review.",
    )
    parser.add_argument(
        "--only-hits-file",
        type=Path,
        default=None,
        help="Optional hits JSON file; only review items from its matched review folders are loaded.",
    )
    parser.add_argument(
        "--include-narrator-candidates",
        action="store_true",
        help="Include narrator candidates in review items (off by default).",
    )
    parser.add_argument(
        "--review-folder-regex",
        default=r"^regen_.*_tail(?:_final_.*)?$",
        help="Regex filter for review folder names (default: regen_*_tail and regen_*_tail_final_*). Use empty string to include all.",
    )
    args = parser.parse_args()

    review_dir_filter_keys = None
    if args.only_hits_file is not None:
        review_dir_filter_keys = build_review_dir_filter_from_hits(args.only_hits_file.resolve())

    items = collect_review_items(
        args.book_dir.resolve(),
        args.only_review_dir,
        review_dir_filter_keys,
        include_narrator_candidates=bool(args.include_narrator_candidates),
        review_folder_regex=args.review_folder_regex,
    )
    if not items:
        if args.only_review_dir is not None:
            print(f"No review items found for: {args.only_review_dir}")
        elif args.only_hits_file is not None:
            print(f"No review items found for hits file: {args.only_hits_file}")
        else:
            print("No review items found.")
        return

    root = tk.Tk()
    app = ReviewApp(root, items, args.decisions_log.resolve(), args.state_file.resolve())
    root.mainloop()


if __name__ == "__main__":
    main()
