# Character Details v3 — Test Plan

**Date:** 2026-10-06
**Status:** Approved with the v3 design. Infrastructure per ruling R6: Vitest, pure-TS service layer only (no jsdom/renderer) — the data-integrity floor.

## Strategy

Two suites, one property: **"no data is deleted or orphaned, ever."**

- **Python** (stdlib `unittest`, co-located `test_*.py`, hermetic `tempfile.TemporaryDirectory` fixtures, file round-trip assertions — house pattern per `py_services/test_parser_router.py:20-46`): migration + backend readers.
- **Vitest** (new infra in `apps/desktop`: 1 devDependency + `vitest.config.ts` with `$lib` alias + `"test": "vitest run"`; node env, no jsdom): pure-TS service layer — `characterDomain.ts` (six silent `catch {}` blocks: `:137,163,230,261,318,388`), `characterMutationService.ts`, `bookCharacterRepository.ts`, folder loader. I/O stubbed by `vi.mock('$lib/services/fs')` with an in-memory file map.
- **Shared fixture canonical sample:** one canonical v3 book fixture, embedded identically in both suites (or a checked-in file both read) — prevents Python/TS fixture drift (field names, `formatVersion`, GUID field).
- **`diff_tree(before, after)` helper in both suites:** every mutation test asserts *changed paths == expected set*. That single assertion is what makes the property testable.

## T-MIG — migration safety suite (`py_services/test_character_folder_migration.py`)

Shared fixture: hermetic `Book-9` with 3 clusters (aliases, gender, color, descriptors, `roleLabels`, voice fields, stats), 2 chapter dirs (dialogue.json with all four reference sites + rosters), `voices.json`, `audio_lines/**/manifest.json`, decoys (root `notes.json`, a binary file). GUID source **injectable** (counter-based for byte-computable expectations; the real mint gets its own property test).

| # | Test | Expected |
|---|---|---|
| M1 | split preserves every v2 field | exactly 3 files in `characters/`, named `<Title>.json`, `formatVersion "3.0"`, `guid` = injected value, **every** v2 field present with unchanged value (iterated, not spot-checked); root `characters.json` absent; no stray files |
| M2 | deterministic output | two fresh trees, same injected GUID sequence → `diff_tree` empty |
| M3 | mint properties (real mint, 1000 draws) | all unique; all match `^[0-9A-HJKM-NP-QRSTV-Z]{8}$` |
| M4 | GUIDs stable across runs | re-run on migrated tree → identical title→GUID map |
| M5 | remap dialogue sites | `characterId`, both candidate arrays, `characterBreakdown` keys all GUIDs; zero slugs |
| M6 | remap rosters | every entry a GUID; membership unchanged |
| M7 | remap voices | every `assignments[].characterId` a GUID; assignment fields untouched |
| M8 | remap manifests | manifest `characterId` fields GUIDs; **directory names unchanged** (R8) |
| M9 | zero stale refs, tree-wide | (a) reference-site scan: zero old slugs; (b) stronger: recursive walk of **all** string values in the tree (excl. `_backups`): zero exact-equal to any old slug |
| M10 | second run is a no-op | 0 operations; `diff_tree` empty; no second backup |
| M11 | backup exists and matches | `_backups/character-v3/<ts>/` contains byte-identical pre-images of **every** changed/deleted file (incl. retired `characters.json`) |
| M12 | unrelated files untouched | `diff_tree(before, after)` == exact allowlist; decoys byte-identical |
| M13 | kill mid-migration is recoverable | fault injection (writer raises / `os._exit` after 2nd of N writes): no torn file (all JSON parses); backup-restore reproduces the pre-migration tree byte-for-byte |
| M14 | dry-run is the default | zero writes (incl. no backup dir); report returned |
| M15 | validation gates + unresolvable refs | duplicate titles (exact + case/whitespace variants) → abort, names the collision, zero writes; unresolvable dialogue ref → **left in place, unremapped**, migration proceeds (roster: left in place unless `--drop-unresolved`, which drops the entry) |
| M16 | BOM + non-ASCII | `utf-8-sig` read; `Café`, `—`, CJK titles round-trip; output UTF-8 no BOM; filenames safe |
| M17 | consumer equivalence after migration | on the same logical book, v2 readers and v3 folder readers **agree**: `load_character_lookup` identical name→canonical map; `build_closed_world_catalog` (folder) == catalog (v2 file); `update_character_stats` on migrated tree == pre-migration stats |

