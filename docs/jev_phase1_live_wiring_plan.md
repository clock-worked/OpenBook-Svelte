# JEV Phase 1 — Live Wiring Plan

**Origin:** Crystal Society council meeting, 2026-10-02 (OpenBook curation quality).
Full record: `C:\Users\CJDJ\Documents\Default Project\.council\scratch\crystal-society-2026-10-02-openbook-curation\`
(brief, six seat contributions, chair synthesis with dissents preserved).

## Goal

Ship into the **live** app what the offline experiments already validated:

1. The JEV cross-verification layer (exp02 run-query, beats baseline on all four frozen
   books) moves from offline CLI into `/api/parse`.
2. The JEV interactive-assist endpoint (`/api/jev-dialogue-ai`, already built) gets its
   missing UI wiring — a third assist panel beside Gemini and local LLM.
3. The `is_dialogue` upstream gate (noul) is folded into the verify run query.
4. Visibility: the verify summary is surfaced after parse; audio generation notes how many
   unknown lines share the Unknown voice.

**Non-goals (Phase 2/3, parked deliberately):** roster v2 (the +8–12pp lever), curator
verification, Viterbi tie-break, descriptor-collision sentinel, OpenJEV local 9B. Each needs
its own pre-registered eval arm before it starts.

## Phase 0 — steer by the right numbers (process change, no code)

- Steer by the **triple**: `assertRate` (floor: frozen baseline −2pp), `assertPrecision`,
  `guessHit@3`. Bare `accuracy` stops being the headline number — it *rewards* confident
  asserts (all-unknown ⇒ 0) and can be gamed in the opposite direction of the FP goal.
- UX twin metric: **unknowns resolving in ≤1 click** — every downgraded unknown must keep a
  surfaced top-3 guess list (the JEV verdict table already emits this).

---

## Workstream A — verify layer into live `/api/parse` (backend)

Files: `py_services/parser_router.py` (route at L149), `py_services/openbook_parser/jev_verify_service.py`,
tests in `py_services/openbook_parser/test_jev_verify.py`.

### A1. `VerifyPolicy` additions (defaults keep CLI/experiment behavior unchanged)

```python
downgrade_only: bool = False   # live path: never promote a suggestion to an assert
noul_gate: bool = False        # live path: is_dialogue noul<0.5 forces downgrade
budget_seconds: float = 90.0   # wall-clock budget for the whole verify stage
```

### A2. `verify_chapter` changes

- **`downgrade_only`**: after `decide_verdict`, if action == `"confirm"` and the line is a
  suggestion → action becomes `"note"`. (A confirm on an already-asserted line is already a
  no-op in `apply_verdict`, so only the suggestion case needs the gate.)
- **`budget_seconds`**: enforce a wall-clock deadline while collecting the thread-pool
  futures (e.g. `concurrent.futures.wait(futures, timeout=remaining)`). Runs not finished by
  the deadline are left unverified (fail-open, same as a per-run error today); set
  `summary["budgetExhausted"] = True` and `summary["skippedRuns"] = N`.
- **`noul_gate`**: `build_run_query` adds a second question per line:
  `is_dialogue_<pos>` (type `noul`; read `docs/typesafe_jev_api.md` for the exact noul
  answer shape — the verify service only parses `choice` answers today, so a
  `parse_run_noul` helper is needed). Instruction: "Is the text in `<q<pos>>` actually
  spoken dialogue (not thought, narration, or stage direction)?" In the verdict loop: if
  p(yes) < 0.5 → force action `"downgrade"` (treated as not-dialogue → unknown-with-guess).
  Adding questions changes the query → cache keys change automatically → no stale replays.

### A3. `/api/parse` wiring

- Read `request.parser_options["jev_verify"]` (optional dict):
  `{enabled: bool, gate: "carryover"|"full", downgrade_conf, promote_conf, budget_seconds}`.
- **On by default when `VERCEL_JEV_API_KEY` exists** (`.env` is already loaded by
  `JevService`/`_load_local_env_file` at router creation — reuse that path).
- No key → construct no client → `meta.jevVerify = {"enabled": false, "skipped": "no_api_key"}`
  and the `script` payload is **byte-identical** to today.
- Lift `_load_character_lookup` out of `jev_verify_cli.py` into `jev_verify_service.py`
  (or a small shared helper) and call it with `get_book_root()` at parse time.
- Use `request.text` as the `chapter_text` for state windows (the temp file is written with
  `newline=""`, so span coordinates match; `request.text` is the same bytes).
- Run the verify stage with `await asyncio.to_thread(verify_chapter, ...)` — the router's
  established pattern for long work (`/api/local-dialogue-ai` L221) — so the FastAPI event
  loop is not frozen for the HTTP calls.
- Response: `meta.jevVerify` = the full verify summary (targets, runs, modelCalls, cacheHits,
  errors, actions, downgradedLineIds, promotedLineIds, elapsedMs, budgetExhausted, skippedRuns).
  Per-line `attribution.jevVerification` blocks already serialize through
  `script_to_dict_list` — the UI gets per-line detail for free.

### A4. Fail-open invariants (Safety's conditions — acceptance criteria)

1. No API key ⇒ skipped, byte-identical output.
2. Per-run transport failure ⇒ lines keep the heuristic (already true; must stay true).
3. Budget exhaustion ⇒ remaining runs skip, summary flags it, no exception escapes.
4. `meta.jevVerify` is **always** present after a parse that had a key — no silent
   verification death.
5. The live path **never promotes** (`downgrade_only=True` always in the live policy).

**Cache-key analysis (no change needed):** `cache_key` hashes schema+model+state+questions;
the roster lives inside the question criteria, so a `characters.json` rename changes the
roster → changes the key. Cached payloads are raw model answers (not verdicts); verdicts are
recomputed with the current `name_lookup`. Stale-replay risk: none.

### A5. Tests (extend `test_jev_verify.py`; mock the client, no network)

- `downgrade_only`: confirm on a suggestion line stays a suggestion; downgrades still apply.
- Budget: client that sleeps past the budget → `budgetExhausted: true`, unverified lines
  keep heuristic values, no exception.
- `noul_gate`: p(yes) < 0.5 forces a downgrade even when the choice agrees.
- No client: summary carries the error, lines untouched.
- Router: `/api/parse` with a mocked key → `meta.jevVerify` present; without a key →
  `skipped: "no_api_key"` and script identical to a no-JEV parse.

---

## Workstream B — JEV assist panel in the UI (frontend)

The backend already exists and is unchanged in this workstream
(`jev_service.py` + `parser_router.py` L237–251; request/response = `LocalDialogueAiRequest`
/ `LocalDialogueAiResponse`).

Files (each mirrors its local-AI counterpart):

| New file | Mirror |
|---|---|
| `apps/desktop/src/lib/services/apiClient.ts` (extend) | existing gemini/local endpoints |
| `apps/desktop/src/lib/services/jevDialogueAi.ts` | `services/localDialogueAi.ts` |
| `apps/desktop/src/lib/stores/jevDialogueAi.ts` | `stores/localDialogueAi.ts` (minus server-settings — JEV is fixed: 1500-char window, scene roster ≤51 + None) |
| `apps/desktop/src/lib/components/chapter/ChapterJevPanel.svelte` | `ChapterLocalAiPanel.svelte` (minus the settings card) |
| `apps/desktop/src/lib/components/chapter/ChapterCharacterPanel.svelte` (extend) | mount the JEV panel beside the local-AI panel |

**Verify-summary UI:** a small line in the JEV panel (or character panel) reading the parse
`meta.jevVerify`: e.g. `JEV: 42 targets · 18 runs · 2 cache hits · 0 errors (3.2 s)` and a
`skipped: no API key` state. Trace how the parse `meta` currently flows into the chapter
view (store/prop) and add the minimum plumbing.

**Known sharp edge (pre-existing):** `JevService.__init__` raises `ValueError` when
`VERCEL_JEV_API_KEY` is missing, at router-creation time. The panel must show a friendly
error state if the endpoint 400/500s rather than crashing the chapter view.

---

## Workstream C — unknown-voice note at generation (frontend)

File: `apps/desktop/src/lib/components/chapter/tools/audio/audioHandlers.ts`
(the "No voice assigned" alert, ~L137).

- When N dialogue lines have no voice (Unknown speaker), the generation alert/summary adds
  one line: `N unknown lines will use the Unknown voice.`
- Keep the existing protective behavior (no named-voice default) intact.

---

## Status

- [ ] Branch `feat/jev-live-wiring` created
- [ ] A1–A4 + tests green (`pytest` in `py_services`)
- [ ] B: JEV panel + verify-summary UI, typecheck/build green
- [ ] C: audio note, typecheck/build green
- [ ] Committed to branch

## Next (Phase 2 — out of scope for this plan)

1. **Roster v2** — alias memory + text scan in `build_roster`; up to 255 options on the
   TypeSafe API (the 51/52 cap belongs to the local Open-Jev 9B track). The +8–12pp lever.
2. Curator verification / Viterbi tie-break / collision sentinel — each with its own
   pre-registered eval arm.
3. OpenJEV local 9B — after the benchmark harness produces numbers for it.
