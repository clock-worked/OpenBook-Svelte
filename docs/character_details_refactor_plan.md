# Character Details v3 Refactor Plan (Execution)

**Date:** 2026-10-06
**Goal:** Implement the frozen v3 design in parallel with up to 8 agents on a dedicated branch/worktree, with strict file ownership and a dependency-ordered merge sequence.

## Refactor Principles

- Contract-first: the schema doc (`docs/schema_characters_v3.md`) is normative; any lane that disagrees with it stops and escalates.
- One owner per file, per lane. Cross-lane edits require the owner's ack.
- Small, reviewable increments; no scope creep beyond R1–R11.
- Read-legacy/write-v3 during transition; IN-2 is a runtime guard in code, not a convention.
- Tests are deliverables, not a follow-up (Lane 8).

## Lane Map (8 lanes, one owner each)

| Lane | Name | Owns (exclusive files) | Deliverables | Depends on |
|---|---|---|---|---|
| **L1** | Types | `apps/desktop/src/lib/types.ts` | `Character.guid`; `CharacterFile` v3.0 type; GUID pattern constant; `formatVersion '3.0'` | — (lands first; everything imports it) |
| **L2** | FS/API foundation | `apps/desktop/src/lib/services/fs.ts`; `apiContracts.ts`; `apiClient.ts`; `py_services/parser_router.py` (new routes only) | `listCharacterFiles` / `readCharacterFile` / `writeCharacterFile` / `deleteFile` (dual-path); `POST /api/list-files` + `POST /api/delete-file` (containment-checked like `/api/read-text`) | L1 |
| **L3** | Backend reader fan-out | **new** `py_services/character_store.py`; `dialogue_ai_context.py`; `openbook_parser/knowledge_store.py`; `openbook_parser/jev_verify_service.py`; `update_character_stats.py`; `chapter_review_service.py`; `openbook_parser/booknlp_parser_service.py` (via knowledge store) | Shared folder loader (`load_characters` / `write_character` / `delete_character_file`, atomic); all six readers on it; **write-only-on-change** guard in stats; one-off scripts (`strip_characters_json.py`, `backpropagate_character_curation.py`, `carry_forward_voice_assignments.py`, `migrate_chapter_character_ids.py`) retired/superseded notes | L1 (code); validated on a live book post-L4 |
| **L4** | Migration | **new** `apps/desktop/src/lib/services/characterMigration.ts`; **new** `scripts/python/ai/migrate_characters_v3.py` | 5-stage algorithm (gate → inventory → deterministic mint → validation → ordered apply + post-rescan); FE auto-on-open driver + Python ops script (dry-run default, `--apply`, `_backups/character-v3/<ts>`); shared canonical fixture | L1, L2 |
| **L5** | FE repositories | `apps/desktop/src/lib/services/bookCharacterRepository.ts`; `chapterCharacterRepository.ts` | 3-tier load cascade (folder → v2 auto-migrate → derive); per-file `writeCharacterFile`; `persistBookCharactersData` deleted; roster GUID hydration | L2 |
| **L6** | FE mutations | `apps/desktop/src/lib/services/characterMutationService.ts`; `characterDomain.ts` | Shrunk service: rename = collision-reject → write-new → verify → delete-old; single-file alias/descriptor/gender/color ops; merge = surviving fan-out (GUID remaps + roster remap + source delete); `mintCharacterGuid`; dead slug/prefix machinery removed | L2 |
| **L7** | Stores + UI | `stores/characters.ts`; `stores/bookCharacters.ts`; `components/chapter/ChapterCharacterPanel.svelte`; `ChapterCharacterList.svelte`; `ChapterCharacterBookList.svelte`; **new** `ChapterCharacterDetails.svelte`; `components/common/PillList.svelte` (only if a prop proves necessary) | Store actions (signatures unchanged, single-file internals; new `setBookCharacterDescriptors`); whole-row click + `stopPropagation` + pencil deleted; the details card (title editor, 3-row stats 3-state, gender, alias PillList, descriptor bullets + hover-X + quiet `+` + re-learning note); `computeChapterLineCounts` extraction; old `ChapterCharacterDetails.svelte` deleted | L5, L6 |
| **L8** | Tests + docs | vitest setup (`apps/desktop` config + `__tests__`); `py_services/test_character_folder_migration.py`; `py_services/openbook_parser/test_character_folder_reader.py`; doc updates per below | Full test plan (`docs/character_details_test_plan.md`) green; regression gate green; doc deltas | **all lanes merged — last** |

## L8 doc deltas (pinned)

