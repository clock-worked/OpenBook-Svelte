# OpenBook API Contract (Frontend ↔ Python)

**Status:** Active (Lane 2 foundation)  
**Updated:** 2026-10-07

## Ownership

- Canonical frontend endpoint definitions live in `apps/desktop/src/lib/services/apiClient.ts`.
- Canonical shared request/response payload shapes live in `apps/desktop/src/lib/services/apiContracts.ts`.
- Service modules must import `API_ENDPOINTS` and client helpers instead of hardcoding backend URLs.
- Default API base URL is `http://127.0.0.1:8010`, overrideable with `VITE_API_BASE_URL`.

## Error Classification

All API-facing service code should classify errors for UI handling using:

- `network`: transport failure (backend unavailable, DNS/connection problems)
- `validation`: HTTP `4xx` response from backend
- `server`: HTTP `5xx` response or unexpected server-side failure

Implementation reference:

- `ApiClientError`
- `toApiClientError(...)`
- `apiRequestJson(...)`
- `apiRequestVoid(...)`
- `apiFetch(...)`

## Current Endpoint Map

The following backend routes are currently governed by the centralized client:

- `POST /api/parse`
- `POST /api/list-files`
- `POST /api/delete-file`
- `POST /api/set-book-root`
- `POST /api/set-audio-root`
- `GET /api/list-chapters`
- `POST /api/update-character-stats`
- `POST /api/scan-voice-manifests`
- `POST /api/list-voice-samples`
- `POST /api/read_file_absolute`
- `POST /api/generate_audio_line`
- `POST /api/vibevoice/line`
- `POST /api/vibevoice/chapter`
- `POST /api/delete_character_audio`
- `POST /api/delete_audio_line`
- `GET /api/audio/{chapterTitle}/{characterName}/{lineId}`

The map was reconciled against the live routes in `py_services/parser_router.py` on 2026-10-07 (several other live routes, e.g. `/api/read-text` and `/api/review-chapter`, are also registered there).

## Character Storage

Chapter review (`POST /api/review-chapter`) learns descriptors only from assigned
speaker lines. Narrator is excluded by its canonical name as well as the legacy
`narrator` ID, including books where Narrator has a v3 GUID. Explicit non-speaker
lines are also excluded. Review still confirms these lines, but does not harvest
their descriptors or modify Narrator's character file. Previously saved character
descriptors are not automatically removed.

- The `characters/` folder (v3.0, one file per character, short-GUID identity) is the source of truth for character data — see `docs/schema_characters_v3.md`.
- `/api/save` handles per-character files under `characters/` (dual-path, containment-checked like `/api/read-text`).

## Lane 2 Policy

- Add new backend routes by extending `API_ENDPOINTS` first.
- Keep request/response parsing in services, but route URLs and cross-cutting error normalization in the API client.
- Do not hardcode backend host or endpoint paths outside the centralized API client module.
