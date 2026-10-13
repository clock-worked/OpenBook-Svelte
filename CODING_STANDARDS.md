# Coding standards

Mechanical rules have deterministic checks where they can be made so; the rest
is judgement guidance for agents and humans.

## Python (py_services)

- **Dual-context imports.** A module must import shared code so it works both
  as a package (`python -m unittest`) and from bare-script context:
  `try: from .shared import x` / `except ImportError: from shared import x`.
  Pattern: `openbook_parser/knowledge_store.py`.
- **No cross-service private imports.** A module must not import `_`-prefixed
  names from a sibling `py_services` module. Shared cross-service helpers live
  in a common module under public names (`dialogue_ai_common.py`, from
  `refactor/architecture-deepening`).
  Check: `py_services/test_import_boundaries.py`.
- **Safe file I/O** goes through a shared module (`safe_io.py`, from
  `refactor/architecture-deepening`) rather than being duplicated per service.

## TypeScript (apps/desktop)

- **Dynamic import for store cycles.** Two stores that need each other (e.g.
  `openJev.ts` ↔ `chapter.ts`) import each other dynamically inside a
  function: `const { x } = await import('./chapter.svelte')`.
- **Explicit `.ts` extensions** on relative imports in Node `--test` files.
- **Canonical identity helpers** live in `services/characterIdentity.ts`
  (FE) and `dialogue_ai_common.py` (py) — both from
  `refactor/architecture-deepening`; the two sides must stay in sync with
  `tests/character_identity_vectors.json`.
