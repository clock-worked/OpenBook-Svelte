# Architecture

## Current Runtime Shape

OpenBook currently runs as a **Svelte desktop frontend** (`apps/desktop`) plus a **Python FastAPI sidecar** (`py_services`).

- Frontend primary path: **File Browser / File System Access API**.
- Backend role: parsing, chapter listing, save fallback, audio generation/serving, and manifest scans.
- Standalone developer utilities live under `scripts/python` so `py_services` stays focused on runtime modules and shared backend support packages.
- API governance: endpoint map + error handling are centralized in `apps/desktop/src/lib/services/apiClient.ts` and documented in `docs/api_contract.md`.

## Backend Composition (Lane 5)

The Python service is now split into composition root + routers.

- Composition root: `py_services/api_server.py`
- Shared runtime state: `py_services/api_runtime_state.py`
- Root configuration router: `py_services/root_config_router.py`
- Parser/chapter router: `py_services/parser_router.py`
- Audio router: `py_services/audio_router.py`
- VibeVoice integration router: `py_services/audio/vibevoice.py`

Design intent:
- `api_server.py` owns middleware + router registration only.
- Router modules own endpoint logic by domain.
- VibeVoice stays an integration boundary (adapter), not core app logic.
- Standalone batch/audit/smoke/setup scripts live outside this folder under `scripts/python`.

## Frontend Architecture (Desktop App)

Key service boundaries in `apps/desktop/src/lib/services`:

- `fs.ts`: File System Access API + backend root sync + chapter file reads/writes.
- `parser.ts`: parser API calls.
- `audio.ts`: audio generation, manifests, and audio URL/path behavior.
- `voices.ts`, `vibevoice.ts`: voice discovery/assignment-related backend calls.
- `apiClient.ts`: canonical endpoint map, base URL, and error normalization.
- `apiContracts.ts`: shared frontend-side API request/response shapes.

Stores remain orchestration-focused (Lane 4):
- `bookState.ts`, `bookCharacters.ts`, `speakers.ts`, `settings.ts`, `audio.ts`.

## Data Layout (Current)

At book root (example):

- `characters/` (v3.0 — one file per character, GUID identity; the shared `py_services/character_store.py` loader is the single backend read/write path. Dual-I/O folder rules: legacy `characters.json` is read-only fallback, and once `characters/` holds valid v3.0 files it must not be rewritten — invariant IN-2)
- `voices.json`
- `settings.json` (optional)
- `<Chapter>/chapter.txt`
- `<Chapter>/dialogue.json` (current write target)
- `<Chapter>/audio_lines/...` (generated clips + manifests)

Policy direction:
- read legacy where needed,
- write current schema (`dialogue.json` v3).

## Validation & Ops

Lane 5 smoke test:

- `scripts/python/dev_smoke/router_split_smoke_test.py`

Run:

```bash
.venv/Scripts/python.exe scripts/python/dev_smoke/router_split_smoke_test.py
```

The smoke test validates route registration and key edge behavior (including audio root/file/path guard handling).

## Related Docs

- API contract: `docs/api_contract.md`
- Parser details: `docs/parser_integration.md`
- Refactor tracker: `docs/refactor_plan.md`
- Schema docs: `docs/schema_v2.md`, `docs/schema_v3.md`
