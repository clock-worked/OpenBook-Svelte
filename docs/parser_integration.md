# Parser Integration

## Current Integration Model

Parser functionality is exposed through the Python FastAPI sidecar in `py_services`, not through a direct per-call Tauri CLI bridge.

Standalone Python maintenance, audit, and smoke scripts now live under `scripts/python` so `py_services` can stay runtime-focused.

Primary modules:

- `py_services/api_server.py` (composition root)
- `py_services/parser_router.py` (parser/chapter endpoints)
- `py_services/root_config_router.py` (book/audio root setup)

## Frontend Call Path

Frontend parser operations flow through:

- `apps/desktop/src/lib/services/parser.ts`
- `apps/desktop/src/lib/services/apiClient.ts`
- Endpoint `POST /api/parse`

Before parser/chapter operations, frontend syncs selected book root with backend:

- `POST /api/set-book-root` (via `fs.ts` root sync helpers)

## Parser/Chapter Endpoints

Owned by `py_services/parser_router.py`:

- `GET /api/list-chapters`
- `POST /api/parse`
- `POST /api/save`
- `POST /api/update-character-stats`
- `POST /api/coref-health`
- `GET /api/modernbooknlp/cache`
- `POST /api/modernbooknlp/cache`

Behavior notes:

- Parse requests accept raw chapter text and parser options.
- ModernBookNLP runs once over the concatenated book and stores its output in
	`.modernbooknlp/`. Chapter parses reuse that cache with chapter-local offsets.
	Changed, added, or removed chapter sources require rebuilding the cache.
- Save writes normalized dialogue payloads under the active backend book root.
- Character stats update can be triggered after dialogue writes.

## Request Models

Shared backend request/response model definitions live in:

- `py_services/api_models.py`

This keeps parser/audio/root routers aligned on a single schema source.

## Running the Backend

From repo root (Windows):

```bash
.venv/Scripts/python.exe py_services/api_server.py
```

Or via uvicorn module style:

```bash
.venv/Scripts/python.exe -m uvicorn py_services.api_server:app --host 0.0.0.0 --port 8000
```

## Smoke Validation

Lane 5 regression check:

```bash
.venv/Scripts/python.exe scripts/python/dev_smoke/router_split_smoke_test.py
```

This verifies parser/audio/vibevoice route registration and key endpoint behavior after backend refactors.

## Related Docs

- `docs/api_contract.md`
- `docs/architecture.md`
- `docs/refactor_plan.md`
