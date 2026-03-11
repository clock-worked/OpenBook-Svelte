# OpenBook Refactor Plan (Execution-Oriented)

**Date:** 2026-02-19  
**Goal:** Refactor in parallel with minimal merge collisions and clear ownership boundaries.  
**Product decisions captured:**
- Preferred runtime path: **File Browser / File System Access API** as primary user flow.
- Schema direction: **focus on current schema** (`dialogue.json` v3 for writes); retain minimal legacy reads only where needed.
- Python parser/audio: **split responsibilities** into clearer service boundaries.
- API governance: establish a lightweight canonical contract.
- VibeVoice dependency: external runtime dependency, treated as integration boundary.

---

## Refactor Principles

1. **Contract-first changes** for cross-team work.
2. **Read compatibility, write current format only** unless explicitly approved.
3. **One owner per lane**, one reviewer from adjacent lane.
4. **Small PRs** (target 300-700 LOC changed) with stable checkpoints.
5. **No feature expansion** during refactor.

---

## Lane Map (Parallel Workstreams)

## Lane 1 — Runtime Boundary & FS Ownership (Completed)

**Purpose**
- Make filesystem/runtime behavior predictable and centralized.
- Keep File System Access API as the default frontend path.

**Owns**
- `apps/desktop/src/lib/services/fs.ts`
- Route bootstrap/runtime wiring in:
  - `apps/desktop/src/routes/+page.svelte`
  - `apps/desktop/src/routes/book/+page.svelte`
  - `apps/desktop/src/routes/chapter/+page.svelte`

**Deliverables**
1. Centralized backend root/audio sync helpers.
2. Shared route bootstrap/session restoration helper (next step).
3. Remove duplicate backend root/stat wiring from routes.

**Acceptance Criteria**
- Routes do not directly duplicate backend root-sync logic.
- Book root/audio root sync behavior is unchanged from user perspective.
- Chapter scanning works in normal and dev mode.

---

## Lane 2 — API Client + Contract

**Purpose**
- Remove ad hoc endpoint calls and standardize request/response handling.

**Owns**
- `apps/desktop/src/lib/services/*` HTTP call sites
- New centralized API client module
- Contract doc in `docs/`

**Deliverables**
1. Single API base URL + endpoint map.
2. Typed request/response wrappers.
3. Error classification (`network`, `validation`, `server`) for UI usage.

**Acceptance Criteria**
- No hardcoded endpoint strings outside API client.
- Parser/audio/settings calls compile against shared types.

---

## Lane 3 — Chapter Feature Decomposition

**Purpose**
- Decompose oversized chapter UI orchestration into maintainable modules.

**Owns**
- `apps/desktop/src/lib/components/chapter/ChapterView.svelte`
- Related chapter tool modules and shared utilities

**Deliverables**
1. Move non-render logic to feature modules (review/edit/audio/join-split).
2. Keep component focused on composition + event wiring.

**Acceptance Criteria**
- `ChapterView.svelte` reduced significantly and easier to review.
- No behavior regression in review/edit/audio workflows.

---

## Lane 4 — Character/Voice Domain Modularization

**Purpose**
- Untangle identity remapping, persistence, and voice assignment concerns.

**Owns**
- `apps/desktop/src/lib/stores/bookCharacters.ts`
- `apps/desktop/src/lib/stores/speakers.ts`
- `apps/desktop/src/lib/services/voices.ts`

**Deliverables**
1. Move heavy transform/remap logic out of stores into domain services.
2. Keep stores as state containers + orchestration only.

**Acceptance Criteria**
- Store files are significantly smaller and easier to reason about.
- Voice/character assignment rules have single-source implementations.

---

## Lane 5 — Python Service Boundary Split

**Purpose**
- Split parser and audio concerns for clearer ops + future scaling.

**Owns**
- `py_services/api_server.py`
- `py_services/audio_generation_service.py`
- parser/audio router modules

