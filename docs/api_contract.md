# OpenBook API Contract (Frontend ↔ Python)

**Status:** Active (Lane 2 foundation)  
**Updated:** 2026-02-19

## Ownership

- Canonical frontend endpoint definitions live in `apps/desktop/src/lib/services/apiClient.ts`.
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

## Lane 2 Policy

- Add new backend routes by extending `API_ENDPOINTS` first.
- Keep request/response parsing in services, but route URLs and cross-cutting error normalization in the API client.
- Do not hardcode backend host or endpoint paths outside the centralized API client module.
