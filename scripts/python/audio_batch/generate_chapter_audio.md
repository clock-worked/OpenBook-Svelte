# Generate Chapter Audio (VibeVoice)

This script generates one WAV per dialogue line from a dialogue.json file.
Default backend is direct VibeVoice in Python (no ComfyUI required).

Status: legacy/manual fallback workflow. This script is not part of the frontend runtime path.

## Quick Start (ComfyUI venv)

1) Install deps into the ComfyUI venv:

```bash
C:/Users/Chad/Documents/ComfyUI/.venv/Scripts/python.exe -m pip install -r "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Speech/vibevoice/requirements-vibevoice.txt"
C:/Users/Chad/Documents/ComfyUI/.venv/Scripts/python.exe -m pip install tqdm
```

2) Run on a chapter:

```bash
.venv/Scripts/python.exe scripts/python/audio_batch/generate_chapter_audio.py \
  --backend vibevoice \
  --model_path "C:/Users/Chad/Documents/ComfyUI/models/vibevoice/VibeVoice-Large-Q8" \
  --dialogue "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/01 - A False God & Proactive Measures/dialogue.json"
```

Output goes to: <chapter-folder>/audio_lines/

## Options

- --backend vibevoice | comfy
- --model_path path to local VibeVoice model folder
- --device cuda | mps | cpu
- --cfg_scale float (default 1.3)
- --ddpm_steps int (default 20)
- --seed int
- --output_dir custom output folder
- --start_id, --end_id to limit lines
- --line_id to regenerate one line
- --skip_existing to avoid overwriting existing WAVs
- --dry_run to validate inputs only

## Regenerate a single line

```bash
.venv/Scripts/python.exe scripts/python/audio_batch/generate_chapter_audio.py \
  --backend vibevoice \
  --model_path "C:/Users/Chad/Documents/ComfyUI/models/vibevoice/VibeVoice-Large-Q8" \
  --dialogue "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/01 - A False God & Proactive Measures/dialogue.json" \
  --line_id 92
```

## Hardcoded bits (edit in the script)

- SPEAKER_SAMPLES is a hardcoded map of characterId to WAV sample path.
- DEFAULT_MODEL_PATH points to a local model folder.
- DEFAULT_COMFY_URL, DEFAULT_COMFY_ROOT, DEFAULT_WORKFLOW_PATH are only used when --backend comfy.

## Current limitations

- Speaker mapping is hardcoded. Missing characterId raises an error.
- Dialogue text is not post-processed beyond basic ASCII normalization.
- No automatic voice sample selection or fallbacks.
- Output is always one WAV per line; no concatenation.
- VibeVoice backend requires the vibevoice package and a local model folder.
- CPU mode is very slow. CUDA is recommended.

## ComfyUI backend (optional)

The script can drive ComfyUI if you set --backend comfy. It will:
- Patch the LoadAudio and VibeVoiceSingleSpeakerNode nodes in the workflow.
- Post the prompt to the ComfyUI API.
- Download the generated WAV from ComfyUI output.

Example:

```bash
.venv/Scripts/python.exe scripts/python/audio_batch/generate_chapter_audio.py \
  --backend comfy \
  --workflow "C:/Users/Chad/Documents/ComfyUI/custom_nodes/VibeVoice-ComfyUI/examples/Single-Speaker.json" \
  --comfy_url "http://127.0.0.1:8000" \
  --dialogue "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/01 - A False God & Proactive Measures/dialogue.json"
```
