# Character Details & Alias System — v3 Design

**Date:** 2026-10-06
**Status:** Frozen at council round 2 (2026-10-06). Implements the decisions in `docs/council_character_details_decisions.md` (R1–R11). Implementation tracked in `docs/character_details_refactor_plan.md`.

## Scope & non-goals

**In:** the CharacterDetails card (single-click from the chapter list); alias pills + descriptor list UI; v3.0 per-character file storage with GUID identity; the v2→v3 migration (auto on open); frontend/backend reader+writer adaptation.

**Out (per rulings):** cluster-level rename/merge actions *inside* the details card (drag-merge in the list + book route remain the cluster-operation surfaces, R7); per-alias files (R2: cluster granularity only); GUID-renamed audio directories (R8); batch-save endpoints (contract: per-file ops keep the dual I/O paths indistinguishable).

## Decisions (one line each)

R1 title = cluster display name; old title → alias on change · R2 `characters/<Title>.json` per cluster · R3 short-GUID identity, all references by GUID · R4 `characters.json` retired, real migration · R5 descriptor remove + honest note, quiet `+` to add · R6 vitest service-layer floor · R7 whole-row click, pencil deleted, collision → visible rejection, silent auto-upsert, primary-name UI-only retirement · R8 audio dirs name-based, GUID in manifest · R9 auto-migrate on open · R10 hard gate for old app × v3 book · R11 v1-script books migrate store-only. (Verbatim: `docs/council_character_details_owner_rulings.md`.)

## Data model (v3)

`Book-N/characters/<Title>.json`, one file per alias cluster, GUID identity — normative format in `docs/schema_characters_v3.md`.

**Why:** R3's decoupling makes the title a pure display property of one file. The design's central consequence: **rename = file rename + `title` field update + old title appended to `aliases`; zero reference fan-out.** Alias add/remove, descriptor add/remove, gender, color, notes: exactly one file each. Merge is the *only* routine multi-file operation (survivor absorbs aliases incl. source title + descriptor union; GUID remap across dialogue/rosters/voices/manifests; source file deleted).

**In-memory identity:** `Character` gains `guid`; `id` becomes a documented mirror (`id = guid`) so existing read sites keep working. **UI selection is the display name, never the GUID** — the GUID is plumbing (files, references, maps), never displayed, compared, or passed in UI code. One resolver: `resolveCharacter(name) → record` over the folder-loaded store.

## Migration (v2 → v3)

