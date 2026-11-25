# One owner for the book format

The question "which files does this book have, and which schema is each one in?" had no owner. ADR 0003 (read legacy, write current) was enforced at each call site, and issue #2 shows what happens when one site forgets: a v3 book re-created its retired chapter roster. One frontend module now owns the book format: `apps/desktop/src/lib/services/bookFormat.ts`.

```text
openBook(root) -> Book
book.version()                          '2.0' | '3.0', memoized per Book
book.read(doc, chapter?)                null when absent, never an error
book.write(doc, chapter?, value, opts?) false when it failed or the format refuses it
book.readChapterDialogue(chapter)       dialogue.json -> script -> unversioned dialogue -> dialogue-old
book.saveDialogue(chapter, value)       the dialogue save write, for the chapter persistence owner (ADR 0014)
doc: dialogue | script | roster | voices | settings | characters | speakerBlocklist
```

Four rules are binding:

1. **Callers name a document, never a path.** The layout table is private. Only a few jobs get a raw path, through `layoutPath`: history capture (and routing history changes back to documents with `classifyBookPath`), the migration's file inventory and legacy-store probes, and `ChapterStatus.scriptPath` (which file a chapter loads from). The fs character-folder primitives and the scanner of the workspace's chapters keep their own file names.
2. **The v2/v3 decision is made once, here.** A book is v3 when its `characters/` folder holds at least one valid character file. If the folder read fails, the book counts as v2, as the old roster guard did.
3. **Retired documents are refused in a v3 book.** A chapter roster and the v2 `characters.json` both return `false` and write nothing. `allowRetiredInV3` lets the migration and an explicit user roster add through (the exemption issue #2 kept), and `backupDir` writes a copy into a backup tree, without history.
4. **The dialogue format-version list lives here** (`DIALOGUE_FORMAT_VERSIONS`, `isVersionedDialogue`). It moved from the chapter persistence module, which removes the fs -> chapterPersistence import cycle.

The rules are pinned by one vector file of v2 and v3 fixture books, `__tests__/fixtures/bookFormatVectors.json`, run by `__tests__/bookFormat.test.ts`. The #2 regression is one row of it.

Character *files* (`characters/<Title>.json`) stay with the fs character-folder primitives and the character mutation service. Addressing characters by id is candidate N4 (#18).

**Considered options**: a guard inside each writer (that was the state that produced #2); a synchronous `openBook` that detects the version up front (every read would pay for a folder scan); exporting the path builders as "advanced" API (they would keep being used, and the rule would stay at the call sites). Accepted costs: every read is now optional, so a missing `voices.json` or `settings.json` no longer logs an error. A `Book` caches its version, so code that migrates a book must open a new `Book` afterwards.
