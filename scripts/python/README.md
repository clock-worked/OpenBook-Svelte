# Python Script Inventory

OpenBook now keeps Python code in two distinct zones:

- `py_services/`: backend runtime modules imported by the desktop app, FastAPI routers, or shared backend services.
- `scripts/python/`: standalone developer utilities, batch runners, audits, experiments, smoke tests, and setup helpers.

## Keep vs Move Rules

Keep a file in `py_services/` when at least one of these is true:

- The frontend or backend starts it directly at runtime.
- A FastAPI router or backend service imports it.
- It is a shared support package that backend runtime code imports.

Move a file into `scripts/python/` when all of these are true:

- It is a manual CLI, audit runner, experiment harness, smoke test, or setup helper.
- The frontend does not invoke it as part of normal runtime.
- Its outputs are offline artifacts, reports, or one-shot maintenance changes.

## Current Runtime Files That Intentionally Stay in py_services

- `api_server.py`, `api_runtime_state.py`, `api_models.py`
- `audio_router.py`, `parser_router.py`, `root_config_router.py`, `audio/`
- `audio_generation_service.py`, `chapter_service.py`, `vibevoice_local_service.py`
- `openbook_cli.py`, `update_character_stats.py`
- `openbook_parser/`
- `chapter_audio_test/`

`chapter_audio_test/` stays in `py_services/` on purpose. It started as a standalone helper package, but the current VibeVoice backend router imports it directly for chunked generation, ASR alignment, boundary refinement, and split validation.

## Utility Categories

| Folder | Purpose | Notes |
| --- | --- | --- |
| `asr_validation/` | ASR timestamps, drift audits, validation experiments, regenerate manifests | Offline analysis and repair helpers |
| `audio_batch/` | Manual batch generation flows built around the shared VibeVoice/chapter split pipeline | Not frontend-invoked |
| `publishing/` | Finishing pass and M4B assembly tools | Post-generation packaging |
| `dev_smoke/` | Lightweight backend and model smoke checks | Regression checks, not runtime code |
| `setup/` | Environment/bootstrap helpers for external repos or models | Installation/setup only |

## Import Model For Moved Scripts

Standalone scripts that still need backend support import through `scripts/python/_path_setup.py`.

That bootstrap adds these locations to `sys.path`:

- repo root
- `scripts/python`
- `py_services`
- each immediate category folder under `scripts/python`

This keeps the moved entrypoints runnable from anywhere while still allowing them to reuse backend support modules such as `vibevoice_local_service.py` and `chapter_audio_test/`.

## Running Scripts

From repo root on Windows:

```bash
.venv/Scripts/python.exe scripts/python/<category>/<script>.py ...
```

The same shared `.venv` is still the execution environment. The reorg changes file ownership and documentation, not the Python environment strategy.