**Deliverables**
1. Separate parser router and audio router registration.
2. Shared models module for request/response schemas.
3. Keep external VibeVoice as integration adapter, not app core.

**Acceptance Criteria**
- `api_server.py` becomes composition/root wiring, not business-logic container.
- Parser and audio changes can ship independently.

---

## Lane 6 — Documentation & Governance

**Purpose**
- Eliminate doc drift and define “current truth”.

**Owns**
- `README.md`
- `docs/architecture.md`
- `docs/parser_integration.md`
- schema and compatibility docs

**Deliverables**
1. Current architecture map.
2. Schema policy: read legacy where required, write current format.
3. API contract location and update process.

**Acceptance Criteria**
- New contributor can follow startup/runtime/data flow from docs only.
- Docs align with actual route/service behavior.

---

## Collision Avoidance Rules

1. **Folder ownership is strict** per lane.
2. Cross-lane edits require linked issue + owner ack.
3. Interface changes land first in shared branch notes.
4. Merge order for dependency lanes:
   - Lane 2 contract updates before Lane 3/4 consumers.
   - Lane 1 runtime boundary updates before broad route refactors.

---

## Suggested Sequence

### Week 1
- Lane 2 foundation (API client skeleton)
- Lane 6 baseline docs update
- Lane 1 initial boundary extraction (in progress)

### Week 2
- Lane 1 complete route/session boundary consolidation
- Lane 5 backend router split phase 1

### Week 3-4
- Lane 3 chapter decomposition
- Lane 4 character/voice modularization
- Lane 5 backend split phase 2 + contract alignment

---

## Tracking Template (copy per lane)

- **Owner:**
- **Scope files:**
- **Out of scope:**
- **Open risks:**
- **PR checklist:**
  - [ ] No duplicated logic introduced
  - [ ] Contract/types updated where needed
  - [ ] Docs updated for behavior changes
  - [ ] Smoke-tested main flow

---

## Lane 1 Current Status

✅ Started: centralized backend sync/stats path in `fs.ts` and route adoption begun.  
✅ Completed: shared project session restore/apply helper (`projectSession.ts`).  
✅ Completed: shared route bootstrap initializer (`initRouteProjectContext`) adopted by `book/+page` and `chapter/+page`.  
✅ Completed: extracted reusable directory picker helper (`directoryPicker.ts`) and reused in `book/+page`.  
✅ Completed: extracted reusable handle→backend root sync helper (`syncBookRootFromHandle`) and reused in landing route.  
✅ Completed: extracted stored-project availability helper (`getStoredProjectAvailability`) and reused in landing route.  
✅ Completed: extracted landing project-action module (`landingProject.ts`) and moved handle/path project-loading logic out of `+page`.  
✅ Completed: normalized project-init redirect/message policy via shared route policy service (`routePolicy.ts`).  
✅ Completed: extracted landing interaction/debounce helpers (`landingInteraction.ts`) and removed repeated gating logic from `+page`.  
🏁 Lane 1 complete.

---

## Lane 2 Current Status

✅ Started: added centralized API client module (`apiClient.ts`) with canonical endpoint map + single base URL support.  
✅ Completed: introduced typed request/response helpers and shared error classification (`network` / `validation` / `server`).  
✅ Completed: migrated service HTTP callsites (`fs.ts`, `parser.ts`, `audio.ts`, `voices.ts`, `vibevoice.ts`) to the centralized client.  
✅ Completed: migrated remaining UI hardcoded backend callsites (`ChapterView.svelte`, `ChapterCharacterPanel.svelte`, `audioHandlers.ts`, `settings/+page.svelte`).  
✅ Completed: added API contract/governance doc (`docs/api_contract.md`).  
✅ Completed: tightened typed API payload handling in Lane 2 services (`fs.ts`, `audio.ts`, `vibevoice.ts`, `voices.ts`) to reduce `any` parsing paths.  
✅ Completed: centralized shared API payload contracts in `services/apiContracts.ts` and migrated Lane 2 service consumers to import these contracts.  
✅ Completed: reduced duplicated runtime response guards in `audio.ts` by routing generation flows through shared API request/error handling.  
✅ Completed: centralized JSON POST request boilerplate in `apiClient.ts` (`apiPostJson` / `apiPostVoid`) and migrated Lane 2 service callsites.  
🏁 Lane 2 complete.

