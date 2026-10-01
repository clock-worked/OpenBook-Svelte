# Exp02 — Run query (batched JEV verification of contiguous dialogue runs, Q2)

**Branch:** `exp/jev-verify-run-query` · **Worktree:** `worktrees/exp2-run-query`

## Hypothesis

Turn-taking is a **run** property, not a line property. A single untagged
quote is ambiguous in isolation (exp01 hit the 0.49 boundary on exactly
such a line), but "does this untagged run continue the same speech as the
tagged run around it?" is a well-posed question the model can answer at
high confidence. Batching the question per run (one call, one Choice per
line) should push disagreements past the 0.60 downgrade bar — removing
carryover FPs — while costing ≤ the per-line call count.

## Design (pre-registered)

- **Gate (carryover):** identical to exp01.
- **Run grouping:** a run = maximal contiguous sequence of dialogue lines.
  A run is a job iff it contains ≥1 gated target. Tagged lines inside a
  run are **anchors**: visible to the model, no verdict applied.
- **Query (Q2, per run):** one JEV call. State = window around the run
  with each line's quote marked `<q0>…<qN>` (named markers — JEV 1.13 has
  no reliable counting). One Choice question per line, shared roster
  (union of target lines' rosters) + `None`. Instruction states the
  continuation rule literally: *"An untagged quote that continues the
  same speech as a nearby tagged quote is spoken by the same speaker."*
- **Single-line runs** fall back to the same builder shape (one
  `<q0>`-marked question) — clean exp01-vs-exp02 isolation.
- **Verdict table:** identical to exp01 (FP-first, no silent flips).
  Verdicts applied **only to gated lines** in the run.
- **Accounting:** one model call per run; `max_calls` caps runs, not
  lines. `attribution.jevVerification.query = "run"`.

## Success / stop criteria

- **Success:** on synthetic, line 3 downgraded (FP gone, Dorian first
  guess). On real books 14/15/17/18: assert precision up with no
  identity-accuracy loss vs frozen baselines (59.87 / 71.47 / 73.47 /
  64.93%), and ≥ exp01 on the same targets.
- **Stop:** if run batching lowers agreement confidence vs per-line
  (context rot from large runs) and downgrades net negative.

## Results

### Synthetic book (same frozen baseline as exp01)

| metric | baseline | exp01 (Q1) | **exp02 (Q2)** |
|---|---|---|---|
| accuracy | 0.6667 | 0.6667 | **1.0000** |
| assert rate | 0.6667 | 0.6667 | 0.3333 |
| assert precision | 0.5000 | 0.5000 | **1.0000** |
| guess acc | 0.3333 | 0.3333 | 0.6667 |

Line 3: JEV Dorian at conf **0.62** (vs 0.49 per-line) → **downgrade**.
The FP assert is gone, replaced by unknown-with-Dorian-guess (correct).
One model call for the whole run (targets=1, runs=1, calls=1).
31 unit tests pass (up from 23).

**Also fixed in this worktree:** `eval_fp_suite.py` metric bug —
`asserted` counted only *mismatched* asserts, so assert rate/precision
were FP-rate and an ill-defined ratio. Now counts all asserts;
decomposition `accuracy = assertRate × assertPrecision + guessAcc`
balances exactly. All prior synthetic numbers re-scored under the
corrected definitions.

### Real books 14/15/17/18 (9,464 gold dialogue lines)

All runs on staged copies (`scripts/tmp/ledger_books/`), fixed FP suite,
same gold for every arm.

**Accuracy:**

| book | baseline | exp01 (Q1) | **exp02 (Q2)** | exp03 (blunt) |
|---|---|---|---|---|
| 14 | 0.6290 | 0.6884 | **0.6938** | 0.6300 |
| 15 | 0.7081 | 0.7022 | **0.7206** | 0.7085 |
| 17 | 0.7326 | 0.7495 | **0.7499** | 0.7334 |
| 18 | 0.5972 | 0.6101 | **0.6101** | 0.5972 |

**Assert precision (primary FP metric):**

| book | baseline | exp01 | **exp02** | exp03 |
|---|---|---|---|---|
| 14 | 0.6916 | 0.7525 | **0.7555** | 0.7000 |
| 15 | 0.7315 | 0.7337 | **0.7482** | 0.7144 |
| 17 | 0.7561 | 0.7807 | **0.7817** | 0.7432 |
| 18 | 0.6130 | 0.6331 | **0.6332** | 0.6000 |

**FP removal vs collateral (correct asserts sacrificed):**

| book | exp02 FPs removed / sacrificed | exp03 FPs removed / sacrificed |
|---|---|---|
| 14 | ~105 / **−38 (net gain)** | ~116 / 232 |
| 15 | ~57 / **2** | ~64 / 311 |
| 17 | ~72 / **2** | ~75 / 326 |
| 18 | ~70 / **0** | ~125 / 259 |

**Verdict — success criteria met:**
- Beats baseline on accuracy AND assert precision in all four books.
- Identity accuracy (assertRate × precision) flat-to-up: b14 +0.019,
  b15/17/18 within ±0.001 — vs the blunt rule's −0.11 to −0.23.
- Q2 ≥ Q1 in every book; Book-15 is the proof case (Q1 regressed
  −0.006, Q2 gains +0.013) — run context crosses the confidence bar
  and re-guesses correctly, exactly the synthetic-book effect at scale.
- Selectivity: comparable FP removal to the blunt rule at 10–100× lower
  collateral. The static "distrust carryover" policy (exp03) is
  dominated.

**Infra bugs found and fixed during the ledger run** (all in this
worktree, ported to exp01 where relevant):
- `build_run_query` window now bounded by the full run box (non-monotonic
  spans) — regression test added (32 tests).
- CLI `_read_text` must use `newline=''` to match the parser's CRLF-
  preserving read; universal-newline translation shifted every span
  offset after the first break and crashed query building.
- CLI skips unreadable/unverifiable chapters instead of dying.
- `eval_fp_suite` stdout reconfigured to `errors="replace"` (chapter text
  contains U+F000-block chars that crashed cp1252 consoles).