Five stages (full algorithm: `docs/schema_characters_v3.md` + the guardian's suite in the test plan):

0. **Idempotency gate (read-only):** folder with valid unique GUIDs → no-op exit. Folder **and** `characters.json` → **abort** (crashed prior run; never guess). Neither → v1 path (R11) or fresh book.
1. **Inventory:** parse `characters.json`; validate uniqueness/ambiguity (reusing `migrate_chapter_character_ids.py:105-151` logic); walk every reference family (dialogue 4 sites, rosters, voices, manifests); filename-collision check.
2. **Deterministic minting:** `SHA-256(book::v3::v2-id)` → 5 bytes → 8-char Crockford; sorted by `firstAppearance` then title for stability.
3. **Validation gates:** any hit → abort, zero writes (parse failures, duplicates, unresolvable refs, co-existence, mint collision).
4. **Backup:** `_backups/character-v3/<UTC-ts>/` mirroring relative paths of every file that will change (incl. the retired `characters.json`).
5. **Ordered apply:** write all `characters/*.json` (atomic) → remap + atomically write every touched reference file → **last**, delete root `characters.json` → **post-apply rescan asserting zero surviving v2 slugs** across all families.

**Execution paths (R9):** (a) **auto on open** — the FE load cascade detects v2, runs the same staged algorithm through the `fs.ts` primitives (per-file writes; ordering + idempotency make a crash resumable by re-open), then loads the folder. (b) **ops script** `scripts/python/ai/migrate_characters_v3.py` — same algorithm with house atomicity (tempfile + `os.replace`), dry-run default, `--apply`. The two share the algorithm shape; the script is the auditable/backup-first path for real books.

## Frontend

**`fs.ts` new primitives** (each with the standard dual-path pattern): `listCharacterFiles` (FS: `getDirectoryHandle('characters')` + `values()`; backend: `POST /api/list-files {dir_path}`), `readCharacterFile` / `writeCharacterFile` (thin wrappers over `readFileAsJson`/`writeFile`), `deleteFile` (FS: `removeEntry`; backend: `POST /api/delete-file` — **new endpoint**, containment-checked like `/api/read-text`).

**`bookCharacterRepository.ts`:** load = three-tier cascade (folder → v2 auto-migrate → derive-from-chapters). `persistBookCharactersData` **dies**; per-file `writeCharacterFile(record)` replaces it (survivor of the old merge: the one-field normalizer). New surface: `readCharacterFolder`, `writeCharacterFile`, `renameCharacterFile` (write-new → read-back-verify-GUID → delete-old), `deleteCharacterFile`, `createCharacterFile` (mints GUID).

**`characterMutationService.ts` shrinks:** the slug re-derivation, prefix-remaps, and name-based fan-out machinery all die (they existed only because v2 IDs were name slugs). Rename = collision check (reject, R7) → write-new → delete-old; failure contract: write-new fails → abort, nothing lost; delete-old fails → warn, self-heals on reload (duplicate-GUID reconciliation, newest `updatedAt` wins). Merge = the surviving fan-out (GUID→GUID remaps — the existing `remapCharacterIdInDialogueFiles`/`remapCharacterIdInVoicesAssignments` are string-agnostic and reused verbatim — plus a new roster-GUID remap; then delete source file). `setCentralCharacterPrimaryName` kept as a thin delegate to rename (R7).

**Stores:** action signatures unchanged; internals swap to single-file writes. New: `setBookCharacterDescriptors`. `stores/characters.ts`: name/alias/id maps built from folder records; `idToCanonical` maps GUID→display name; roster derivation keys off `line.characterId` (GUID) first, `chosenSpeaker` fallback for legacy v1 chapters. `computeChapterLineCounts` extracted as a pure shared helper (list badges + details stats).

**Components:**
- `ChapterCharacterList.svelte` — **whole-row body click** → `onSelect(name)`; row is already `role="button"` (add Enter key); **pencil + inline rename deleted**; color badge / jump / apply / trash kept, each `|stopPropagation`; `cursor: pointer`.
- `ChapterCharacterDetails.svelte` (replaces `ChapterCharacterDetails.svelte`, which is deleted) — props: `character: Character | null`, `chapterLineCount: number`, and intent callbacks `onSetTitle / onSetGender / onAddAlias / onRemoveAlias / onAddDescriptor / onRemoveDescriptor`. Subscribes to `$bookCharacters` itself; the panel passes identity only.
  - **Title:** click → `<input>` with text selected (auto-`select()`); Enter/blur commits, Escape cancels, empty → revert + stay in edit. Commit = `onSetTitle` → rename op (collision → visible rejection, R7).
  - **Stats:** one column, three rows, small grey label over large bold number (`meta-label` idiom, `SpeakerCard.svelte:148-155`): *Lines this chapter* (`chapterLineCount`), *Lines in book* (`stats.totalLines`), *Chapters* (`stats.chapterCount`). Three-state rendering: bold number / muted `0` / muted `—` (never render missing as 0).
  - **Gender:** unchanged 3-pill picker.
  - **Aliases:** `PillList` (`selected = character.aliases`, `allowDirectEntry`, per-item color) — the title is **not** a pill; the Star/primary concept is gone (R7).
  - **Descriptors:** bulleted list; hover swaps bullet → X (remove, with the one-line "may reappear after the next chapter review" note, R5); quiet hover-revealed `+` with direct-entry input (R5 add).
- `ChapterCharacterPanel.svelte` — unified panel-local `selectedCharacterName` fed by **both** lists; one reactive cleanup rule (stale ⇔ resolved record vanished); details wiring is one-liners over store actions; `selectedAliasItems`/`setPrimaryBookCharacterName` move out/die. Panel **shrinks**.
- `PillList.svelte` — no new props expected (direct entry + hover-X already exist); a `revealAddOnHover` opt-in only if the alias container needs the hover-reveal idiom without touching the 5 `AudiobookSettings`/2 `SpeakerCard` call sites.

## API surface

**New endpoints (3, all small):** `POST /api/delete-file {file_path}` (containment-checked), `POST /api/list-files {dir_path}` (names only), and that's it — **no** migration endpoint (R9: migration is FE-driven auto-on-open + the Python ops script), **no** batch save. `/api/save` stays a generic single-file writer.

**Backend:** one shared module `py_services/character_store.py` — `load_characters(book_root) → (records, source)` (folder if ≥1 valid v3.0 file, else `characters.json`), `write_character` (atomic tempfile + `os.replace`), `delete_character_file`. **All six** `characters.json` readers call it (`dialogue_ai_context.py:265-300`, `knowledge_store.py:33-69` (folder takes the fallback slot), `booknlp_parser_service.py` (via knowledge store), `jev_verify_service.py:93-112`, `update_character_stats.py:103-178`, `chapter_review_service.py:237-249,352-353`) — not six ad-hoc folder readers. `update_character_stats` gains a **write-only-on-change** guard (it runs on every dialogue save and would otherwise fan out to N files).

**Frozen wire contracts:** `characterCatalog`, `aliasToAdd`/`newCharacterProposal`, `descriptorsAdded` — byte-stable, now sourced from the folder (verified transparent to the parser: `characterId` is an inert lookup surface, `booknlp_parser_service.py:82-85`).

**Old-app gate (R10):** loader sees v3 folder + no `characters.json` on an old client → character mutations blocked with "update the app"; chapter/dialogue reading unaffected.

## Invariants (numbered, each has a test — see test plan)

- **IN-1 — Identity is the GUID.** Immutable; title/aliases are display data, never referenced across files.
- **IN-2 — One source of truth.** The folder is the sole character SoT. No writer writes root `characters.json` once the folder exists (runtime guard, not convention).
- **IN-3 — Surgical single-file writes.** Rename/alias/descriptor/gender/color/notes = exactly one file, zero reference fan-out.
- **IN-4 — Merge is the multi-file op.** The only routine fan-out; everything else shrank.
- **IN-5 — GUID-keyed persistence.** Exact-string GUID substitution; the name-matching bug class (`characterDomain.ts:20-24`) is gone from the hot path.
- **IN-6 — Derived data is never authoritative.** `stats`, `firstAppearance`, `usedByCharacters`, `manifestStats` regenerable; GUID→file by scan, no index file.
- **IN-7 — No name is ever lost.** A title change appends the old title to `aliases`; a merge folds the absorbed title into the survivor's `aliases`.
- **IN-8 — Titles unique per book.** Enforced at every write; collision → visible rejection, never silent.

## Related Docs

- `docs/council_character_details_decisions.md` · `docs/council_character_details_owner_rulings.md`
- `docs/schema_characters_v3.md` (normative format) · `docs/character_details_test_plan.md` (proof)
- `docs/character_details_refactor_plan.md` (execution) · `docs/api_contract.md`