**v1 staged path (same file):** v1 speakers → one cluster each (slug id per `id_candidates()`, `migrate_chapter_character_ids.py:72-80`); `m/f/u` → gender bijective; full pipeline invariants (M1/M9/M12) hold; unresolved `chosenSpeaker` aborts by default; second run byte-identical no-op. **R11 case:** v1-script-only chapters → store migrates, script files stay name-based, dry run flags each.

## Vitest service-layer matrix (`apps/desktop/src/lib/services/__tests__/`)

| Test | Input → expected |
|---|---|
| **rename (core safety assertion)** | `characters/Alice.json` (guid G1) + dialogue/roster/voices with G1 → `rename(G1, "Alicia")`: file renamed; `title === "Alicia"`; **old title `Alice` in `aliases`** (R1); `diff_tree` over the whole book == {del Alice.json, add Alicia.json} — references byte-untouched |
| rename collision | target title == another character's title → rejected, zero writes (R7) |
| addAlias / removeAlias | one file changes; aliases array is the only mutation; case-insensitive dup → false, no write |
| addDescriptor / removeDescriptor | one file; list mutates; nothing else |
| setGender / setColor | one file each; other characters byte-identical |
| create | new `characters/<Title>.json`, valid GUID (regex + unique vs folder); only addition in tree |
| **merge (the one fan-out)** | A(GA) + B(GB) → target absorbs aliases **incl. A's title** + descriptor union; `diff_tree` == exact expected changed-file list; tree-wide scan: zero `GA` tokens remain |
| folder loader: guid resolution | scan builds guid→file map; `load(G1)` returns the right record |
| folder loader: missing file | dialogue refs a GUID with no file → deterministic graceful unresolved (no throw, no phantom) |
| folder loader: duplicate title / duplicate guid | detected and surfaced, not silent first-wins |
| persist round-trip | a record with `roleLabels` on disk survives an unrelated character's persist (IN-2/IN-5) |
| **no name-vs-id string matching** | no consumer code path matches display names against ids (guideline from data facet risk 3) |
| chapter normalization display names (`components/chapter/tools/shared/__tests__/chapterNormalization.test.ts`) | v2 slug `chosenSpeaker`/candidate (`sergeant-jaha`) → canonical title (`Sergeant Jaha`); uppercase v3 GUID `characterId` → title despite lowercase id-map keys; unknown speakers untouched |

## Regression gate (merge blockers)

**Existing, must pass unchanged:** `py_services/test_parser_router.py` · `test_chapter_review_service.py` · `openbook_parser/test_closed_world_parser.py` · `openbook_parser/test_jev_verify.py` · `openbook_parser/test_episode_decoder.py` · `openbook_parser/test_modernbooknlp_service.py` · `scripts/python/dev_smoke/router_split_smoke_test.py`.

**New joiners:** `py_services/test_character_folder_migration.py` (M1–M17 + v1) · `py_services/openbook_parser/test_character_folder_reader.py` (M17's reader half) · the vitest service matrix. Gate = all green in one run.

## Smoke checklist (`dev:all` against a real book)

1. Boot; open a **migrated** book → chapter character list renders from the folder (count matches the backup `characters.json`).
2. **First real-book migration is a one-shot.** Before: out-of-tree copy of the whole book dir; dry-run report read (split counts, per-family remap counts, unresolved list = empty).
3. After: `_backups/character-v3/<ts>` file count == changed-file count from the report.
4. Post-apply scans: tree-wide stale-slug scan = 0; every dialogue `characterId` resolves to a `characters/*.json`; per-char stats identical pre/post (run stats refresh, diff).
5. App walk: rosters render; no null speakers in a known chapter; audio panel finds manifests; one closed-world parse + one JEV run succeed (catalog consumers live).
6. **Core safety on real data:** one UI title rename → on disk exactly one file changed in `characters/`, zero reference files changed.
7. **Chapter-view display names:** character chips/labels render the saved title — slug-era `chosenSpeaker` values (e.g. `sergeant-jaha`) show as `Sergeant Jaha`; GUID-referenced lines show the title, never the raw GUID.

## Related Docs

- `docs/character_details_alias_design.md` · `docs/schema_characters_v3.md` · `docs/character_details_refactor_plan.md` (Lane 8 owns this plan's execution)
