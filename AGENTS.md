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
python -m unittest test_parser_router test_jev_service test_import_boundaries test_character_store test_character_folder_migration openbook_parser.test_jev_verify openbook_parser.test_episode_decoder openbook_parser.test_closed_world_parser openbook_parser.test_character_folder_reader
```

- `test_chapter_review_service` is local-only: needs spaCy 3.5 + `en_core_web_sm`; one test is skip-marked and the file is excluded from CI.
- `openbook_parser/test_modernbooknlp_service.py` is local-only: needs the `booknlp` package; excluded from CI.
- A fresh worktree has no venv of its own — use the main checkout's environment or system Python.

### Gotchas (Windows / PowerShell)

- `2>&1 | Select-Object` makes a GREEN run report "Exited with code 1". Reliable form:
  `python -m unittest ... > out.txt 2>&1; echo EXIT=$LASTEXITCODE`.
- `git worktree add` — pass `-q` to keep output clean.

## Repo map

- `apps/desktop/src/lib/services/*.ts` — one backend adapter per REST route (`parser.ts`, `chapterPersistence.ts`, `fs.ts`, `dialogueAI.ts`, `openjev.ts`, `audio.ts`, …).
- `apps/desktop/src/lib/stores/*.svelte.ts` — UI state (`bookCharacters.ts`, `chapter.ts`, `fs.svelte.ts`, `openJev.ts`, …).
- `py_services/*.py` — FastAPI route handlers, one per area (`dialogue_ai_service.py` = OpenJEV route, `local_dialogue_ai_service.py`, `jev_service.py`, `chapter_review_service.py`, …).
- Shared cross-cutting code is being consolidated by `refactor/architecture-deepening` (FE: `services/characterIdentity.ts`, `services/dialogueAiPayload.ts`; py: `dialogue_ai_common.py`, `safe_io.py`, `fs_history.py`).
- Docs: `docs/` (per-area design notes), `docs/adr/` (decisions), `docs/agents/` (agent docs).
- Conventions: `CODING_STANDARDS.md` (import boundaries enforced by `py_services/test_import_boundaries.py`).
