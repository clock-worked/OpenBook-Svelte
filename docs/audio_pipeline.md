# Audio pipeline

## Overview

- Generate per-line WAVs after conflicts are resolved.
- Naming: `{zeroPaddedLine}-{speaker}.wav` (e.g., `0007-Catherine.wav`).
- Output folder: `{Book}/{Chapter}/` (next to script JSON files).

## Steps

1. Load `<Chapter>.script.json` and `characters.json`.
2. For each line with `chosenSpeaker` and non-empty text, synthesize audio.
3. Write WAV to `{Chapter}` folder; skip unchanged lines.
4. Update `metadata.json` with timestamps and completion flags.

## Implementation (MVP)

- Backend: `gen_audio(text, voice, output_path)` writes a valid WAV stub.
- Frontend: `generateChapterAudio(root, script, characters)` iterates lines and invokes `gen_audio`.

## Adapter shape

```ts
type GenerateAudio = (opts: {
  text: string;
  voice: string;
  sampleRate?: number;
}) => Promise<ArrayBuffer>;
```

## Desktop audition + TTS (Google)

- `.env` under `apps/desktop/src-tauri/`:
  - `GOOGLE_TTS_SA_PATH=src-tauri/<your_service_account>.json`
  - `TTS_PROVIDER=google-tts`

- Commands exposed by Tauri:
  - `tts_list_voices()` → JSON of Chirp3 HD voices
  - `tts_preview(voiceName, accent, text?)` → returns cached WAV path
  - `tts_synthesize_line({ text, voiceName, accent, speakingRate?, isInterjection? })` → cached WAV path

- Caching location: app data dir `OpenBook/audio_cache/preview|lines`
- Frontend helpers: `$lib/services/audio.ts`
- UI: `/book` tab for per-character audition, sorting, and audiobook settings


