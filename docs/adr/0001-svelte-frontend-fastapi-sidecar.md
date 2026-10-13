# Svelte frontend + Python FastAPI sidecar

OpenBook runs as a Svelte desktop app (`apps/desktop`) paired with a Python FastAPI sidecar (`py_services`). The sidecar owns parsing, chapter listing, audio generation/serving, and manifest scans; the frontend's primary user path is the File System Access API, with the sidecar as the save fallback and heavy-lift. All endpoint URLs and cross-cutting error normalization are centralized in one frontend API client, and the backend is split into a composition root plus per-domain routers.

**Considered options**: a direct per-call Tauri CLI bridge for parser functionality was rejected; the Python sidecar behind HTTP keeps the backend swappable and testable independently of the desktop shell.
