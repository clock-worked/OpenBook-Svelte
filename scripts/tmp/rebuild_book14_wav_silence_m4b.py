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
    cover = book_dir / "cover.jpg"
if not cover.exists():
    raise SystemExit("Missing cover image")


def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode != 0:
        raise SystemExit(f"Command failed ({p.returncode}): {' '.join(str(c) for c in cmd)}\n{p.stderr}")


def run_capture(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"Command failed ({p.returncode}): {' '.join(str(c) for c in cmd)}\n{p.stderr}")
    return p.stdout


def ffprobe_duration(path: Path) -> float:
    out = run_capture([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ]).strip()
    return float(out)


def ch_num(name: str):
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


silence3 = build_dir / "silence_3s.wav"
silence5 = build_dir / "silence_5s.wav"
run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "3", "-c:a", "pcm_s16le", str(silence3)])
run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "5", "-c:a", "pcm_s16le", str(silence5)])

chapter_dirs = sorted(
    [p for p in book_dir.iterdir() if p.is_dir() and re.match(r"^\d+\s*-\s*", p.name)],
    key=lambda p: ch_num(p.name)
)
chapter_map = {ch_num(p.name): re.sub(r"^\d+\s*-\s*", "", p.name) for p in chapter_dirs}

chapter_wavs = []
for chapter in chapter_dirs:
    audio_root = chapter / "audio_lines"
    if not audio_root.exists():
        continue

    wavs = [
        p for p in audio_root.rglob("*.wav")
        if ".bak-" not in p.name and not p.name.endswith(".REGEN.wav")
    ]
    if not wavs:
        continue

    def line_key(path: Path):
        m = re.match(r"^(\d+)-", path.name)
        line_id = int(m.group(1)) if m else 10**9
        return (line_id, str(path.parent).lower(), path.name.lower())

    wavs = sorted(wavs, key=line_key)
    first, rest = wavs[0], wavs[1:]
    number = ch_num(chapter.name)

    add_pause = False
    m = re.match(r"^(\d+)-", first.name)
    if m:
        line_id = int(m.group(1))
        dpath = chapter / "dialogue.json"
        if dpath.exists():
            payload = json.loads(dpath.read_text(encoding="utf-8"))
            line = next((x for x in payload.get("lines", []) if isinstance(x, dict) and x.get("id") == line_id), None)
            text = str((line or {}).get("text", "")).strip().lower()
            if text.startswith("chapter "):
                add_pause = True

    sequence = [first]
    if add_pause:
        sequence.append(silence3)
    sequence.extend(rest)
    sequence.append(silence5)

    seq_path = build_dir / f"chapter_{number:03d}_seq.txt"
    with seq_path.open("w", encoding="utf-8") as f:
        for item in sequence:
            safe = str(item).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    chapter_wav = build_dir / f"chapter_{number:03d}.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(seq_path), "-c:a", "pcm_s16le", str(chapter_wav)])
    chapter_wavs.append((number, chapter_map.get(number, f"Chapter {number}"), chapter_wav, ffprobe_duration(chapter_wav)))

if not chapter_wavs:
    raise SystemExit("No chapter wavs generated")

book_concat = build_dir / "book_concat_from_wav.txt"
with book_concat.open("w", encoding="utf-8") as f:
    for _, _, wav, _ in chapter_wavs:
        safe = str(wav).replace("'", "'\\''")
        f.write(f"file '{safe}'\n")

full_m4a = build_dir / "book14_full_from_wav.m4a"
run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(book_concat), "-c:a", "aac", "-b:a", "96k", str(full_m4a)])

meta = build_dir / "chapters.ffmeta"
start_ms = 0
with meta.open("w", encoding="utf-8") as f:
    f.write(";FFMETADATA1\n")
    f.write("title=Primal Hunter Book 14\n")
    f.write("artist=Zogarth\n")
    f.write("album=Primal Hunter Book 14\n")
    for number, title, _, duration in chapter_wavs:
        dur_ms = int(round(duration * 1000))
        end_ms = start_ms + dur_ms
        f.write("[CHAPTER]\nTIMEBASE=1/1000\n")
        f.write(f"START={start_ms}\nEND={end_ms}\n")
        f.write(f"title={number:02d} - {title}\n")
        start_ms = end_ms

final_m4b = book_dir / "Book-14-complete.m4b"
run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(full_m4a), "-i", str(meta), "-i", str(cover), "-map", "0:a", "-map", "2:v", "-map_metadata", "1", "-c:a", "copy", "-c:v", "mjpeg", "-disposition:v", "attached_pic", str(final_m4b)])

out_m4b = abs_dir / f"{abs_base}.m4b"
out_chapters = abs_dir / f"{abs_base}.chapters.txt"
shutil.copy2(final_m4b, out_m4b)

probe = json.loads(run_capture(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_chapters", str(out_m4b)]))
lines = [f"## total-duration:: {fmt_ts(float(probe.get('format', {}).get('duration', 0.0)))}"]
for chapter in probe.get("chapters", []):
    lines.append(f"{fmt_ts(float(chapter.get('start_time', 0.0)))} {chapter.get('tags', {}).get('title', 'Untitled Chapter')}")
out_chapters.write_text("\n".join(lines) + "\n", encoding="utf-8")

print("DEPLOYED_M4B", out_m4b)
print("DEPLOYED_CHAPTERS", out_chapters)
print("CHAPTER_COUNT", len(chapter_wavs))
print("TOTAL_DURATION", lines[0].replace('## total-duration:: ', ''))
