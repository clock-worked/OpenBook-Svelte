# Council Brief — Character Details & Alias System Refactor

**Date:** 2026-10-06
**Status:** Pass 1 input (advisors: Crystal Society — `crystal-architect`, `crystal-data`, `crystal-ux`, `crystal-contract`, `crystal-guardian`, `crystal-scribe`)
**Protocol:** 2-pass, per `docs/council_openjev_speaker_decisions.md` — Pass 1: parallel, fresh-context, read-only. Pass 2: cross-exam of Pass 1 positions.

## Feature request (owner's intent)

1. **Single-click** on a row in the **chapter character list** opens the **CharacterDetails** component (today it is only reachable by selecting from the book character list).
2. **CharacterDetails** replaces/expands the current gender + alias-cluster component:
   - **Title** = character name. Clicking it turns it into an input with the name text **selected**, so the user can edit. This may be the name of the *entry* (a surface form), not necessarily the cluster's canonical name — but it could be.
   - **Stats**: one column, three rows — (a) lines assigned to it **this chapter**, (b) lines **in the book**, (c) how many **chapters** it is in. Small grey label above a larger dark bold number (modern UX).
   - **Gender picker** as before.
   - **Alias system refactor**: no more "main alias" concept. Wrapped rows of **pills with X** (the pattern used elsewhere in the app). Should be a **reusable component** for app consistency. Hovering the container reveals a **+** button for adding more aliases.
   - **Descriptors**: the descriptors captured for this character as a **bulleted list**. Hovering an entry changes the bullet into an **X** for removing it.
3. Owner suspects the **backend character data storage** must change, which will change how other parts of the app read that data.
4. Produce **design docs + tests** for all changes. Must **not delete data randomly**.
5. Must be able to **migrate from older data** (v1/v2 formats).
6. Execution: orchestrator + up to **8 parallel implementation agents** on a **branch/worktree**; no stepping on each other's toes.

## Current architecture (verified 2026-10-06)

### Frontend (`apps/desktop`, SvelteKit + Svelte 5)

- **Types** `src/lib/types.ts:10-37`:
  `Character { id, name, gender, aliases: string[], descriptors?, race?, color, notes, firstAppearance, stats: { totalLines, chapterCount }, voice?, provider?, voiceId?, voiceMeta?, manifestStats?, count?, chapterCount? }`;
  `CharactersJson { formatVersion: "2.0", characters }`.
- **Stores**: `stores/characters.ts` (chapter roster, derived), `stores/bookCharacters.ts` (book-level **source of truth**; all mutation actions: rename, merge, setPrimaryName, addAlias, detachAlias, setGender, color, voice…).
- **Panel** `components/chapter/ChapterCharacterPanel.svelte` (1200 lines; tabs: Characters | AI Assist | Local AI | JEV):
  - `components/chapter/ChapterCharacterList.svelte` — chapter roster rows: color badge (click → ColorPicker), inline name rename (pencil), jump-to-line, apply-to-selection, delete, drag-merge. **No row click → details today.**
  - `components/chapter/ChapterCharacterBookList.svelte` — collapsible book character list; selecting one drives the details view.
  - `components/chapter/ChapterCharacterDetails.svelte` (179 lines) — gender pills + alias cluster list with **Star (primary)** and **Delete Alias**; renders only when a *book* character is selected.
- **`components/common/PillList.svelte`** — reusable pill component: hover-revealed X remove, + add button (search dropdown or direct entry), `maxPills`, per-item colors, `readonly`. Used in `book/AudiobookSettings.svelte` (5×) and `book/SpeakerCard.svelte` (2×).
- **Persistence: dual I/O** — File System Access API (`services/fs.ts`) with fallback to `POST /api/save` (`services/apiClient.ts`; dev base `http://127.0.0.1:8012`).
- **Mutation services**: `services/characterMutationService.ts` (rename/merge/primary-name/alias fan-out across `characters.json`, `dialogue.json`, `voices.json`, chapter rosters, legacy script files), `services/characterDomain.ts` (pure remap helpers), `services/characterGender.ts`, `services/bookCharacterRepository.ts`, `services/chapterCharacterRepository.ts`.
- **Descriptors**: stored, learned via chapter review, sent to the parser catalog — but **no UI displays or edits them at all**.

### Backend (`py_services`, FastAPI)

- **Files**: central `Book-N/characters.json` v2.0 (SoT); per-chapter `<Ch>/<Ch>.characters.json` = ID refs; `dialogue.json` v3.2 (lines carry `characterId` + candidates + `stats.characterBreakdown`); `voices.json` assignments; audio manifests `audio_lines/<Name>/manifest.json`; legacy `book.characters.json` knowledge store (name-keyed, `m/f/u` genders).
- **Readers of `characters.json`**: `dialogue_ai_context.py:265-300` (AI-assist context); `openbook_parser/knowledge_store.py:33-69` (falls back read-only to `characters.json`); `openbook_parser/booknlp_parser_service.py:279-306` (closed-world catalog: id/name/aliases/gender/descriptors); `openbook_parser/jev_verify_service.py:93-112` (name lookup); `update_character_stats.py:113-169` (rewrites `stats` on **every dialogue.json save**); `chapter_review_service.py:237-249` (reads; writes learned descriptors at 352-353); `parser_router.py` (`/api/save` generic writer; dialogue saves trigger stats refresh at 143-152).
- **One-off scripts reading/writing `characters.json`**: `scripts/python/ai/strip_characters_json.py`, `backpropagate_character_curation.py`, `migrate_chapter_character_ids.py`, `carry_forward_voice_assignments.py`.
- **Stats are derived** — `totalLines`/`chapterCount`/`firstAppearance` are recomputable from chapter files by two independent implementations (Python `update_character_stats.py`, TS `syncMissingBookCharacterDefaults`). Safe to regenerate in a migration.
- **Migration conventions**: dry-run default → full validation → timestamped `_backups/<purpose>/<ts>` → atomic writes (tempfile + `os.replace`).
- **Tests**: stdlib `unittest`/pytest files co-located (`test_parser_router.py`, `test_chapter_review_service.py`, `openbook_parser/test_closed_world_parser.py`, `test_jev_verify.py`). **No frontend test infra** (no vitest/playwright).
- **Docs house style**: flat `docs/<topic>.md`, kebab-case; title + Date/Status header; `##` sections; backticked `file:line` as unit of ownership; Related Docs section. Council memos: `docs/council_*.md`.
- **Git**: current branch `feat/jev-live-wiring` (9 branches); conventional commits; stale worktree registrations exist.

## What each facet must deliver (Pass 1)

Read the repo (read-only). Return, for your facet:
1. **Recommendations** with rationale (be concrete: file paths, shapes, names).
2. **Top risks** your facet sees (max ~5, ordered).
3. **Open questions for the owner** (things only the human can decide).

Cite `file:line` evidence. No implementation. No file edits.
