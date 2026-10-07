# Council Memo — Character Details & Alias System Refactor

**Date:** 2026-10-06
**Status:** Final — three rounds complete. The pass 1/2 consensus on data architecture was **superseded by binding owner rulings** (see "Owner rulings" below).
**Question:** Expand the chapter character panel: single-click a chapter character row to open a rich CharacterDetails card (editable title, 3-row stats, gender, alias pills without a "main alias", descriptor list with hover-remove) — and decide the character data storage question the owner then resolved with a new v3.0 architecture.
**Scope:** `apps/desktop` (SvelteKit frontend), `py_services` (FastAPI backend), `scripts/python` (migration), `docs/`. The full character data path: storage, mutation services, stores, panel UI, parser/AI pipeline consumers.
**Advisors:** `crystal-architect`, `crystal-data`, `crystal-ux`, `crystal-contract`, `crystal-guardian`, `crystal-scribe` (fresh-context, read-only, three rounds). Frozen inputs: `docs/council_character_details_brief.md` (request + verified architecture) and `docs/council_character_details_owner_rulings.md` (owner's words, R1–R7).

## Recommendation (pass 1/2 consensus — superseded on storage)

The council converged on a **UI-only change**: `characters.json` v2.0 retained as source of truth, no storage-format change, no version bump, no migration. The only data delta proposed was an optional display `name` on per-chapter roster entries.

| Dispute | Outcome | Rationale |
|---|---|---|
| Title/entry-name semantics | **Converged: per-chapter surface name** (data facet) | The owner's brief phrase "the name of the entry, not necessarily an alias cluster name" is only coherent under surface-name semantics. Contract, ux, and architect all **withdrew** their fan-out framings in pass 2. |
| Storage schema & version | **Converged: no change, no bump** (data + contract) | Nothing in the repo reads `characters.json`'s `formatVersion` (`bookCharacterRepository.ts:142,227` only default it); all consumers are field-tolerant. A bump would be cosmetic. |
| Central persist merge | **Converged: rekey by id** (data IN-3 + guardian red-first test) | `persistBookCharactersData` (`bookCharacterRepository.ts:319-353`) is name-keyed; a title change drops the renamed record and re-adds it without file-only fields (e.g. `roleLabels`). A live data-loss path. |
| Descriptor removal | **Converged: remove + honest note** (data, ux, guardian, architect) — contract dissented | `chapter_review_service.py:321-334` re-appends learned descriptors on every review; a suppressed-list was the only durable option but forced a central v3 field. 4–1. |
| Frontend test infra | **Converged: vitest, pure-TS service floor** (guardian) | `characterDomain.ts` has **six** silent `catch {}` blocks (`:137,163,230,261,318,388`) around the rename/merge fan-out with zero coverage. |
| Row interactions | **Converged: pencil deleted; collision → visible rejection** (ux conceded, guardian concurred) | One name editor per job (the details title); implicit merge from a one-click editor is the "randomly deleted" failure mode. |

## Owner rulings (rounds 1–2)

The owner overrode the storage consensus. One row per ruling; the left column is the position the ruling supersedes.

| # | Council position (pass 1/2) | Owner's choice | Owner's words (verbatim) |
|---|---|---|---|
| R1 | Surface name on roster entry (display-only) | **Title = the cluster's display name; old title becomes an alias on change** | "Surface name that applies to the character. Defaults to alias but is a separate entity." |
| R2 | No storage change | **`Book-N/characters/<Title>.json` — one file per alias cluster, named by title** | "Each character stored as a Json with that title name. characters folder" |
| R3 | Stable slugs in `characters.json` suffice | **Short GUIDs; everything references by GUID** | "Use short guids so that characters are separated from titles, names, and aliases" |
| R4 | No migration needed | **Split + full GUID remap; `characters.json` retired** | "It is not backward compatible if you add json files for each character in a characters folder. Everything should reference those files." |
| R5 | (pass 1/2: display+remove) | Remove + honest re-learning note; **plus** quiet `+` to add | — (council_character_details_owner_rulings.md:35-40) |
| R6 | — | **Vitest, pure-TS service layer only** | — (rulings:41-43) |
| R7 | — | Whole-row click; pencil deleted; cluster actions out of card scope; silent auto-upsert; primary-name UI-only retirement; collision → reject | — (rulings:44-48) |
| R8 | Guardian: GUID-rename audio dirs (1); data/architect/scribe: keep name-based (3) | **Audio dirs stay name-based; GUID in `manifest.json` only** | "Keep name-based (Recommended)" |
| R9 | Contract: in-app button + dry-run; architect: auto; data: ops script | **Auto-migrate on open** (backup-first, idempotent, resume-safe) | "Auto-migrate on open" |
| R10 | — | **Hard "update required" gate** when an old app version meets a v3 book | "Hard 'update required' gate (Recommended)" |
| R11 | — | **v1-script books: migrate store only**; script files stay name-based until re-parse | "Migrate store only (Recommended)" |

**Supersession:** R1–R4 and R8–R11 supersede the pass 1/2 "no storage change" consensus in full. The v3.0 design, format, and migration are specified in `docs/character_details_alias_design.md` and `docs/schema_characters_v3.md`.

**Council-side rulings (no owner input needed):** 8-char uppercase Crockford-base32 GUIDs (pattern `^[0-9A-HJKM-NP-QRSTV-Z]{8}$`); deterministic migration minting (`SHA-256(book::v3::v2id)` → 5 bytes); runtime mints = 5 random bytes with uniqueness check; no `_index.json` (GUID→file resolution is a folder scan); rename = write-new → verify → delete-old with duplicate-GUID self-heal (newest `updatedAt` wins); concurrent-tab GUID collisions are acceptable (2⁴⁰ space, self-heals); `narrator` stays a reserved literal with no file; `Character.id` becomes a documented mirror of `guid`.

## Consequences (bigger scope)

The override bought the core safety property — **title rename = one file rename, zero reference fan-out** — at the cost of making the migration the largest single write operation in the project (every `dialogue.json`, roster, `voices.json`, and manifest in a book). It is handled as such: five-stage algorithm (idempotency gate → inventory → deterministic mint → validation gates → ordered apply with backup + post-apply rescan asserting zero surviving v2 slugs), executed automatically on first open (R9) with a Python ops script as the atomic alternative. Execution is an 8-lane plan in `docs/character_details_refactor_plan.md` on a dedicated worktree/branch.

## Residual risks

- **Partial-migration state** (folder + `characters.json` co-existing): the loader **aborts, never guesses**; recovery = restore from `_backups/character-v3/<ts>/`. Bounded by apply ordering (folder first, root file retired last).
- **Dual-writer split-brain across the transition**: IN-2 is a runtime guard — any writer that writes root `characters.json` when the folder exists is a bug, enforced in code, not convention.
- **Audio stranding on rename**: accepted by R8. A title rename does not move `audio_lines/<Name>/`; the manifest's `characterId` (GUID) is the reference of record.
- **Name-shaped display leaks**: every display path must resolve GUID→title through the folder map; a test asserts no consumer string-matches names against ids.
- **Stats double-writer** (FE `syncMissingBookCharacterDefaults` + BE `update_character_stats`): accepted last-writer-wins; stats are regenerable; writes scoped to changed files only.

## Evidence and run IDs

- Round 1 (pass 1, all fresh-context): children `ses_eec5a8a15ffe69QGdraD9K5ss8` (architect), `ses_eec5a8a15ffdCtE5mTuvM0zEfG` (data), `ses_eec5a8a19ffeYqyglOZhZ6WHZo` (ux), `ses_eec5a8a18ffe5qiifVKG1bd7bT` (contract), `ses_eec5a8a17ffe9Ds0Zl0vpxY3KD` (guardian), `ses_eec5a8a17ffdYnNQQBruPwYSQb` (scribe).
- Round 2 (pass 2 cross-exam, fresh-context per house protocol): `ses_eec3b226cffeu2A9KgaPBX6qHL` (architect), `ses_eec3b226bffeDeYWpbnWemOGQQ` (data), `ses_eec3b2270ffe0zhtyBsJFKCX8e` (ux), `ses_eec3b226fffeacKPrb4M7F42Lh` (contract), `ses_eec3b226effe4JWNQW1618chG4` (guardian), `ses_eec3b226effdRjF9lr3BTrdx6G` (scribe).
- Round 3 (owner-ruling redesign, data-side facets): `ses_eec1c6786ffebJ6J27AbNt06oA` (data), `ses_eec1c6785ffeVDU89c1GOH1LAz` (contract), `ses_eec1c677bffet75KBszscYtWjA` (guardian), `ses_eec1c677affe5NGVRmi1sa7gsD` (architect), `ses_eec1c6779ffeRfZrN1Y5rf1uEb` (scribe).
- Repo evidence (verified by advisors, re-verified by parent): `apps/desktop/src/lib/types.ts:10-37`, `bookCharacterRepository.ts:140-168,312-361`, `characterMutationService.ts:18-277`, `characterDomain.ts:137,163,230,261,318,388`, `chapterCharacterRepository.ts:9-121`, `fs.ts:452-454,552-614`, `py_services/parser_router.py:115-162`, `update_character_stats.py:103-178`, `chapter_review_service.py:237-249,321-353`, `dialogue_ai_context.py:265-300,288`, `openbook_parser/knowledge_store.py:33-69`, `openbook_parser/booknlp_parser_service.py:70-103,279-306`, `openbook_parser/jev_verify_service.py:93-112`, `scripts/python/ai/migrate_chapter_character_ids.py:24-47,105-178,215-292`, `docs/schema_v2.md:71-121`, `docs/schema_v3.md:7`.

## Related Docs

- `docs/council_character_details_brief.md` (frozen input)
- `docs/council_character_details_owner_rulings.md` (frozen input)
- `docs/character_details_alias_design.md` (the design)
- `docs/schema_characters_v3.md` (the format, normative)
- `docs/character_details_test_plan.md` (the proof)
- `docs/character_details_refactor_plan.md` (execution: lanes, ownership, status)
- `docs/council_openjev_speaker_decisions.md` (protocol precedent)
