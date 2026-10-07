# Owner Rulings — Character Details & Alias System Refactor (Round 2)

**Date:** 2026-10-06
**Status:** Binding input for council round 2. Supersedes the council's Pass 1/2 "no storage change" position on data architecture.

The owner (Chad) answered the council's decision list. Verbatim rulings where material:

## R1 — Title semantics
> "Surface name that applies to the character. Defaults to alias but is a separate entity."

The title is the **character's display name** (cluster-level), editable in the details card. When a title is changed, the **old title becomes an alias by default** (name history preserved — no name is ever lost).

## R2 — Storage: per-character files in a `characters/` folder
> "Each character stored as a Json with that title name. characters folder"

- New layout: `Book-N/characters/<Title>.json` — **one file per alias cluster**, named by the character's title.
- Granularity: **cluster only** (owner's explicit choice). Aliases live inside the cluster's file. No per-alias files.
- Each file carries a **reference/identity field** (R3).

## R3 — Identity: short GUIDs, decoupled from all names
> "Use short guids so that characters are separated from titles, names, and aliases"

- Character identity = a **short GUID** (format to be specified by the council: short, unambiguous alphabet, collision-safe, human-tolerable in logs).
- **Everything references characters by GUID**: `dialogue.json` lines + candidates, chapter rosters `<Ch>.characters.json`, `voices.json` assignments, audio manifests.
- Consequence: **title rename = file rename + title field update. Zero reference fan-out.** This is the core safety property of v3.

## R4 — `characters.json` is retired; real migration required
> "It is not backward compatible if you add json files for each character in a characters folder. Everything should reference those files."

- Migration **splits** `characters.json` (v2.0) into `characters/*.json` (v3.0), mints the short GUIDs, and **remaps every GUID reference** across dialogue files, chapter rosters, voices, and audio manifests.
- House migration conventions apply: dry-run default, full validation, timestamped `_backups/<purpose>/<ts>`, atomic writes, idempotent re-runs.
- The folder is the **single source of truth** after migration. `characters.json` is retired (owner rejected keeping it as a live second source).
- Legacy v1 / `book.characters.json` books must also migrate (via the same or a staged path).

## R5 — Descriptors
- Removal: **allowed, with an honest UI note** that a removed descriptor may reappear after the next chapter review (re-learning is accepted; no suppressed-list field).
- Adding: **quiet hover-revealed +** with direct-entry input (owner approved beyond original scope).

## R6 — Frontend test infrastructure
- **Vitest, pure-TS service layer only** (no jsdom/renderer). This is the data-integrity floor.

## R7 — UI mechanics
- Row click target: **whole row body** (all inner controls `stopPropagation`).
- Pencil inline-rename: **deleted** (the details title is the one name editor).
- Cluster-level rename/merge actions inside the details card: **out of scope** (drag-merge in the list + book route remain the cluster-operation surfaces).
- Rows without a book record: **silent auto-upsert** on first cluster-level edit (existing behavior).
- Primary-name ("main alias") concept: **UI-only retirement this pass** (Star gone; service kept).
- Title/entry-name collision with another character's title: **reject with a visible message** (no implicit merge).

## Derived design directives (council to spec precisely)
1. **v3.0 format version** on the new per-character files; reader-tolerance matrix for v2/v3.
2. **GUID spec**: length, alphabet (no confusables like 0/O, 1/I/l), generation + persistence (where a new character's GUID is minted), stability rules.
3. **Folder write atomicity**: the frontend now writes *multiple files* (a character file + possibly the roster). Define the consistency rules for the dual I/O paths (FS Access per-file writes vs `/api/save`).
4. **Mutation surface under v3**: which file(s) each operation touches — rename (1 file rename), alias add/remove (1 file), descriptor add/remove (1 file), gender (1 file), color (1 file), create (1 new file). The fan-out services (`characterMutationService.ts`) should shrink dramatically; merge is the main remaining multi-file operation.
5. **Migration fan-out is now the riskiest operation in the project** (GUID remap across every chapter's dialogue.json + rosters + voices + manifests). It gets the full safety suite.
6. **Parser/AI wire contracts stay frozen**: the closed-world `characterCatalog`, `aliasToAdd`, `descriptorsAdded` shapes do not change — the catalog is now *built from the folder* instead of `characters.json`.
