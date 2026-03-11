# ASR Validation + Experiment Workflow

This document defines the ASR artifact format and how to run two experiments without changing app UI yet:

1. **Drift validation + word timestamps** for existing generated clips.
2. **Merged generation viability** (`Character + narration + Character`) with ASR-based split suggestions.
3. **Punctuation A/B** testing for short-segment truncation (`comma` vs `period`).

---

## 1) Word-timestamp artifact format

Use `py_services/asr_word_timestamps.py` to generate per-clip artifacts.

Canonical format version:

- `formatVersion: "asr-word-timestamps/v1"`

Key fields:

- `audio.fileName`, `audio.audioPath`
- `transcript.text`
- `words[]`
  - `index`
  - `word`
  - `normalized`
  - `startSec`
  - `endSec`
  - `durationSec`
  - `confidence`
- `quality.wordCount`, `quality.timedWordCount`, `quality.timingCoverageRatio`
- Optional `validation` (when source text is provided)
  - `sourceText`
  - `sourceWordCount`
  - `asrWordCount`
  - `textSimilarity`

This is the format intended for future per-word highlighting in playback (if ASR is present for the clip).

---

## 2) Generate ASR timestamps for existing clips

Example (chapter-level scan):

```bash
.venv/Scripts/python.exe py_services/asr_word_timestamps.py \
  --audio-dir "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/03 - The Most Important Question/audio_lines" \
  --dialogue "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/03 - The Most Important Question/dialogue.json" \
  --model microsoft/VibeVoice-ASR
```

Outputs default to `<audio-dir>/asr_words/*.asr.json`.

---

## 3) Merged generation experiment (Character + narration + Character)

Use `py_services/asr_experiment_runner.py merged-split`.

What it does:

1. Uses an existing merged clip (`--merged-audio`) **or** generates one clip from combined text (`--sample-path`).
2. Runs ASR with word timestamps.
3. Fuzzy-matches segment A / narration / segment B to ASR tokens.
4. Writes a report with suggested time ranges to keep/remove.

Outputs:

- `<output-dir>/<merged-name>.asr.json`
- `<output-dir>/merged_split_experiment.json`

Example using existing merged audio:

```bash
.venv/Scripts/python.exe py_services/asr_experiment_runner.py merged-split \
  --output-dir "C:/temp/asr_exp/ch03_line_pattern" \
  --merged-audio "C:/temp/asr_exp/ch03_line_pattern/merged_character_full.wav" \
  --segment-a-text "Right," \
  --narration-text "Carmen said, nodding." \
  --segment-b-text "Shamans are generally known to come in two forms: the ones who create a bond with a single elemental or other spirit-like entity they then grow alongside, and the more religious sort who form a bond with a being far more powerful than themselves, sometimes even gods." \
  --asr-model microsoft/VibeVoice-ASR
```

Example generating merged audio with character sample:

```bash
.venv/Scripts/python.exe py_services/asr_experiment_runner.py merged-split \
  --output-dir "C:/temp/asr_exp/ch03_line_pattern" \
  --sample-path "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Audio Samples/carmen-b13-c24-60.wav" \
  --segment-a-text "Right," \
  --narration-text "Carmen said, nodding." \
  --segment-b-text "Shamans are generally known to come in two forms: the ones who create a bond with a single elemental or other spirit-like entity they then grow alongside, and the more religious sort who form a bond with a being far more powerful than themselves, sometimes even gods." \
  --asr-model microsoft/VibeVoice-ASR
```

Read `viability.isViable` and `splitSuggestion` in `merged_split_experiment.json` to decide whether this strategy is reliable enough.

---

## 4) Punctuation A/B experiment (comma vs period)

Use `py_services/asr_experiment_runner.py punctuation-ab`.

What it does:

1. Uses existing variant clips (`--audio-a`, `--audio-b`) **or** generates both variants with same sample.
2. Runs ASR for both.
3. Compares transcript similarity + tail-word match ratio.

Outputs:

- `<output-dir>/<variant-a>.asr.json`
- `<output-dir>/<variant-b>.asr.json`
- `<output-dir>/punctuation_ab_report.json`

Example:

```bash
.venv/Scripts/python.exe py_services/asr_experiment_runner.py punctuation-ab \
  --output-dir "C:/temp/asr_exp/ch03_punctuation" \
  --sample-path "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Audio Samples/carmen-b13-c24-60.wav" \
  --text-a "Right," \
  --text-b "Right." \
  --asr-model microsoft/VibeVoice-ASR
```

Use the report `result.winner` plus `metrics.variantA/variantB` to determine whether replacing terminal commas for tiny segments helps stability.

---

## Notes

