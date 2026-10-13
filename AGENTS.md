# AGENTS.md

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `clock-worked/OpenBook-Svelte` (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout: `GLOSSARY.md` at the repo root plus `docs/adr/` for decisions. See `docs/agents/domain.md`.

## Running checks

### Frontend (`apps/desktop`)

```
npm run test    # vitest
npm run build   # vite build
```

### Python (`py_services`)

```
python -m unittest discover -p 'test_*.py'
```

`discover` picks up every `test_*.py` module automatically — a new test file needs no list update (CI runs the same command). Local-only suites use a `local_` prefix so discovery skips them:

- `local_chapter_review_service` is local-only: needs spaCy 3.5 + `en_core_web_sm`; one test is skip-marked.
- `openbook_parser/local_modernbooknlp_service.py` is local-only: needs the `booknlp` package.
- A fresh worktree has no venv of its own — use the main checkout's environment or system Python.

### Gotchas

- Edit the same file sequentially — parallel `edit` calls to one file can fail ("Could not find oldString") even with correct text.
- (Windows/PowerShell) `2>&1 | Select-Object` makes a GREEN run report "Exited with code 1". Reliable form:
  `python -m unittest ... > out.txt 2>&1; echo EXIT=$LASTEXITCODE`.
- (Windows/PowerShell) `git worktree add` — pass `-q` to keep output clean.

## Repo map

- `apps/desktop/src/lib/services/*.ts` — one backend adapter per REST route (`parser.ts`, `chapterPersistence.ts`, `fs.ts`, `dialogueAI.ts`, `openjev.ts`, `audio.ts`, …).
- `apps/desktop/src/lib/stores/*.svelte.ts` — UI state (`bookCharacters.ts`, `chapter.ts`, `fs.svelte.ts`, `openJev.ts`, …).
- `py_services/*.py` — FastAPI route handlers, one per area (`dialogue_ai_service.py` = OpenJEV route, `local_dialogue_ai_service.py`, `jev_service.py`, `chapter_review_service.py`, …).
- Shared cross-cutting code is being consolidated by `refactor/architecture-deepening` (FE: `services/characterIdentity.ts`, `services/dialogueAiPayload.ts`; py: `dialogue_ai_common.py`, `safe_io.py`, `fs_history.py`).
- Docs: `docs/` (per-area design notes), `docs/adr/` (decisions), `docs/agents/` (agent docs).
- Conventions: `CODING_STANDARDS.md` (import boundaries enforced by `py_services/test_import_boundaries.py`).
