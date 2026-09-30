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
- Quote-attribution experiments and benchmark ledger: `docs/quote_attribution_experiments.md`

## Fresh Clone Setup

Prerequisites: Git, Node.js 20+, and Python 3.11. From the repository root:

```bash
python scripts/bootstrap.py
```

This initializes the VibeVoice-ComfyUI submodule, creates `.venv`, installs the
backend dependencies, and runs `npm ci` for the desktop app. Large model downloads
are opt-in:

Copy `py_services/.env.example` to `py_services/.env` when using Gemini,
ElevenLabs, Vercel JEV, or custom local-model settings. The real `.env` remains
ignored.

```bash
# VibeVoice Q8 model used by the local audio backend (about 11.6 GB)
python scripts/bootstrap.py --skip-node --skip-python --with-vibevoice

# Isolated Chatterbox environment; use the CUDA 12.4 PyTorch wheels
python scripts/bootstrap.py --skip-node --skip-python --with-chatterbox --torch-index-url https://download.pytorch.org/whl/cu124

# Pinned OpenJEV adapter, base model, and loader under .models/openjev
python scripts/bootstrap.py --skip-node --skip-python --with-openjev
```

Run `python scripts/bootstrap.py --dry-run --with-vibevoice` to inspect setup
actions without installing or downloading anything. Model and environment folders
are intentionally ignored by Git.

## Backend Smoke Check

```bash
.venv/Scripts/python.exe scripts/python/dev_smoke/router_split_smoke_test.py
```
