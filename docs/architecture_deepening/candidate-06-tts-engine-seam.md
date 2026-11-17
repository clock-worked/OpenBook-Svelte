# Candidate 6 — Name the TTS engine seam

**Status:** designed (decision record below) — not started
**Part of:** architecture review 2026-11-06, candidates 1–8
**Companion issue:** see `docs/architecture_deepening/README.md`
**Consistent with:** ADR-0011 (VibeVoice stays an integration boundary/adapter) — this candidate *names* that seam and makes it real and typed; it does not dissolve it.

## Problem

The chunk-generation core lives in a package named `chapter_audio_test/` that is **production core** (imported by the production router `audio/vibevoice.py:23–45` and `chatterbox_local_service.py:14–17`) — a reader cannot tell benchmark sandbox from core. Around it:

- **Two chunking regimes with no shared constant:** `ChatterboxLocalService(max_chunk_chars=450)` (`chatterbox_local_service.py:48`) vs `DEFAULT_MIN_CHUNK_CHARS = 800` (`audio/vibevoice.py:48`). In the character pipeline, a ≤800-char chunk is handed to Chatterbox, which **silently re-chunks it to ≤450** (`chatterbox_local_service.py:185–218`) — the pipeline's `chunkIndex/chunkTotal` progress (and the FE's rescaling of it) counts 800-chunks while the engine actually makes 450-calls.
- **Provider asymmetry:** `VibeVoiceLocalService.generate_audio` does *no* chunking; only Chatterbox gets long-text protection on the single-line path.
- **Five text normalizers** with slightly different replacement sets: `AudioGenerationService._normalize_text` (996–1015), `vibevoice_local_service.normalize_text` (53–79), `chapter_audio_test/text_utils.normalize_chapter_text`, `audio/vibevoice.py:_normalize_generation_text` (285–296), `generate_chapter_audio.py` (147+). What text an engine hears depends on which of the five the path happens to traverse.
- **The chunk-generator protocol is untyped and exists in 4 copies:** `generation.py:246–258` duck-types `service.generate_audio(text=, sample_path=, output_path=, seed=)`; the pipeline defines `_ServiceChunkGenerator` **as a class inside the function body** (`audio/vibevoice.py:874–919`) to adapt `AudioGenerationService` to it. The "engine" concept has no named interface. (Telling detail: `test_chatterbox_chunking.py:59–74` must bypass `__init__` with `object.__new__` and hand-set 12 attributes — construction is not testable through the interface.)

## Design

**One named interface at the engine seam; the plan knows the engine's limits.**

- **Rename** `chapter_audio_test/` → `chapter_audio/` — mechanical import rename; the package is core.
- **`AudioEngine` protocol** (the named interface): `generate_audio(text, sample_path, output_path, seed)`, plus declared `max_chunk_chars` and `normalize(text)`. `ChatterboxLocalService` and `VibeVoiceLocalService` become the **two adapters** at the seam — with two genuine adapters it is a real seam, not a hypothetical one. The in-function-body `_ServiceChunkGenerator` adapter moves to the composition root.
- **Kill the nested re-chunk:** the chunk plan accounts for the engine's declared limit **at plan time** — 800-char alignment chunks are sub-split into the engine's 450-char chunks inside the plan, so progress counts are true and no engine silently re-chunks. (800 stays the pipeline/WhisperX alignment granularity; 450 becomes Chatterbox's declared property, not a hidden constant.)
- **Normalizers organized behind the adapters:** each engine adapter owns its normalization (genuinely per-engine — the replacement sets differ for good reasons); shared primitives live in `chapter_audio/text_utils`. The five smeared copies become: per-adapter normalization + one shared utility, nothing in between.

## Decisions

1. Package rename is mechanical; done first.
2. Plan-time sub-split (true progress) over "let the engine re-chunk" (invisible progress).
3. Per-engine normalization stays per-engine but is **declared on the adapter** — the "which normalization does this engine hear" question has one answer: the adapter's.
4. VibeVoice's no-chunking asymmetry is made explicit by declaring `max_chunk_chars` (its limit is effectively unbounded/one-call; the plan simply doesn't sub-split it).

## Fences (out of scope)

- Provider routing/classification (which engine a request goes to) → candidate 3 (the profile names the engine adapter).
- The progress record that consumes the plan's chunk counts → candidate 7.

## Tests

- `test_chatterbox_chunking.py` extended: plan-time sub-split produces true chunk counts; the fake model implements `AudioEngine` cleanly (the `object.__new__` hack goes away).
- Per-adapter normalizer tests (each normalization set pinned).
- Existing pipeline behavior pinned by the chunking regression tests.

## Landing order (one commit each)

1. Rename `chapter_audio_test/` → `chapter_audio/` (imports).
2. `AudioEngine` protocol; both services declare `max_chunk_chars` + `normalize`.
3. Plan-time sub-split in the chunk plan; silent re-chunk removed from `ChatterboxLocalService`.
4. Adapter wiring at the composition root (in-function-body class deleted).
