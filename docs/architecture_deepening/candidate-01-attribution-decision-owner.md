# Candidate 1 — Give the attribution decision an owner

**Status:** designed (decision record below, confirmed by owner 2026-11-07) — not started
**Part of:** architecture review 2026-11-06, candidates 1–8
**Companion issue:** see `docs/architecture_deepening/README.md`

## Problem

"How does a line get its attribution and decision trace" has no owning module. The trace is a stringly-typed dict whose *shape* is the real interface: 5 writers, 6 readers, 4 divergent schemas (parser dict / FE dialogue.json / `api_models.Attribution` / `dialogueAiPayload.ts`). Every recent commit in this zone has been a 4–8 file coordinated edit, and the two subtlest defects found in review are wiring defects invisible to any module's local interface (dead `paragraph_sibling_speaker` signal; dead `nextParagraphDialogue` guard at `dialogue_ai_service.py:629`).

## Current state (evidence)

- Writers maintaining the "chosen candidate first" invariant by hand in 4 variants:
  `booknlp_parser_service.py` (`_build_line_attribution` 149–209, `_apply_attribution_override` 212–246, modernbooknlp inline block 606–648), `episode_decoder.py` (`_apply_override` 139–185), `jev_verify_service.py` (`apply_verdict` 559–605), legacy `dialogue_parser_service.py` (`attribution={"legacyRule": ...}` 909).
- Rule/reason name strings cross 4 module boundaries and the process boundary with no registry: `HARD_REASONS` (episode_decoder 10–15), `HARD_REASONS + 2 first-person rules` (booknlp 249–258), `CARRYOVER_RULES` / `LOCAL_EVIDENCE_REASONS` (jev_verify 49–66), 2-string literal in FE `parser.ts` 230–234.
- Assert/suggestion policy implemented twice: Python thresholds in `attribution_passes.rank_candidates` (639–647: top < 0.74, margin < 0.14) and FE `shouldAbstain` (`parser.ts` 236–242: 0.74 / 0.86 / named-candidates / blocked speaker), in parity by hand across a process boundary.
- Dead signal: `paragraph_sibling_speaker` is implemented in `signal_extractor.py` 184–192, 221 and scored in `attribution_passes.py` 510–512, but its only producer (`chapter_review_service.py:331`) writes `resolutionStatus: user_confirmed` to the *persisted file*, never into parser output — the live parse path never populates it.
- Dead guard: `dialogue_ai_service.py:629` reads `target["nextParagraphDialogue"]`, a key nothing ever sets (the context builder sets `nextParagraphLines`, `dialogue_ai_context.py:454`).

## Design

One owning module: **`py_services/openbook_parser/attribution_decision.py`** (Python-first; the TS mirror is the final step of this candidate). It owns three things:

1. **The trace contract.** A single entry point `apply_decision(line, decision)` where `decision` is a typed value carrying: kind (`initial` / `override` / `episode_override` / `verdict`), source stage, chosen candidate, reasons. The stage differences (pop/insert vs sort vs synthetic-candidate prepend vs leave-trace-intact) live in the *data*, not the API. The owner is the only place these invariants are maintained:
   - candidate list order == ranking
   - `decisionTrace.selectedCandidate` == `line.speaker`
   - `selectedReasons` == top candidate's reasons
   - override reasons appended
   - `genderConflict` recomputed
2. **The rule registry.** Every emittable rule (parser sieves, scoring reasons, Gemini override codes) belongs to exactly one of **4 exhaustive categories**: `HARD` (the 5 `HARD_REASONS` — decoder/verify never touch), `CARRYOVER` (`CARRYOVER_RULES` — verify gate), `LOCAL_EVIDENCE` (`LOCAL_EVIDENCE_REASONS` — scoring support), `FIRST_PERSON` (the 2 rules the FE exempts from abstain). A conformance test enforces exhaustiveness. The registry also adopts the Gemini path's reason-name vocabulary now (names registered + categorized); the restructure of that 225-line chain stays in candidate 10.
3. **The assert/suggestion policy spec.** One spec (thresholds 0.74/0.14 + rules), applied at each edge: Python applies it in `rank_candidates` to its candidate set; FE applies it in `shouldAbstain` to the post-collapse set it sees. The FE's un-assert power is preserved and made explicit in the spec. FE-only checks (`no_named_candidates`, `speaker_blocked`) join the spec as named, edge-scoped rules.