- These workflows are intentionally outside app playback/UI for now.
- If the merged split strategy proves stable, the next step is adding a preprocessing path that writes final per-character clips and updates chapter manifests accordingly.
- If ASR artifacts exist for playback clips, desktop can later switch from line-level scrolling to per-word highlighting with `words[].startSec/endSec`.

---

## 5) Concrete run: line 32/33/34 merged baseline comparison

Experiment folder:

- `C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/01 - A False God & Proactive Measures/audio_tests/asr_exp_line_32_33_34`

Baseline audio used for comparison:

- `merged_32_33_34.wav` (single merged clip for line 32 + narration line 33 + line 34)

Key artifacts:

- `merged_32_33_34.asr.json`
- `merged_split_experiment.json`
- `punctuation_ab/punctuation_ab_report.json`

### 5.1 Baseline merged ASR quality

From `merged_32_33_34.asr.json`:

- `validation.textSimilarity = 0.9738`
- `validation.sourceWordCount = 52`
- `validation.asrWordCount = 51`
- Notable miss: final word recognized as `Dodds` instead of `gods`.
- Segment-A token (`Right,`) is not preserved as a standalone token at the start of transcript.

Interpretation: the merged audio transcribes well overall, but the tiny opening character segment is unstable/absorbed.

### 5.2 Split viability against merged baseline

From `merged_split_experiment.json`:

- `matching.segmentA.score = 0.5455` (below threshold)
- `matching.narration.score = 0.7742`
- `matching.segmentB.score = 0.9904`
- `matching.minRequiredScore = 0.75`
- `viability.isViable = false`

Suggested windows produced by ASR matcher:

- `segment_a: 8.52s -> 8.82s` (mislocalized into the middle of segment B)
- `narration removal: 1.68s -> 3.46s`
- `segment_b: 3.86s -> 16.00s`

Interpretation: this run does **not** support reliable post-hoc splitting from one merged generation because segment A cannot be located robustly.

### 5.3 Punctuation A/B for short opening segment

Inputs:

- Variant A text: `Right,`
- Variant B text: `Right.`

From `punctuation_ab/punctuation_ab_report.json`:

- Variant A: `textSimilarity = 1.0`, `tailMatchRatio = 1.0`, `asrWordCount = 1`
- Variant B: `textSimilarity = 0.0`, `tailMatchRatio = 0.0`, `asrWordCount = 223`
- `result.winner = A`

Interpretation: for this sample, comma form is dramatically more stable; period form collapses into a long hallucinated token stream.

### 5.4 Decision from this run

- Keep merged-split marked as experimental/non-viable for this pattern.
- Prefer direct per-line generation for very short character segments.
- If merged generation is retried, gate usage on `viability.isViable == true` and enforce a stronger segment-A confidence threshold.

---

## 6) Chapter/Book audit for missing or wrong-line clips

Use `py_services/asr_chapter_audio_audit.py` to scan generated chapter audio against `dialogue.json` and produce:

- per-chapter audit report
- per-chapter regenerate manifest
- consolidated Book manifest (when `--book-dir` is used)

Defaults are tuned for speed on smaller GPUs:

- `--model distil-whisper/distil-small.en`
- `--torch-dtype float16`
- `--chunk-length-s 20`
- `--batch-size 4`

### 6.1 Single chapter example

```bash
.venv/Scripts/python.exe py_services/asr_chapter_audio_audit.py \
  --chapter-dir "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/03 - The Most Important Question" \
  --model distil-whisper/distil-small.en \
  --device cuda
```

Output (default):

- `<chapter>/audio_lines/asr_audit/chapter_asr_audit.json`
- `<chapter>/audio_lines/asr_audit/regenerate_manifest.json`
- `<chapter>/audio_lines/asr_audit/regenerate_line_ids.txt`

### 6.2 Full Book-14 audit (all chapters)

```bash
.venv/Scripts/python.exe py_services/asr_chapter_audio_audit.py \
  --book-dir "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14" \
  --model distil-whisper/distil-small.en \
  --device cuda \
  --output-dir "C:/temp/book14_asr_audit"
```

Consolidated outputs:

- `C:/temp/book14_asr_audit/book_asr_audit.json`
- `C:/temp/book14_asr_audit/book_regenerate_manifest.json`
- `C:/temp/book14_asr_audit/book_regenerate_line_ids.txt`

`book_regenerate_manifest.json` is the main input for regenerate automation.

### 6.3 Fast precheck (no ASR)

Use dry-run first to catch missing files and naming issues quickly:

```bash
.venv/Scripts/python.exe py_services/asr_chapter_audio_audit.py \
  --book-dir "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14" \
  --output-dir "C:/temp/book14_asr_audit" \
  --dry-run
```
