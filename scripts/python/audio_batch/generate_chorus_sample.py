"""Experimental: render a multi-voice unison/chorus line with Chatterbox.

Each voice sample renders every line separately (base ChatterboxTTS so
``exaggeration``/``cfg_weight`` are honoured). The per-line clips are then
silence-trimmed, time-stretched to a common length and layered with small
timing offsets, stereo panning and a light room reverb so the result reads as
several people speaking/singing together rather than three unsynchronised takes.

Run with the chatterbox_env interpreter, e.g.:
    chatterbox_env\\Scripts\\python.exe scripts\\python\\audio_batch\\generate_chorus_sample.py
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve

BOOK = Path(r"E:\Resources\A-Practical-Guide-To-Evil")
DEFAULT_CHAPTER = BOOK / "Book-2" / "35-Chapter-29-Stand"
DEFAULT_VOICES = [
    BOOK / "Audio Samples" / "voice-previews" / "en_uk_manchester_19M.mp3",
    BOOK / "Audio Samples" / "voice-previews" / "en_uk_ireland_31M.mp3",
    BOOK / "Audio Samples" / "voice-previews" / "en_uk_blackpool_25M.mp3",
]


def clean_text(text: str) -> str:
    text = re.sub(r"[“”\"]", "", text).strip()
    if text.isupper():
        text = text.capitalize()
    return text


def load_lines(chapter_dir: Path, character_id: str) -> list[dict]:
    data = json.loads((chapter_dir / "dialogue.json").read_text(encoding="utf-8"))
    return [
        {"id": ln["id"], "text": clean_text(ln["text"]), "shout": ln["text"].strip("“”\" .,!").isupper()}
        for ln in data["lines"]
        if ln.get("characterId") == character_id
    ]


def trim(wav: np.ndarray) -> np.ndarray:
    trimmed, _ = librosa.effects.trim(wav, top_db=35)
    return trimmed


def fit_length(wav: np.ndarray, target: int) -> np.ndarray:
    if len(wav) == 0:
        return np.zeros(target, dtype=np.float32)
    rate = len(wav) / target
    out = librosa.effects.time_stretch(wav, rate=rate) if abs(rate - 1.0) > 0.01 else wav
    out = out[:target]
    return np.pad(out, (0, target - len(out))).astype(np.float32)


def dtw_warp(wav: np.ndarray, ref: np.ndarray, sr: int, anchor_s: float = 0.2) -> np.ndarray:
    """Warp ``wav`` so its phonetic timing follows ``ref`` (MFCC DTW + piecewise stretch)."""
    hop = 256
    m_ref = librosa.feature.mfcc(y=ref, sr=sr, n_mfcc=20, hop_length=hop)
    m_wav = librosa.feature.mfcc(y=wav, sr=sr, n_mfcc=20, hop_length=hop)
    _, path = librosa.sequence.dtw(X=m_ref, Y=m_wav, metric="cosine")
    path = path[::-1]
    step = max(1, int(anchor_s * sr / hop))
    anchors = {0: 0}
    for r, w in path:
        if r % step == 0 and r not in anchors:
            anchors[r] = w
    ref_pts = sorted(anchors) + [m_ref.shape[1]]
    wav_pts = [anchors[r] for r in ref_pts[:-1]] + [m_wav.shape[1]]
    out = []
    for i in range(len(ref_pts) - 1):
        a, b = wav_pts[i] * hop, max(wav_pts[i + 1], wav_pts[i] + 1) * hop
        seg = wav[a:b]
        tgt = (ref_pts[i + 1] - ref_pts[i]) * hop
        if len(seg) < 64 or tgt <= 0:
            out.append(np.zeros(max(tgt, 0), dtype=np.float32))
            continue
        out.append(fit_length(seg, tgt))
    return fit_length(np.concatenate(out), len(ref))


def pan(mono: np.ndarray, position: float) -> np.ndarray:
    """Equal-power pan; position in [-1 (left), 1 (right)]."""
    angle = (position + 1) * np.pi / 4
    return np.stack([mono * np.cos(angle), mono * np.sin(angle)], axis=0)


def room_reverb(stereo: np.ndarray, sr: int, decay_s: float = 0.9, wet: float = 0.18) -> np.ndarray:
    rng = np.random.default_rng(7)
    n = int(sr * decay_s)
    env = np.exp(-np.linspace(0, 6, n))
    out = np.empty_like(stereo)
    for ch in range(stereo.shape[0]):
        ir = rng.standard_normal(n) * env
        ir /= np.sqrt(np.sum(ir**2))
        out[ch] = stereo[ch] + wet * fftconvolve(stereo[ch], ir)[: stereo.shape[1]]
    return out


def normalize(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = np.max(np.abs(x))
    return x * (peak / m) if m > 0 else x


def concat_with_gap(segments: list[np.ndarray], sr: int, gap_s: float) -> np.ndarray:
    gap = np.zeros(segments[0].shape[:-1] + (int(sr * gap_s),), dtype=np.float32)
    parts: list[np.ndarray] = []
    for seg in segments:
        parts += [seg, gap]
    return np.concatenate(parts[:-1], axis=-1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chapter-dir", type=Path, default=DEFAULT_CHAPTER)
    ap.add_argument("--character-id", default="V279TY6R", help="Deserter Group")
    ap.add_argument("--voices", type=Path, nargs="+", default=DEFAULT_VOICES)
    ap.add_argument("--takes", type=int, default=1, help="Differently seeded takes per voice")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--exaggeration", type=float, default=0.9)
    ap.add_argument("--shout-exaggeration", type=float, default=1.3)
    ap.add_argument("--cfg-weight", type=float, default=0.3)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--gap", type=float, default=0.25, help="Seconds between lines")
    ap.add_argument("--max-lines", type=int, default=0, help="Limit lines (0 = all)")
    ap.add_argument("--reuse", action="store_true", help="Skip generation if stem exists")
    args = ap.parse_args()

    out_dir = args.out_dir or args.chapter_dir / "audio_test_chorus"
    stems_dir = out_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)

    lines = load_lines(args.chapter_dir, args.character_id)
    if args.max_lines:
        lines = lines[: args.max_lines]
    entries = [
        (voice, take, voice.stem if args.takes == 1 else f"{voice.stem}_take{take}")
        for voice in args.voices
        for take in range(args.takes)
    ]
    print(f"{len(lines)} lines x {len(entries)} voice takes")

    model = None
    sr = 24000

    # clips[v][i] -> mono float32 array
    clips: list[list[np.ndarray]] = []
    for v_idx, (voice, _take, label) in enumerate(entries):
        conds_ready = False
        per_voice: list[np.ndarray] = []
        for ln in lines:
            path = stems_dir / f"{label}__{ln['id']:04d}.wav"
            if not (args.reuse and path.exists()):
                if model is None:
                    import torch
                    from chatterbox.tts import ChatterboxTTS

                    model = ChatterboxTTS.from_pretrained(
                        device="cuda" if torch.cuda.is_available() else "cpu"
                    )
                    sr = model.sr
                if not conds_ready:
                    model.prepare_conditionals(str(voice), exaggeration=args.exaggeration)
                    conds_ready = True
                torch.manual_seed(1000 + v_idx * 97 + ln["id"])
                exag = args.shout_exaggeration if ln["shout"] else args.exaggeration
                wav = model.generate(
                    ln["text"],
                    exaggeration=exag,
                    cfg_weight=args.cfg_weight,
                    temperature=args.temperature,
                )
                sf.write(path, wav.squeeze(0).cpu().numpy(), sr)
                print(f"  [{label}] {ln['id']}: {ln['text']}")
            wav_np, _ = librosa.load(path, sr=sr, mono=True)
            per_voice.append(trim(wav_np))
        clips.append(per_voice)

    n_voices = len(clips)
    pans = np.linspace(-0.6, 0.6, n_voices) if n_voices > 1 else [0.0]
    offsets_ms = [0, 18, 35, 12, 27, 8][:n_voices]

    # 1) Each voice on its own (high exaggeration solo reference)
    for v_idx, (_voice, _take, label) in enumerate(entries):
        solo = concat_with_gap(clips[v_idx], sr, args.gap)
        sf.write(out_dir / f"solo_{label}.wav", normalize(solo), sr)

    # 2) Naive: just sum each voice's full take, no alignment
    solos = [concat_with_gap(c, sr, args.gap) for c in clips]
    longest = max(len(s) for s in solos)
    naive = sum(np.pad(s, (0, longest - len(s))) for s in solos)
    sf.write(out_dir / "chorus_1_naive_overlay.wav", normalize(naive), sr)

    # 3) Aligned: per-line stretch to median duration + offsets + pan + reverb
    aligned_lines, crowd_lines = [], []
    for i in range(len(lines)):
        target = int(np.median([len(clips[v][i]) for v in range(n_voices)]))
        pad = int(sr * 0.06)
        mix = np.zeros((2, target + pad), dtype=np.float32)
        crowd = np.zeros((2, target + pad), dtype=np.float32)
        for v in range(n_voices):
            fitted = fit_length(clips[v][i], target)
            off = int(sr * offsets_ms[v] / 1000)
            placed = np.zeros(target + pad, dtype=np.float32)
            placed[off : off + target] = fitted[: target + pad - off]
            mix += pan(placed, pans[v])
            crowd += pan(placed, pans[v])
            # 4) Crowd: add detuned, delayed doubles of each voice to suggest many singers
            for semis, extra_ms, p_shift in ((-0.35, 22, -0.3), (0.3, 40, 0.3)):
                dbl = librosa.effects.pitch_shift(fitted, sr=sr, n_steps=semis)
                o2 = min(pad, off + int(sr * extra_ms / 1000))
                placed2 = np.zeros(target + pad, dtype=np.float32)
                placed2[o2 : o2 + target] = dbl[: target + pad - o2]
                crowd += 0.55 * pan(placed2, np.clip(pans[v] + p_shift, -1, 1))
        aligned_lines.append(mix)
        crowd_lines.append(crowd)

    aligned = room_reverb(concat_with_gap(aligned_lines, sr, args.gap), sr)
    sf.write(out_dir / "chorus_2_aligned.wav", normalize(aligned).T, sr)
    crowd_mix = room_reverb(concat_with_gap(crowd_lines, sr, args.gap), sr, decay_s=1.4, wet=0.28)
    sf.write(out_dir / "chorus_3_crowd.wav", normalize(crowd_mix).T, sr)

    # 5) DTW: warp every voice onto the timing of the median-length take per line
    dtw_lines = []
    for i in range(len(lines)):
        lens = [len(clips[v][i]) for v in range(n_voices)]
        ref_v = int(np.argsort(lens)[len(lens) // 2])
        ref = clips[ref_v][i]
        pad = int(sr * 0.06)
        mix = np.zeros((2, len(ref) + pad), dtype=np.float32)
        for v in range(n_voices):
            warped = ref if v == ref_v else dtw_warp(clips[v][i], ref, sr)
            off = int(sr * offsets_ms[v] / 1000)
            placed = np.zeros(len(ref) + pad, dtype=np.float32)
            placed[off : off + len(ref)] = warped
            mix += pan(placed, pans[v])
        dtw_lines.append(mix)
    dtw_mix = room_reverb(concat_with_gap(dtw_lines, sr, args.gap), sr)
    sf.write(out_dir / "chorus_4_dtw_aligned.wav", normalize(dtw_mix).T, sr)

    print(f"Done -> {out_dir}")


if __name__ == "__main__":
    main()