**Wire:** additive `ruleCategory` field on the trace (camelCase, frozen-shape-compatible). The FE keeps **no** local rule-name set; legacy files without the field fall back to a small TS set.

## Decisions

1. Owner scope = Python-first; TS mirror is a later step within this candidate.
2. Both dead paths excised (zero behavior change): `paragraph_sibling_speaker` signal and the `nextParagraphDialogue` guard. The two potential *fixes* (wiring user-confirmed siblings into parse; making the guard live) are parked as separate decisions — they are behavior changes.
3. One policy SPEC, applied at each edge — neither side overrides the other.
4. Wire carries `ruleCategory`; no hand-maintained FE mirror of the Python sets.
5. Registry adopts Gemini reason vocabulary; chain restructure fenced to candidate 10.
6. One entry point `apply_decision(line, decision)`; stage behavior in the data.
7. Exactly 4 categories, exhaustive, conformance-tested.
8. Policy pinned by behavioral vectors: `tests/attribution_policy_vectors.json` (candidate set + scores + flags → expected assert/suggestion outcome), run by **both** the Python and TS suites.
9. FE-only checks are named, edge-scoped rules in the shared spec.
10. Migration in dataflow order, one writer per commit: `dialogue_parser_service` → `booknlp_parser_service` (both inline variants) → `episode_decoder` → `jev_verify_service`.
11. Test fixtures (`test_episode_decoder.py`, `test_jev_verify.py`, `test_closed_world_parser.py`) build lines through the owner's constructor instead of hand-built dicts — internal key renames stop breaking fixtures.
12. Reader-side projections (`api_models.Attribution`, `dialogueAiPayload.ts`) join with **parity assertion only** — a conformance test proves each preserves every field the owner writes. No `api_models` restructuring (candidate 11).
13. FE adoption is the final step: `parser.ts` applies the shared spec, reads `ruleCategory` off the wire, keeps the small legacy fallback set. Deliverable includes a TS policy module + trace types. The spec has two live applications from day one.
14. ADR (next number after 0012) records three commitments: (1) the attribution decision has one owning module — writers may not maintain trace invariants by hand; (2) the wire carries `ruleCategory`; (3) assert/suggestion is one cross-language spec applied at each edge.

## Fences (out of scope)

- Gemini `_deterministic_override_result` chain restructure → candidate 10.
- `api_models.py` restructuring / `SaveRequest.content` typing → candidate 11.
- Parser tempfile round-trip / options blackboard → candidate 9.

## Tests

- Conformance: every writer's output satisfies the trace invariants (strengthens per migration commit).
- Registry exhaustiveness: every emittable rule name belongs to exactly one category.
- `tests/attribution_policy_vectors.json` run from py and TS suites.
- Projection parity: `api_models.Attribution` and `dialogueAiPayload.ts` preserve every owner-written field.
- Existing suites green at each migration step (fixtures moved onto the owner's constructor).

## Landing order (one commit each)

1. Owner module: trace contract + `apply_decision` + rule registry (4 categories) + policy spec.
2. Excise dead `paragraph_sibling_speaker` (signal_extractor / booknlp / attribution_passes).
3. Excise dead `nextParagraphDialogue` guard (`dialogue_ai_service.py:629`).
4. Migrate legacy `dialogue_parser_service` writer.
5. Migrate `booknlp_parser_service` (both inline variants).
6. Migrate `episode_decoder`.
7. Migrate `jev_verify_service`.
8. Wire: additive `ruleCategory` on the trace.
9. Vector file + both-suite runner.
10. FE: TS policy module + trace types; `parser.ts` adopts (spec, `ruleCategory`, legacy fallback set).
11. ADR.
