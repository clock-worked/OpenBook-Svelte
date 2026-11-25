# Candidate 3 — One provider profile, not six copies

**Status:** designed (decision record below) — not started
**Part of:** architecture review 2026-11-06, candidates 1–8
**Companion issue:** see `docs/architecture_deepening/README.md`

## Problem

"is this provider a local chunked engine (VibeVoice/Chatterbox)?" — the classification that decides which endpoint a generation request goes to — is hand-encoded in 6+ places that must agree by convention:

- FE `audio.ts:400` and `:473` — regex `/vibevoice|chatterbox/`
- FE `audioGenerator.ts:92–109` — a third taxonomy (generator-id → concrete provider string)
- BE `audio/vibevoice.py:177–188` — `_is_vibevoice_provider` / `_is_chatterbox_provider` / `_is_local_chunked_provider`
- BE `audio_generation_service.py:35` — `CHATTERBOX_PROVIDER_ALIASES`; ad-hoc list literals again at 132, 145, 598–604 (dispatch *and* `_get_audio_extension`)

A new local engine is a coordinated multi-file edit across two languages, and the two BE routers (`audio_router.py` and `audio/vibevoice.py`) both call the same `AudioGenerationService.generate_audio_line` through **different endpoints with different response shapes**. Related smells: the 3-tier audio-root fallback exists in **3 orderings** (FE `audioRootFor` `audio.ts:159–161`; BE `audio_router.py:123` and `audio/vibevoice.py:1255`; `audio_router.py:361` — the per-request override wins on the BE, loses on the FE), and two dead FE clients (`google-tts.ts`, `elevenlabs.ts`) masquerade as live providers (all real cloud TTS runs BE-side in `audio_generation_service.py`).

## Design

**One provider profile registry per language, pinned by a shared vector.**

- **BE:** `py_services/audio/provider_profile.py` — the registry: canonical name + aliases → profile `{ engine: local_chunked | cloud, endpoint, file extension, engine adapter }`. `audio_generation_service`'s 5-branch if/elif dispatch and `_get_audio_extension` become table lookups; the alias lists are gone. The duplicated router plumbing (`_ensure_audio_service` ×2, the error-string→HTTP-status sniffing ×2 at `audio_router.py:272–280` and `audio/vibevoice.py:1284–1292`) becomes one shared helper.
- **FE:** `audioGenerator.ts` becomes the single FE home: choice → provider → `{ endpointKind: local_chunked | cloud, provider }`. The `audio.ts` regexes are deleted; endpoint selection reads the classification.
- **Parity:** `tests/provider_profile_vectors.json` (provider name → classification + endpoint kind + extension), run by both the py and TS suites — the established cross-language fixture pattern (precedents: `character_identity_vectors.json`, `attribution_policy_vectors.json`).
- **Audio root:** one canonical ordering — explicit audio root → per-request override → book root — implemented once per language (FE `audioRootFor`; one BE resolver in the audio service); all three current call sites use it.
- **Deletions:** `google-tts.ts` and `elevenlabs.ts` (fix the one type import in `stores/credentials.ts`).

## Decisions

1. Registry lives BE-side in the `audio/` package; the FE mirror is a small map in `audioGenerator.ts`. No cross-language code sharing (not a repo pattern) — parity is pinned by the vector file.
2. Audio-root ordering: **per-request override wins** (matches current BE behavior; the FE `audioRootFor` is corrected to match).
3. Dead FE TTS clients deleted.
4. Router plumbing (`_ensure_audio_service`, status sniffing) unified as part of this candidate — it is the same "which endpoint / which status" knowledge.

## Fences (out of scope)

- Engine internals behind the seam (chunking, text normalizers, the untyped generator protocol) → candidate 6.
- Manifest writes after generation → candidate 4.
- Two-routers-into-one-service endpoint *consolidation* (single endpoint, one response shape) is **not** forced — the profile makes the split harmless; a later cleanup may merge them.

## Tests

- Registry: every known alias resolves; unknown provider → clear error (no silent fall-through).
- Vector file run from both suites (classification + endpoint kind + extension parity).
- Unified router helper (status mapping) unit-tested — neither router is tested today.

## Landing order (one commit each)

1. BE `provider_profile.py` registry; `audio_generation_service` dispatch + extension become lookups.
2. Router plumbing unified (`_ensure_audio_service`, status sniffing) + helper tests.
3. FE: classification into `audioGenerator.ts`; `audio.ts` regexes deleted.
4. Audio-root resolver (one ordering, both languages) + call sites switched.
5. Delete `google-tts.ts` / `elevenlabs.ts` (+ `credentials.ts` import fix).
6. `tests/provider_profile_vectors.json` + both-suite runner.
