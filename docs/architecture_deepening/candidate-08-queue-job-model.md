# Candidate 8 — One queue job model

**Status:** implemented on `refactor/c08-queue-job-model` — decision record: [ADR 0020](../adr/0020-queue-job-model.md)

**As built (where the code had moved since this was written):** line numbers above are from 2026-11-06 (the shadow planner had moved to `AudioPanel.svelte` ~931–1001, `processQueue` to ~1003–1103). The job model's home is a pure module, `services/audioQueueJob.ts` (type, transitions, persisted view), applied by `stores/audioQueue.ts`, so it is tested without a store. The transition set gained `cancel` (the overlay's "Cancel all": queued → canceled, running → cancel requested), and `requeueRunningJobs` became part of the restore. The rebuild reads persisted `lineRefs`, so "the same `QueueJob`" also holds for multi-chapter and speaker-source jobs.

**Part of:** architecture review 2026-11-06, candidates 1–8
**Companion issue:** see `docs/architecture_deepening/README.md`

## Problem

"Which lines are missing for a character in a chapter" has **two implementations that must agree**: the real planner `audioQueuePlanning.ts` (context-DI `AudioPlannerContext`, shared `collectChapterMissingLines` 303–338 — a genuine wave-3 consolidation for the multi-chapter paths) and a **shadow single-chapter planner** in `AudioPanel.svelte:815–885` (`enqueueCharacterGeneration`) with its own missing-line loop (840–852) and job assembly (857–868) that bypasses the planner. They agree today only by construction (both wired to the same `checkAudioExistsForLine`) and will silently diverge.

A **queued job lives in 3 stores of truth**: the `audioQueue` store item (persisted to `localStorage`, `audioQueue.ts:37,135–142`), the component-local `queuedJobs: Map<string, PlannedQueueJob>` (`AudioPanel.svelte:73`, lost on navigation), and the disk-rebuilt payload (`rebuildQueuedJobPayload`, invoked at `AudioPanel.svelte:760–779` when the map misses). `processQueue` (887–1009) orchestrates the join of all three. The restart-rebuild path — the most failure-prone join — has **no test**.

`audioQueue.ts` exposes **15 fine-grained actions**, each a trivial `.map` over items; the actual state machine (which transitions are legal) lives nowhere — in `processQueue`. `enqueueAudioJob` (154–181) contains a subtle double-evaluation: an IIFE computes a filtered `items` that is then **overwritten** by a separate `items:` property re-testing `some(active)` — the two filters can in principle disagree.

## Design

**One planner, one job model, one state machine.**

- **One planner:** the panel's single-chapter enqueue goes through `audioQueuePlanning` (the context-DI planner); the shadow planner is **deleted**. "Missing lines for a character" has one implementation.
- **One job model:** a single `QueueJob` type is the shape across persistence (localStorage), in-flight, and rebuild (disk). The three stores of truth become **views over one model**; `rebuildQueuedJobPayload` reconstructs the same type. In-flight state lives in the store — the component holds no parallel `Map`.
- **One state machine:** the 15 actions collapse to the transition set — `enqueue`, `start`, `progress(phase, …)`, `finish(ok | fail | cancel)`, `remove`, `clearFinished`, `next`. The store validates transitions (the state machine has a home), and `enqueueAudioJob`'s double-evaluation is fixed by construction.

## Decisions

1. Shadow planner deleted — single-chapter paths use the planner (parity by construction, not by convention).
2. In-flight job state moves into the `audioQueue` store; the component-local `queuedJobs` Map is deleted.
3. The 15 actions collapse to the transition set; illegal transitions rejected in the store.
4. Job progress is fed by candidate 7's record (the `progress` transition takes the record's phase).

## Fences (out of scope)

- The progress record the job displays → candidate 7.
- The job payload's provider/endpoint choice → candidate 3 (the profile).
- Playback (`stores/audio.ts` callback-in-state seam, O(lines) reads per advance) — **not** in this candidate; noted as a future candidate if the audio area keeps being touched.

## Tests

- **Restart-rebuild path** (the currently untested, most failure-prone join): a job survives store reload + map miss → disk rebuild produces the same `QueueJob`.
- State machine: legal/illegal transition tests.
- Planner: the single-chapter cases the panel formerly handled (parity with the shadow planner's former behavior).
- Existing `audioQueuePlanning.test.ts` (288 lines) stays green.

## Landing order (one commit each)

1. `QueueJob` type + store state machine (transition set, double-evaluation fixed).
2. Shadow planner deleted; single-chapter enqueue through the planner.
3. Rebuild path on the one type; component `Map` deleted.
4. Tests (restart-rebuild, transitions, planner parity).
