# Candidate 7 — One pipeline progress record

**Status:** designed (decision record below) — not started
**Part of:** architecture review 2026-11-06, candidates 1–8
**Companion issue:** see `docs/architecture_deepening/README.md`

## Problem

One concept — "show the user what the long synchronous run is doing" — is split across **7 files in 3 representations**, with the two sides of the wire each deriving the same facts independently:

- BE writes `progress.json` at phase boundaries: `_write_pipeline_progress` (`audio/vibevoice.py:742–798`) called from 8 sites inside the 440-line `_run_character_chunk_pipeline` (801–1239). The work-dir location is derived by `_resolve_character_work_root` (695–709) **and re-derived independently** by the progress route (1331–1361) and by `_resolve_character_work_dir` (712–739, which re-runs `_normalize_character_lines` just to recompute the dir).
- FE builds the poll URL by hand (`apiClient.ts:55–64`); `audioPipelineProgress.ts:120–164` polls with a 5-second startedAt skew buffer, change-detection on `(phase, chunkIndex)`, in-flight guard.
- `audio.ts:489–506` must compute `crossChapter` **itself** from `resolvePlannedLineChapterTitle` — re-deriving the single/cross-chapter distinction the BE derives in `_resolve_character_work_root` (via `input_chapters`). The two derivations must agree or the poll reads the wrong `progress.json`. **No test pins this contract.**
- `AudioPanel.svelte:963–986` then *re-scales* `chunkIndex/chunkTotal` into a pseudo line-count (`Math.floor((chunkIndex/chunkTotal) * payload.lines.length)`) — a third, client-invented representation.
- Phase strings (`'generating'`, `'transcribing'`, …) are magic strings shared across BE call sites, the FE `phaseLabel` switch (`audioPipelineProgress.ts:13–39`), and the `AudioQueueItem.phase` field. `phasePercent` (`audioPipelineProgress.ts:46–67`) is exported and used **nowhere** — dead.

## Design

**The progress record is self-describing; the FE is a dumb poller + display.**

- **BE:** `py_services/audio/pipeline_progress.py` owns: work-dir resolution (**one function** — all three current derivation sites call it), the record schema (phase as an enum, `chunkIndex`/`chunkTotal`, `startedAt`, and **`scope: single | cross`** — the BE computes it, so the FE never re-derives it), and the atomic write (the existing, well-tested `_write_pipeline_progress` moves here).
- **FE:** one progress model. The poller (`audioPipelineProgress.ts`) returns the record; the display reads it directly. `audio.ts`'s independent `crossChapter` derivation is **deleted** — the record says. `AudioPanel`'s chunk→line rescale is **deleted** — the bar shows true chunk progress. `phasePercent` deleted.
- **Phase names:** enum in the BE, typed union in the FE, pinned by a small shared vector (consistent with the candidate 3 precedent).

## Decisions

1. The BE is the single source of truth for work-dir **and** scope; the FE derives neither.
2. The panel's line-count rescale is dropped — **true progress over fake precision** (a bar that says "chunk 3 of 7" is honest; a fake line-count is not).
3. `phasePercent` deleted (dead code).
4. The existing good parts stay: atomic tmp+replace write, stable `startedAt`, field carry-over, corrupt-file tolerance — they move into the module, not rewritten.

## Fences (out of scope)

- The queue job's progress *field* that consumes this record → candidate 8.
- The engine chunk plan whose counts feed the record → candidate 6 (plan-time sub-split makes the counts true; this candidate just carries them).

## Tests

- BE: work-dir resolution + record schema (extend the existing `test_pipeline_progress.py`, which currently covers only the two pure helpers).
- FE: poller tests with a mocked `apiClient` — skew buffer, change detection, stop semantics (untested today).
- Vector: record shape (phase names) pinned for both languages.

## Landing order (one commit each)

1. BE `pipeline_progress.py` (work-dir resolution + schema + atomic write).
2. Pipeline rewired: 8 write sites + 3 work-dir sites call the module.
3. Progress route returns the self-describing record (includes `scope`).
4. FE: progress model; `audio.ts` `crossChapter` derivation deleted; panel rescale deleted; `phasePercent` deleted.
5. Tests (BE + FE + vector).
