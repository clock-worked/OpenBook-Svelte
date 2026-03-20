#!/usr/bin/env python3
import argparse
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


def play_wav(path: Path | None) -> None:
    if path is None:
        return
    if not path.exists() or path.suffix.lower() != ".wav":
        return
    import winsound

    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)


def stop_audio() -> None:
    import winsound

    winsound.PlaySound(None, winsound.SND_PURGE)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def chapter_sort_key(chapter_name: str) -> tuple[int, str]:
    match = re.match(r"^(\d{2})\s*-", chapter_name)
    if match:
        return int(match.group(1)), chapter_name
    return 999, chapter_name


def to_wav_target(path: Path | None) -> tuple[Path | None, Path | None]:
    if path is None:
        return None, None
    path_text = str(path)
    if path_text.endswith(".wav.REGEN"):
        wav_path = Path(path_text[:-6])
        return wav_path, path
    if path_text.endswith(".wav"):
        return path, Path(path_text + ".REGEN")
    return None, None


def is_non_line_audio_path(path: Path, chapter_dir: Path) -> bool:
    resolved = path.resolve()
    audio_root = (chapter_dir / "audio_lines").resolve()
    try:
        rel = resolved.relative_to(audio_root)
    except ValueError:
        return False

    lower_name = resolved.name.lower()
    if lower_name in {"full_character.wav", "_manual_split_preview.wav"}:
        return True

    if any(part in {"_chunk_pipeline", "generation_chunks"} for part in rel.parts):
        return True

    if len(rel.parts) != 2:
        return True

    if rel.parts[0].startswith("_"):
        return True

    return False


def find_line_audio_paths(chapter_dir: Path, line_id: int) -> list[Path]:
    audio_root = chapter_dir / "audio_lines"
    if not audio_root.exists():
        return []

    wavs = [
        item.resolve()
        for item in sorted(audio_root.rglob(f"{line_id}-*.wav"))
        if not is_non_line_audio_path(item.resolve(), chapter_dir)
    ]
    if wavs:
        return wavs

    regen = [
        item.resolve()
        for item in sorted(audio_root.rglob(f"{line_id}-*.wav.REGEN"))
        if not is_non_line_audio_path(item.resolve(), chapter_dir)
    ]
    return regen


@dataclass
class CandidateOption:
    line_id: int
    score: float
    text: str
    audio_paths: list[Path] = field(default_factory=list)


@dataclass
class ReviewItem:
    chapter_name: str
    chapter_dir: Path
    status: str
    reasons: list[str]
    line_id: int | None
    expected_text: str
    asr_text: str
    target_wav: Path | None
    target_regen: Path | None
    candidates: list[CandidateOption]


