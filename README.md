# OpenBook-Svelte

OpenBook is a desktop-first workflow for chapter parsing, dialogue review, character/voice assignment, and audio generation.

## Project Layout

- Frontend app: `apps/desktop`
- Python runtime backend: `py_services`
- Standalone Python utilities: `scripts/python`
- Architecture and planning docs: `docs`

## Current Runtime Model

- Frontend: Svelte desktop app using File System Access API as the primary path.
- Backend: FastAPI sidecar with split routers (root config, parser, audio, vibevoice).
- API contract and endpoint governance: `docs/api_contract.md`.

## Key Docs

- Architecture: `docs/architecture.md`
- Parser integration: `docs/parser_integration.md`
- API contract: `docs/api_contract.md`
- Refactor tracker: `docs/refactor_plan.md`
- Schema references: `docs/schema_v2.md`, `docs/schema_v3.md`

## Backend Smoke Check

```bash
.venv/Scripts/python.exe scripts/python/dev_smoke/router_split_smoke_test.py
```