---

## Lane 3 Current Status

✅ Started: extracted chapter dialogue normalization/attribution logic from `ChapterView.svelte` into shared module (`chapterNormalization.ts`).  
✅ Completed: wired chapter-load normalization in `ChapterView.svelte` to consume `normalizeScriptData` from shared tools module.  
✅ Completed: preserved existing save/sync attribution behavior by reusing shared `normalizeAttribution` helper.  
✅ Completed: extracted chapter animation-planning and paragraph-run builders into shared presentation module (`chapterPresentation.ts`).  
✅ Completed: rewired `ChapterView.svelte` to use shared presentation helpers (`buildChapterAnimationPlanData`, `buildParagraphRuns`, `createStaticChapterAnimationPlan`).  
✅ Completed: extracted chapter load/save orchestration into shared persistence module (`chapterPersistence.ts`).  
✅ Completed: rewired `ChapterView.svelte` to delegate dialogue load and save flows through shared persistence helpers (`loadChapterContent`, `saveDialogueFromNormalized`).  
✅ Completed: extracted chapter character bootstrap/cache helpers into shared module (`chapterCharacterBootstrap.ts`) and removed slug/alias bootstrap internals from `ChapterView.svelte`.  
✅ Completed: extracted legacy↔normalized script sync + generated audio marker refresh logic into shared state-sync module (`chapterStateSync.ts`).  
✅ Completed: rewired `ChapterView.svelte` to use shared state-sync helpers (`buildLegacyScriptFromNormalized`, `mergeNormalizedFromLegacy`, `buildGeneratedAudioMarkerState`).  
✅ Completed: smoke-checked touched chapter files for compile errors.  
🏁 Lane 3 complete.  

---

## Lane 4 Current Status

✅ Started: extracted character identity/remap/alias transforms into shared domain service (`characterDomain.ts`).  
✅ Completed: `bookCharacters.ts` now acts as store orchestration and delegates remap logic with explicit chapter targets.  
✅ Completed: extracted character load/derive/persist data logic into repository service (`bookCharacterRepository.ts`) and removed those internals from `bookCharacters.ts`.  
✅ Completed: consolidated repeated `setBookCharacter*` mutation paths behind a shared upsert helper in `bookCharacters.ts`.  
✅ Completed: extracted rename/merge/alias mutation orchestration into dedicated service (`characterMutationService.ts`) with thin store wrappers.  
✅ Completed: centralized voice-manifest scan API in `voices.ts` (`scanVoiceManifests`) to remove duplicate endpoint wiring.  
✅ Completed: extracted voice merge/assignment/dedup rules into shared domain service (`voiceDomain.ts`) and wired `speakers.ts` to use it.
🏁 Lane 4 complete.

---

## Lane 5 Current Status

✅ Started: extracted shared FastAPI request models into `py_services/api_models.py`.  
✅ Completed: split parser/chapter routes into dedicated router module (`py_services/parser_router.py`).  
✅ Completed: split audio routes into dedicated router module (`py_services/audio_router.py`).  
✅ Completed: reduced `py_services/api_server.py` to composition/root wiring + path state endpoints and router registration.  
✅ Completed: preserved VibeVoice as integration-boundary router (`audio/vibevoice.py`) wired through shared audio-service callbacks.  
✅ Completed: smoke-validated refactor modules via Python compile/import check (`py_compile`).
✅ Completed: added and ran router split smoke script (`py_services/router_split_smoke_test.py`) to validate route registration + lightweight endpoint behavior.
