# OpenBook Data Flow Resources

The codebase is the primary source. No external resources needed for the save-flow topic.

## Knowledge

- [Code: `apps/desktop/src/lib/services/fs.ts`](apps/desktop/src/lib/services/fs.ts)
  The transport seam: `readText` (REST → `/api/read-text`, FSA → direct disk), `writeFile`/`writeDialogue` (history-wrapped), `readCentralCharacters` (per-book read cache, ADR 0012). Use for: what any file read/write costs in API calls.
- [Code: `apps/desktop/src/lib/services/history.ts`](apps/desktop/src/lib/services/history.ts)
  The undo/redo engine: `historyAction` = batch, `capture`/`write` snapshot file contents before changes. Use for: why reads happen at all.
- [Code: `apps/desktop/src/lib/components/chapter/ChapterView.svelte`](apps/desktop/src/lib/components/chapter/ChapterView.svelte)
  The chapter editor's actions: `saveCurrentChapter` (line ~620), `markChapterReviewed` (line ~676, the N+2 capture burst). Use for: mapping UI gestures to API calls.
- [Code: `apps/desktop/src/lib/services/chapterPersistence.ts`](apps/desktop/src/lib/services/chapterPersistence.ts)
  `saveDialogueFromNormalized` — the normalized-save pipeline. Use for: the plain-save call sequence.
- [Code: `py_services/parser_router.py`](py_services/parser_router.py)
  The REST routes: `/api/read-text`, `/api/save`, `/api/save-dialogue`, `/api/apply-dialogue-stats`. Use for: what each endpoint does server-side.
- [ADR: `docs/adr/0012-incremental-character-stats.md`](docs/adr/0012-incremental-character-stats.md)
  The decision that split "save" from "stats update". Use for: why saves are now one round trip.
- [Doc: `AGENTS.md` repo map](AGENTS.md)
  File→role index for the whole app. Use for: finding any module fast.

## Wisdom (Communities)

- None needed — the user is the domain authority for this codebase.
