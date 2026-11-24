# Candidate 4 — Give the audio manifest an owner

**Status:** designed (decision record below) — not started
**Part of:** architecture review 2026-11-06, candidates 1–8
**Companion issue:** see `docs/architecture_deepening/README.md`

## Problem

The character audio manifest (v2.0 schema: clips, `metadata.totalClips/chapters/sources/voiceIds/primaryVoiceId`) has **3 independent BE writers** and **2 FE reader subsystems with separate caches**:

- BE writers: `audio_generation_service.py` `_update_manifest` (606–693) and `reconcile_character_manifest` (742–961, which also bootstraps manifests from orphan files, 777–835); `audio/vibevoice.py` `_upsert_character_manifest` (573–689). A clip-field change is a 3-file change.
- FE readers: `audio.ts` `readManifest` (209–305) over HTTP — a 4-step fallback chain (reconcile endpoint → absolute read → relative read) gated by **module-level session latches** (`absoluteReadApiAvailable` / `reconcileManifestApiAvailable`, 77–78) that flip permanently on one 404; and `manifests.ts` over Tauri `invoke` with its own cache — exactly **one caller** (`bookCharacters.ts:269`).

Understanding "why is my manifest not refreshing" requires knowing the latches exist. No FE test touches `readManifest`; no BE test touches the manifest writers.

## Design

**One manifest module BE-side; one reader subsystem FE-side.**

- **BE:** `py_services/audio/manifest_store.py` owns the schema and all writes: `upsert_clip`, `reconcile` (orphan bootstrap), `read`. The three existing writers keep their distinct *triggers* (post-generation, reconciliation, pipeline upsert) but become thin calls into the store; the v2.0 schema is defined once.
- **FE:** `manifests.ts` is folded into `audio.ts`'s HTTP reader (its single caller `bookCharacters.ts:269` switches over) — one cache, one invalidation API. The Tauri path duplicates what the HTTP path already does. `readManifest` keeps its fallback chain (older sidecars genuinely lack the reconcile endpoint) but it moves inside the reader with an **injectable availability probe**, so the latch behavior is testable through the interface.

## Decisions

1. BE writers keep their triggers, share one schema + one write path (the store).
2. Tauri reader folded into the HTTP reader — deletion test: one caller, parallel subsystem, duplicated legacy-format sniffing (`manifests.ts:112–127`).
3. Fallback chain kept (real capability: older sidecars) but made testable via injected probe; latches become internal state of the reader, not module-level globals.

## Fences (out of scope)

- Provider dispatch/routing that calls the store → candidate 3 (the service calls the store after dispatch; no coupling beyond that).
- Manifest *consumer* UI (AudioPanel playback lists) — unchanged.

## Tests

- BE: `manifest_store` unit tests — upsert, reconcile, orphan bootstrap, corrupt-file tolerance (the writers are untested today).
- FE: `readManifest` tests with a mocked transport — latch flips on 404, fallback order, cache invalidation.

## Landing order (one commit each)

1. BE `manifest_store.py` (schema + upsert/reconcile/read); `audio_generation_service` rewired.
2. `audio/vibevoice.py` upsert rewired to the store.
3. FE: fold `manifests.ts` into `audio.ts` (caller switched); `manifests.ts` deleted.
4. `readManifest` fallback with injectable probe + tests.
