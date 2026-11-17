# Candidate 2 — Consolidate the chapter persistence module

**Status:** designed (decision record below, confirmed by owner 2026-11-07) — not started
**Part of:** architecture review 2026-11-06, candidates 1–8
**Companion issue:** see `docs/architecture_deepening/README.md`

## Problem

"Save a chapter's dialogue" is not owned by any single module: it is a relay across six files (ChapterView → `chapterPersistence` → bootstrap → `fs` transport → `parser_router` → stats accumulator), and each hop adds a hidden policy: the transport sniffs the path to pick the endpoint, the write fires stats only in one session mode, the component nulls caches around the save, and the bootstrap is a 9-parameter no-op in current-format books. "Load a chapter" takes a 9-callback bag that is byte-identical at both production call sites. The dialogue format-version list exists in 3 copies. No test covers the full save path at any layer.

## Current state (evidence)

- `saveDialogueFromNormalized` (`chapterPersistence.ts:157`) takes a 3-callback `ChapterSaveRepo` bag (151–155); `loadChapterContent` (52–75) takes 9 callbacks — the audit found the load bag **byte-identical** at both production call sites (`ChapterView.svelte:781` and `:840`; all 5 callbacks are plain module-level imports).
- `ensureCentralCharactersForNames` (`chapterCharacterBootstrap.ts:56`): 9 params, 2 dead (`apiSave`, `getBackendRootAbsolutePath` — never referenced in the body; the `apiSave` closure at `ChapterView.svelte:124–129` is constructed every save and never invoked). Its v3 guard (`listCharacterFiles(root).length > 0 → return`, line 70) makes the whole call a no-op in current-format books; `bookMigration.ts:454` already stubs it out.
- Endpoint selection leaks into the transport: `httpTransport` special-cases `path.endsWith('/dialogue.json')` → `/api/save-dialogue` (`fs.ts:715`); `writeScript` duplicates a second, *disagreeing* heuristic (`fs.ts:1133`, also matches `.script.json`, no `rootDirHandle` gate) — in sidecar-only mode a `dialogue.json` save through `writeScript` gets the stats delta applied **twice** (latent).
- Stats fan-out coupled to module state: `writeDialogue` fires `applyDialogueStatsDelta` only when the module-level `rootDirHandle` is set (`fs.ts:903–912`) — identical calls have different side effects by session mode. Incidental fires hit backup-preimage writes (`bookMigration.ts:335`), audio-metadata stamps (`chapterPersistence.ts:354`), remap/migration writes (`characterDomain.ts:165`, `characterMigration.ts:774`).
- Cache invalidation distributed: component-local `characterNameToIdMap` nulled at 5 spots in `ChapterView.svelte` (98–115, 133–135, 796, 863); `centralCharactersCache` inside `fs.writeCentralCharacters` (949–954); book-characters store via bootstrap callback; mark-reviewed flow adds `forceRefreshBookCharacters` (738).
- Format-version list `['2.0','3.0','3.1','3.2']` in 3 copies: `chapterPersistence.ts:86–93`, `fs.ts:1165–1170`, `bookMigration.ts:97`.
- BE `/api/save-dialogue` (`parser_router.py:147–176`): write, then stats delta in a nested try whose failure is a bare `print` (165–166) — "Stats failures never fail the save" is the intended contract but is invisible and untested (no route-level test exists).

## Design

**`chapterPersistence.ts` becomes the one owner** of chapter save **and** load. The interface shrinks to what genuinely varies:

- **save:** `(chapter, normalized lines, rawText, unknownThreshold, speaker label, reviewed/reviewedAt)`
- **load:** `(chapter, root, unknownThreshold)`

The 3-callback repo bag and the 9-callback load bag are **deleted**; the module imports normalization (`chapterNormalization`), transport (`fs`), and identity resolution directly. The transport adapter becomes dumb: the owner names the write kind ("dialogue save"), and no path-sniffing survives in `httpTransport` or `writeScript`. Stats-after-save becomes an **explicit step of the owner** (FSA mode: POST `/api/apply-dialogue-stats` after the write; HTTP mode: nothing — the endpoint already applied the delta), and `writeDialogue` becomes a pure write. Cache invalidation moves into the owner: after a successful save it invalidates the identity caches and refreshes the book-characters store in one place; the component-local name→id map is absorbed into the shared cached resolver `fs.ensureCharacterNameToIdMapCached`. The format-version list is owned by `chapterPersistence.ts`; `fs.ts` and `bookMigration.ts` import it from there.

## Decisions

1. One owner for save + load; callback bags deleted; interface = what varies. Tests stub at the `fs` seam (existing `inMemoryFs` + `diffTree` pattern).
2. `ensureCentralCharactersForNames` **deleted outright**. Accepted cost: legacy-format books lose root `characters.json` roster backfill at save (v3 books are unaffected — the guard already no-oped it).
3. BE keeps the "Stats failures never fail the save" contract — pinned by a new route-level test; the bare `print` becomes real logging.
4. Fences: the 4 parallel FE stats derivations → candidate 5; `api_models` typing → candidate 11. The incidental stats-delta fires (backups, audio stamps, remap/migration) are **dropped** — only genuine dialogue saves touch stats.
5. Endpoint selection leaves the transport; `writeScript`'s disagreeing heuristic is removed (its five `ChapterCharacterPanel` call sites all write `script.json` → plain `/api/save`, zero behavior change; the latent double-stats bug vanishes).
6. Stats become an explicit owner step; `writeDialogue` pure; `rootDirHandle` gate deleted.
7. Cache invalidation in the owner; component-local map absorbed into `fs.ensureCharacterNameToIdMapCached`; mark-reviewed's extra refresh folds into the same step.
8. Load narrows to `(chapter, root, unknownThreshold)`; version list single-sourced in the owner.
9. Tests: FE owner tests at the fs seam (save writes `dialogue.json` + history capture; load version fallback; single-source version list); NEW BE route-level test for `/api/save-dialogue` (success → write + delta; stats failure → save still succeeds, pinning the swallow; `/api/save` has no stats step). No dedicated tests for the dropped incidental fires; no cross-language parity fixture (candidate 11's).
10. Short ADR (after candidate 1's) recording three seam-placement commitments: transport never infers endpoint semantics from path; stats-after-save is an explicit owner step, never a low-level write side effect; `chapterPersistence` owns the dialogue format-version list.
11. Landing order as below, one step per commit.

## Fences (out of scope)

- The 4 parallel FE stats derivations and the BE join cascade → candidate 5.
- `api_models.py` restructuring / `SaveRequest.content` typing → candidate 11.

## Tests

- FE: owner-level save/load tests at the `fs` seam; existing `chapterPersistence.test.ts` / `chapterAssignment.test.ts` rewritten to stub at the seam instead of the callback bags.
- BE: new route-level test for `/api/save-dialogue` (the swallow pinned) and `/api/save` (no stats step).
- Existing py suites green at each step.

## Landing order (one commit each)

1. Delete the bootstrap (`chapterCharacterBootstrap.ts` + ChapterView closures + bookMigration stub).
2. Pin the BE contract: route test + `print` → logging.
3. Move endpoint selection: owner names the write kind; delete the `httpTransport` sniff and the `writeScript` heuristic.
4. Make stats explicit: `writeDialogue` pure; owner performs the stats step; incidental fires stop.
5. Cache invalidation into the owner (absorb name→id map; delete ChapterView's five nulling spots; fold mark-reviewed refresh).
6. Narrow the load interface; version list into the owner.
7. ADR.
