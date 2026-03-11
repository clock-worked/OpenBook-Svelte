import json
import re
import shutil
import subprocess
from pathlib import Path

book_dir = Path(r"C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14")
build_dir = book_dir / "_m4b_build"
build_dir.mkdir(parents=True, exist_ok=True)

abs_dir = Path(r"C:/Users/Chad/Documents/1-DOCKER_SERVERS/audiobookshelf/audiobooks/Zogarth/The Primal Hunter/The Primal Hunter 14")
abs_dir.mkdir(parents=True, exist_ok=True)
abs_base = "The Primal Hunter 14_ A LitRPG Adventure - 14"

cover = book_dir / "cover.jpeg"
if not cover.exists():
    cover_jpg = book_dir / "cover.jpg"
    if cover_jpg.exists():
        shutil.copy2(cover_jpg, cover)
    else:
        raise SystemExit("Missing cover image (cover.jpeg/cover.jpg)")


def run(cmd):
    print("RUN:", " ".join(str(c) for c in cmd), flush=True)
    p = subprocess.run(cmd, text=True)
    if p.returncode != 0:
        raise SystemExit(f"Command failed ({p.returncode}): {' '.join(str(c) for c in cmd)}")


def run_capture(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"Command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr}")
    return p.stdout


def ffprobe_duration(path: Path) -> float:
    out = run_capture([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ]).strip()
    return float(out)


def ffprobe_audio_params(path: Path) -> tuple[int, int]:
    out = run_capture([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]).strip().splitlines()
    if len(out) < 2:
        raise SystemExit(f"Could not read audio params for {path}")
    sample_rate = int(out[0])
    channels = int(out[1])
    return sample_rate, channels


def channel_layout_for(channels: int) -> str:
    if channels <= 1:
        return "mono"
    return "stereo"


def chapter_number(name: str) -> int:
    m = re.match(r"^(\d+)\s*-\s*", name)
    return int(m.group(1)) if m else 10**9


def fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

print("STEP 1: prepare format-matched silence files", flush=True)
silence_cache: dict[tuple[int, int, int], Path] = {}


def ensure_silence(duration_sec: int, sample_rate: int, channels: int) -> Path:
    key = (duration_sec, sample_rate, channels)
    if key in silence_cache:
        return silence_cache[key]

    cl = channel_layout_for(channels)
    out_path = build_dir / f"silence_{duration_sec}s_{sample_rate}hz_{channels}ch.wav"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={cl}",
        "-t", str(duration_sec), "-ar", str(sample_rate), "-ac", str(channels),
        "-c:a", "pcm_s16le", str(out_path),
    ])
    silence_cache[key] = out_path
    return out_path

print("STEP 2: build chapter wavs", flush=True)
chapter_dirs = sorted(
    [p for p in book_dir.iterdir() if p.is_dir() and re.match(r"^\d+\s*-\s*", p.name)],
    key=lambda p: chapter_number(p.name),
)
chapter_outputs = []

for chapter in chapter_dirs:
    audio_root = chapter / "audio_lines"
    if not audio_root.exists():
        continue

    wavs = [p for p in audio_root.rglob("*.wav") if ".bak-" not in p.name and not p.name.endswith(".REGEN.wav")]
    if not wavs:
        continue

    def line_key(p: Path):
        m = re.match(r"^(\d+)-", p.name)
        line_id = int(m.group(1)) if m else 10**9
        return (line_id, str(p.parent).lower(), p.name.lower())

    wavs = sorted(wavs, key=line_key)
    first_wav = wavs[0]
    sample_rate, channels = ffprobe_audio_params(first_wav)
    silence3 = ensure_silence(3, sample_rate, channels)
    silence5 = ensure_silence(5, sample_rate, channels)
    rest_wavs = wavs[1:]

    ch_num = chapter_number(chapter.name)
    ch_title = re.sub(r"^\d+\s*-\s*", "", chapter.name)

    add_title_pause = False
    m = re.match(r"^(\d+)-", first_wav.name)
    first_id = int(m.group(1)) if m else None
    if first_id is not None:
        dpath = chapter / "dialogue.json"
        if dpath.exists():
            payload = json.loads(dpath.read_text(encoding="utf-8"))
            line = next((x for x in payload.get("lines", []) if isinstance(x, dict) and x.get("id") == first_id), None)
            first_text = str((line or {}).get("text", "")).strip().lower()
            if first_text.startswith("chapter "):
                add_title_pause = True

    seq = [first_wav]
    if add_title_pause:
        seq.append(silence3)
    seq.extend(rest_wavs)
    seq.append(silence5)

    seq_list = build_dir / f"chapter_{ch_num:03d}_seq.txt"
    with seq_list.open("w", encoding="utf-8") as f:
        for item in seq:
            safe = str(item).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    chapter_wav = build_dir / f"chapter_{ch_num:03d}.wav"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0", "-i", str(seq_list),
        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le",
        str(chapter_wav),
    ])

    duration = ffprobe_duration(chapter_wav)
    chapter_outputs.append((ch_num, ch_title, chapter_wav, duration, add_title_pause))
    print(f"CHAPTER_DONE {ch_num:03d} pause3={add_title_pause} dur={duration:.3f}s", flush=True)