- `docs/schema_v3.md:7` — **exact replacement sentence:** "This is a **dialogue format evolution**. `voices.json` remains a v2-compatible structure. **Character storage is no longer v2-compatible**: characters live in the per-character `characters/` folder (v3.0) — see `docs/schema_characters_v3.md`."
- `docs/schema_v2.md` status block — add: "Character storage: `characters.json` v2.0 is **retired as a write target** — superseded by the `characters/` folder (v3.0, `docs/schema_characters_v3.md`). Character sections below are reference/migration history only."
- `docs/api_contract.md` — add `/api/list-files`, `/api/delete-file`; "Character storage" note (folder is SoT; `/api/save` handles per-character files); reconcile the stale endpoint map (it already omits several live routes); bump `Updated:`.
- `docs/architecture.md` Data Layout — `characters.json` line → `characters/` (v3.0); note the shared `character_store.py` loader + dual-I/O folder rules.
- `docs/ui_ux.md` Right Characters Panel — the details-card contract (whole-row click, pencil gone, title editor, stats 3-state, alias pills, descriptor bullets + note, collision rejection).
- `docs/character_details_refactor_plan.md` — status lines → 🏁 as lanes land.

## Collision Avoidance Rules

- Strict file ownership per the table; a file appears in exactly one lane.
- Interface changes (the `character_store.py` API, the TS repository API, the `fs.ts` primitives) are **L2/L3 deliverables that land before their consumers start** — L2 is the contract lane for the frontend, `character_store.py`'s API is frozen in L3 before L4's script and L5's loader use it.
- The canonical v3 fixture (test plan) is owned by L8 but its shape is frozen by `docs/schema_characters_v3.md` — no lane invents field names.
- Docs edits: L8 only. Other lanes submit doc deltas as PR comments.

## Merge Order (waves)

1. **Wave A:** L1 (types)
2. **Wave B:** L2 ∥ L3
3. **Wave C:** L4 ∥ L5 ∥ L6
4. **Wave D:** L7
5. **Wave E:** L8 (test gate + docs; merge gate = full test plan + regression gate green)

Max 3 concurrent implementation agents — dependency-safe; the 8-agent ceiling is the total lane count, not the concurrency.

## Tracking Template (copy per lane)

```
## Lane N — <name> Current Status
- [ ] <deliverable 1>
- [ ] <deliverable 2>
- Acceptance: <criteria from test plan / schema doc>
- Status: ⬜ / 🚧 / ✅ / 🏁
```

## Lane 1 Current Status
- ⬜ `types.ts`: `Character.guid`, `CharacterFile`, GUID constant
- Status: ⬜

## Lane 2 Current Status
- ⬜ `fs.ts` folder primitives (list/read/write/delete, dual-path)
- ⬜ `POST /api/list-files` + `POST /api/delete-file`
- Status: ⬜

## Lane 3 Current Status
- ⬜ `character_store.py` shared loader (API frozen first)
- ⬜ six readers on the loader; stats write-only-on-change
- ⬜ one-off scripts retired/superseded
- Status: ⬜

## Lane 4 Current Status
- ⬜ FE `characterMigration.ts` (5-stage, auto-on-open)
- ⬜ `scripts/python/ai/migrate_characters_v3.py` (dry-run/`--apply`/backup/post-rescan)
- Status: ⬜

## Lane 5 Current Status
- ⬜ folder load cascade + per-file write; `persistBookCharactersData` deleted
- ⬜ roster GUID hydration
- Status: ⬜

## Lane 6 Current Status
- ⬜ rename op (collision-reject → write-new → verify → delete-old)
- ⬜ single-file alias/descriptor/gender/color ops; merge fan-out
- ⬜ `mintCharacterGuid`; dead slug machinery removed
- Status: ⬜

## Lane 7 Current Status
- ⬜ store actions (single-file internals; `setBookCharacterDescriptors`)
- ⬜ `ChapterCharacterList` whole-row click, pencil deleted
- ⬜ `ChapterCharacterDetails` card (title/stats/gender/aliases/descriptors)
- ⬜ panel unified selection; old details deleted
- Status: ⬜

## Lane 8 Current Status
- ⬜ vitest setup + service matrix
- ⬜ T-MIG suite + folder reader equivalence
- ⬜ regression gate green
- ⬜ pinned doc deltas (schema_v3.md:7 verbatim, schema_v2 banner, api_contract, architecture, ui_ux)
- Status: ⬜

## Related Docs

- `docs/character_details_alias_design.md` · `docs/schema_characters_v3.md` · `docs/character_details_test_plan.md` · `docs/refactor_plan.md` (conventions; prior cycle, closed)