class FlaggedReviewApp:
    def __init__(self, root: tk.Tk, items: list[ReviewItem], decisions_log: Path, state_file: Path) -> None:
        self.root = root
        self.items = items
        self.decisions_log = decisions_log
        self.state_file = state_file
        self.index = 0

        self.header_var = tk.StringVar(value="")
        self.path_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.reason_var = tk.StringVar(value="")
        self.selected_candidate_var = tk.StringVar(value="None")

        self.candidate_rows: list[dict[str, Any]] = []
        self.current_selected_candidate_path: Path | None = None

        self._build_ui()
        self._load_resume_state()
        self._render_current()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        self._save_resume_state()
        self.root.destroy()

    def _save_resume_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "savedAt": datetime.utcnow().isoformat() + "Z",
            "index": self.index,
            "total": len(self.items),
            "lineId": self._current_item().line_id if self._current_item() else None,
            "chapter": self._current_item().chapter_name if self._current_item() else None,
        }
        self.state_file.write_text(json.dumps(
            payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_resume_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return

        saved_index = payload.get("index")
        if isinstance(saved_index, int):
            self.index = max(0, min(saved_index, max(0, len(self.items) - 1)))

    def _build_ui(self) -> None:
        self.root.title("Review Flagged Dialogue Lines")
        self.root.geometry("1280x820")

        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, textvariable=self.header_var, font=(
            "Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(top, textvariable=self.path_var, font=(
            "Segoe UI", 10)).pack(anchor="w", pady=(4, 0))
        tk.Label(top, textvariable=self.reason_var, font=(
            "Segoe UI", 10), fg="#7c2d12").pack(anchor="w", pady=(4, 0))

        text_frame = tk.Frame(self.root)
        text_frame.pack(fill="x", padx=10)

        tk.Label(text_frame, text="Expected", font=(
            "Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(text_frame, text="ASR", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=1, sticky="w")
        text_frame.grid_columnconfigure(0, weight=1)
        text_frame.grid_columnconfigure(1, weight=1)

        self.expected_text_widget = ScrolledText(
            text_frame, height=6, wrap="word", font=("Segoe UI", 10)
        )
        self.expected_text_widget.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        self.expected_text_widget.configure(state="disabled")

        self.asr_text_widget = ScrolledText(
            text_frame, height=6, wrap="word", font=("Segoe UI", 10)
        )
        self.asr_text_widget.grid(row=1, column=1, sticky="nsew")
        self.asr_text_widget.configure(state="disabled")

        target_frame = tk.LabelFrame(
            self.root, text="Flagged Target", padx=10, pady=10)
        target_frame.pack(fill="x", padx=10, pady=(10, 6))

        self.target_label = tk.Label(target_frame, text="")
        self.target_label.pack(anchor="w")

        target_buttons = tk.Frame(target_frame)
        target_buttons.pack(anchor="w", pady=(6, 0))
        tk.Button(target_buttons, text="Play Target WAV",
                  command=self._play_target_wav).pack(side="left", padx=(0, 8))
        tk.Button(target_buttons, text="Play Target REGEN",
                  command=self._play_target_regen).pack(side="left", padx=(0, 8))
        tk.Button(target_buttons, text="Mark REGEN",
                  command=self._mark_regen).pack(side="left", padx=(0, 8))
        tk.Button(target_buttons, text="Unmark REGEN",
                  command=self._unmark_regen).pack(side="left")

        candidate_frame = tk.LabelFrame(
            self.root, text="Candidate Replacements", padx=10, pady=10)
        candidate_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        columns = ("line", "score", "path", "text")
        self.tree = ttk.Treeview(
            candidate_frame, columns=columns, show="headings", height=16)
        self.tree.heading("line", text="Line")
        self.tree.heading("score", text="Score")
        self.tree.heading("path", text="Candidate Audio Path")
        self.tree.heading("text", text="Candidate Text")
        self.tree.column("line", width=70, anchor="center")
        self.tree.column("score", width=80, anchor="center")
        self.tree.column("path", width=460)
        self.tree.column("text", width=620)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        cand_buttons = tk.Frame(candidate_frame)
        cand_buttons.pack(fill="x", pady=(8, 0))
        tk.Label(cand_buttons, textvariable=self.selected_candidate_var).pack(
            side="left")
        tk.Button(cand_buttons, text="Play Candidate",
                  command=self._play_selected_candidate).pack(side="right", padx=(8, 0))
        tk.Button(cand_buttons, text="Replace Target with Candidate",
                  command=self._replace_with_selected_candidate).pack(side="right")

        bottom = tk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        tk.Button(bottom, text="Stop Audio",
                  command=stop_audio).pack(side="left")
        tk.Button(bottom, text="Back", command=self._back).pack(
            side="left", padx=(8, 0))
        tk.Button(bottom, text="Skip / Next",
                  command=self._skip_next).pack(side="right")

        tk.Label(self.root, textvariable=self.status_var, anchor="w",
                 fg="#14532d").pack(fill="x", padx=10, pady=(0, 8))

    def _current_item(self) -> ReviewItem | None:
        if self.index < 0 or self.index >= len(self.items):
            return None
        return self.items[self.index]

    def _render_current(self) -> None:
        item = self._current_item()
        self.tree.delete(*self.tree.get_children())
        self.candidate_rows = []
        self.current_selected_candidate_path = None
        self.selected_candidate_var.set("None")

        if item is None:
            self.header_var.set("Review complete")
            self.path_var.set("No more flagged lines")
            self.reason_var.set("")
            self._set_text_widget(self.expected_text_widget, "")
            self._set_text_widget(self.asr_text_widget, "")
            self.target_label.config(text="")
            self.status_var.set("All items reviewed")
            return

        self.header_var.set(f"Flagged item {self.index + 1}/{len(self.items)}")
        self.path_var.set(
            f"{item.chapter_name} | line {item.line_id} | status={item.status}")
        reason_text = "; ".join(
            item.reasons) if item.reasons else "No reason provided"
        self.reason_var.set(reason_text)
        self._set_text_widget(
            self.expected_text_widget,
            item.expected_text or "(missing expected text)",
        )
        self._set_text_widget(
            self.asr_text_widget,
            item.asr_text or "(missing ASR text)",
        )

        target_txt = f"Target WAV: {item.target_wav} | Target REGEN: {item.target_regen}"
        self.target_label.config(text=target_txt)

        for candidate in item.candidates:
            if candidate.audio_paths:
                for candidate_path in candidate.audio_paths:
                    row = {
                        "line": candidate.line_id,
                        "score": candidate.score,
                        "text": candidate.text,
                        "path": candidate_path,
                    }
                    self.candidate_rows.append(row)
            else:
                row = {
                    "line": candidate.line_id,
                    "score": candidate.score,
                    "text": candidate.text,
                    "path": None,
                }
                self.candidate_rows.append(row)

        for idx, row in enumerate(self.candidate_rows):
            path_text = str(
                row["path"]) if row["path"] is not None else "(no audio file found)"
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(row["line"], f"{row['score']:.4f}",
                        path_text, row["text"]),
            )

        self._save_resume_state()

    def _on_tree_select(self, _event: tk.Event) -> None:
        selected = self.tree.selection()
        if not selected:
            self.current_selected_candidate_path = None
            self.selected_candidate_var.set("None")
            return

        row = self.candidate_rows[int(selected[0])]
        candidate_path = row["path"]
        self.current_selected_candidate_path = candidate_path
        if candidate_path is None:
            self.selected_candidate_var.set(
                "Selected candidate has no audio file")
        else:
            self.selected_candidate_var.set(
                f"Selected: line {row['line']} -> {candidate_path.name}")

    def _play_target_wav(self) -> None:
        item = self._current_item()
        if item is None:
            return
        play_wav(item.target_wav)

    def _play_target_regen(self) -> None:
        item = self._current_item()
        if item is None or item.target_regen is None:
            return
        wav_equivalent, _ = to_wav_target(item.target_regen)
        if wav_equivalent is None:
            return
        temp = item.target_regen
        if temp.exists() and temp.suffix == ".REGEN":
            temp_play = Path(str(temp)[:-6])
            if not temp_play.exists():
                shutil.copy2(temp, temp_play)
                try:
                    play_wav(temp_play)
                finally:
                    try:
                        temp_play.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                play_wav(temp_play)

    def _mark_regen(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if item.target_wav is None or item.target_regen is None:
            messagebox.showwarning(
                "Missing target", "No target path available for this item.")
            return

        if item.target_regen.exists():
            self.status_var.set("Already marked as REGEN.")
            return
        if not item.target_wav.exists():
            self.status_var.set("Target WAV not found; cannot mark REGEN.")
            return

        item.target_wav.rename(item.target_regen)
        self._write_decision(
            {
                "action": "mark_regen",
                "chapter": item.chapter_name,
                "lineId": item.line_id,
                "from": str(item.target_wav),
                "to": str(item.target_regen),
            }
        )
        self.status_var.set("Marked for REGEN.")
        self._render_current()

    def _unmark_regen(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if item.target_wav is None or item.target_regen is None:
            messagebox.showwarning(
                "Missing target", "No target path available for this item.")
            return

        if not item.target_regen.exists():
            self.status_var.set("No REGEN file to restore.")
            return

        if item.target_wav.exists():
            backup = item.target_wav.with_name(
                f"{item.target_wav.name}.bak-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}")
            shutil.move(item.target_wav, backup)

        shutil.move(item.target_regen, item.target_wav)
        self._write_decision(
            {
                "action": "unmark_regen",
                "chapter": item.chapter_name,
                "lineId": item.line_id,
                "from": str(item.target_regen),
                "to": str(item.target_wav),
            }
        )
        self.status_var.set("Restored from REGEN.")
        self._render_current()

    def _play_selected_candidate(self) -> None:
        play_wav(self.current_selected_candidate_path)

    def _replace_with_selected_candidate(self) -> None:
        item = self._current_item()
        if item is None:
            return
        candidate = self.current_selected_candidate_path
        if candidate is None or not candidate.exists():
            messagebox.showwarning(
                "No candidate", "Select a candidate row with a valid audio path.")
            return
        if item.target_wav is None or item.target_regen is None:
            messagebox.showwarning(
                "Missing target", "No target path available for this item.")
            return

        backup_paths: list[str] = []
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")

        if item.target_wav.exists():
            backup_wav = item.target_wav.with_name(
                f"{item.target_wav.name}.bak-{stamp}")
            shutil.copy2(item.target_wav, backup_wav)
            backup_paths.append(str(backup_wav))

        if item.target_regen.exists():
            backup_regen = item.target_regen.with_name(
                f"{item.target_regen.name}.bak-{stamp}")
            shutil.move(item.target_regen, backup_regen)
            backup_paths.append(str(backup_regen))

        item.target_wav.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, item.target_wav)

        self._write_decision(
            {
                "action": "replace_with_candidate",
                "chapter": item.chapter_name,
                "lineId": item.line_id,
                "candidate": str(candidate),
                "target": str(item.target_wav),
                "backups": backup_paths,
            }
        )
        self.status_var.set("Replaced target with selected candidate.")
        self._render_current()

    def _skip_next(self) -> None:
        item = self._current_item()
        if item is None:
            return
        self._write_decision(
            {
                "action": "skip",
                "chapter": item.chapter_name,
                "lineId": item.line_id,
            }
        )
        self.index += 1
        self._render_current()

    def _back(self) -> None:
        if self.index <= 0:
            return
        self.index -= 1
        self._render_current()

    def _write_decision(self, payload: dict[str, Any]) -> None:
        self.decisions_log.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "index": self.index,
            **payload,
        }
        with self.decisions_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _set_text_widget(widget: ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state="disabled")


def build_review_items(
    audit_json_path: Path,
    chapter_regex: str | None = None,
    *,
    ignore_non_line_audio: bool = True,
) -> tuple[list[ReviewItem], int]:
    payload = read_json(audit_json_path)
    chapters = payload.get("chapters", [])
    if not isinstance(chapters, list):
        return [], 0

    filter_re = re.compile(chapter_regex) if chapter_regex else None
    review_items: list[ReviewItem] = []
    skipped_non_line = 0

    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        chapter_dir_raw = chapter.get("chapterDir")
        if not isinstance(chapter_dir_raw, str):
            continue

        chapter_dir = Path(chapter_dir_raw).resolve()
        chapter_name = chapter_dir.name
        if filter_re and not filter_re.search(chapter_name):
            continue

        clips = chapter.get("clips", [])
        if not isinstance(clips, list):
            clips = []

        regenerate = chapter.get("regenerate", [])
        if not isinstance(regenerate, list):
            regenerate = []

        for regen_item in regenerate:
            if not isinstance(regen_item, dict):
                continue

            line_id = regen_item.get("lineId") if isinstance(
                regen_item.get("lineId"), int) else None
            audio_path_text = regen_item.get("audioPath") if isinstance(
                regen_item.get("audioPath"), str) else None

            target_source: Path | None = None
            if audio_path_text:
                target_source = Path(audio_path_text).resolve()
                if ignore_non_line_audio and is_non_line_audio_path(target_source, chapter_dir):
                    skipped_non_line += 1
                    continue

            status = str(regen_item.get("status", "unknown"))
            reasons = regen_item.get("reasons") if isinstance(
                regen_item.get("reasons"), list) else []

            matched_clip: dict[str, Any] | None = None
            for clip in clips:
                if not isinstance(clip, dict):
                    continue
                clip_line = clip.get("lineIdFromFile")
                clip_audio = clip.get("audioPath")

                if ignore_non_line_audio and isinstance(clip_audio, str):
                    clip_audio_path = Path(clip_audio).resolve()
                    if is_non_line_audio_path(clip_audio_path, chapter_dir):
                        continue

                if isinstance(line_id, int) and clip_line != line_id:
                    continue
                if audio_path_text and isinstance(clip_audio, str) and clip_audio != audio_path_text:
                    continue

                matched_clip = clip
                break

            expected_text = ""
            asr_text = ""
            candidate_options: list[CandidateOption] = []

            if matched_clip is not None:
                expected = matched_clip.get("expected") if isinstance(
                    matched_clip.get("expected"), dict) else {}
                expected_text = str(expected.get("text", ""))
                asr = matched_clip.get("asr") if isinstance(
                    matched_clip.get("asr"), dict) else {}
                asr_text = str(asr.get("text", ""))

                candidate_entries = matched_clip.get("candidates") if isinstance(
                    matched_clip.get("candidates"), list) else []
                seen_lines: set[int] = set()
                for candidate in candidate_entries:
                    if not isinstance(candidate, dict):
                        continue
                    cand_line = candidate.get("lineId")
                    if not isinstance(cand_line, int):
                        continue
                    if cand_line in seen_lines:
                        continue
                    seen_lines.add(cand_line)
                    score = float(candidate.get("score", 0.0) or 0.0)
                    text = str(candidate.get("text", ""))
                    paths = find_line_audio_paths(chapter_dir, cand_line)
                    candidate_options.append(CandidateOption(
                        line_id=cand_line, score=score, text=text, audio_paths=paths))

            if target_source is None and isinstance(line_id, int):
                possible = find_line_audio_paths(chapter_dir, line_id)
                if possible:
                    target_source = possible[0]

            target_wav, target_regen = to_wav_target(target_source)

            review_items.append(
                ReviewItem(
                    chapter_name=chapter_name,
                    chapter_dir=chapter_dir,
                    status=status,
                    reasons=[str(reason) for reason in reasons],
                    line_id=line_id,
                    expected_text=expected_text,
                    asr_text=asr_text,
                    target_wav=target_wav,
                    target_regen=target_regen,
                    candidates=candidate_options,
                )
            )

    review_items.sort(key=lambda item: (*chapter_sort_key(item.chapter_name),
                      item.line_id if isinstance(item.line_id, int) else 999999))
    return review_items, skipped_non_line


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review flagged ASR dialog lines and apply replacement/regen actions.")
    parser.add_argument(
        "--audit-json",
        type=Path,
        default=Path("C:/temp/book14_asr_audit_00_72/book_asr_audit.json"),
        help="Path to consolidated book_asr_audit.json",
    )
    parser.add_argument(
        "--decisions-log",
        type=Path,
        default=Path(
            "C:/temp/book14_asr_audit_00_72/review_flagged_decisions.jsonl"),
        help="JSONL output for review actions",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(
            "C:/temp/book14_asr_audit_00_72/review_flagged_state.json"),
        help="State file to resume position",
    )
    parser.add_argument(
        "--chapter-regex",
        default=None,
        help="Optional regex to filter chapter names",
    )
    parser.add_argument(
        "--include-non-line-audio",
        action="store_true",
        help="Include _chunk_pipeline/generation_chunks/full_character entries in review.",
    )
    args = parser.parse_args()

    audit_json = args.audit_json.resolve()
    if not audit_json.exists():
        raise SystemExit(f"Audit json not found: {audit_json}")

    items, skipped_non_line = build_review_items(
        audit_json,
        args.chapter_regex,
        ignore_non_line_audio=not args.include_non_line_audio,
    )
    if skipped_non_line > 0 and not args.include_non_line_audio:
        print(
            "Skipped "
            f"{skipped_non_line} non-line audio entries "
            "(_chunk_pipeline/generation_chunks/full_character)."
        )
    if not items:
        print("No flagged items found to review.")
        return

    root = tk.Tk()
    _ = FlaggedReviewApp(
        root=root,
        items=items,
        decisions_log=args.decisions_log.resolve(),
        state_file=args.state_file.resolve(),
    )
    root.mainloop()


if __name__ == "__main__":
    main()