if not chapter_outputs:
    raise SystemExit("No chapters built")

print(f"STEP 3: concat {len(chapter_outputs)} chapters and encode AAC", flush=True)
book_list = build_dir / "book_concat_from_wav.txt"
with book_list.open("w", encoding="utf-8") as f:
    for _, _, ch_wav, _, _ in chapter_outputs:
        safe = str(ch_wav).replace("'", "'\\''")
        f.write(f"file '{safe}'\n")

full_m4a = build_dir / "book14_full_from_wav.m4a"
run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-f", "concat", "-safe", "0", "-i", str(book_list), "-c:a", "aac", "-b:a", "96k", str(full_m4a)])

print("STEP 4: write chapter metadata", flush=True)
meta = build_dir / "chapters.ffmeta"
start_ms = 0
with meta.open("w", encoding="utf-8") as f:
    f.write(";FFMETADATA1\n")
    f.write("title=Primal Hunter Book 14\n")
    f.write("artist=Zogarth\n")
    f.write("album=Primal Hunter Book 14\n")
    for ch_num, ch_title, _, dur, _ in chapter_outputs:
        dur_ms = int(round(dur * 1000))
        end_ms = start_ms + dur_ms
        f.write("[CHAPTER]\nTIMEBASE=1/1000\n")
        f.write(f"START={start_ms}\nEND={end_ms}\n")
        f.write(f"title={ch_num:02d} - {ch_title}\n")
        start_ms = end_ms

print("STEP 5: mux m4b + cover", flush=True)
final_m4b = book_dir / "Book-14-complete.m4b"
run([
    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
    "-i", str(full_m4a), "-i", str(meta), "-i", str(cover),
    "-map", "0:a", "-map", "2:v", "-map_metadata", "1",
    "-c:a", "copy", "-c:v", "mjpeg", "-disposition:v", "attached_pic",
    str(final_m4b),
])

print("STEP 6: deploy files", flush=True)
out_m4b = abs_dir / f"{abs_base}.m4b"
out_chapters = abs_dir / f"{abs_base}.chapters.txt"
shutil.copy2(final_m4b, out_m4b)

probe_json = json.loads(run_capture(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_chapters", str(out_m4b)]))
lines = [f"## total-duration:: {fmt_ts(float(probe_json.get('format', {}).get('duration', 0.0)))}"]
for ch in probe_json.get("chapters", []):
    lines.append(f"{fmt_ts(float(ch.get('start_time', 0.0)))} {ch.get('tags', {}).get('title', 'Untitled Chapter')}")
out_chapters.write_text("\n".join(lines) + "\n", encoding="utf-8")

print("FINAL_M4B", final_m4b, flush=True)
print("DEPLOYED_M4B", out_m4b, flush=True)
print("DEPLOYED_CHAPTERS", out_chapters, flush=True)
print("CHAPTER_COUNT", len(chapter_outputs), flush=True)
print("TITLE_PAUSE_COUNT", sum(1 for *_, p in chapter_outputs if p), flush=True)
print("TOTAL_DURATION", lines[0].replace('## total-duration:: ', ''), flush=True)
