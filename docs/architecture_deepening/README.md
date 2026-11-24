# Architecture deepening — candidates 1–8

From the 2026-11-06 architecture review (HTML report: `C:\Users\CJDJ\AppData\Local\Temp\opencode\architecture-review-2026-11-06.html`). The owner selected candidates 1–8 for work; 9–11 are parked for later consideration.

**Recurring diagnosis:** the app's highest-traffic concepts have no owning module — they are re-stated as dict keys, string sets, and threshold literals at every seam. Each candidate below gives one concept a single owner (a deep module: small interface, behaviour behind it, testable through it).

**Process:** per candidate — design doc here (decision record) → GitHub issue (implementation file, with the landing-order checklist) → implementation in the listed commit steps.

## In progress

| # | Candidate | Doc | Issue |
|---|-----------|-----|-------|
| 1 | Give the attribution decision an owner (trace contract + rule registry + policy spec) | [candidate-01](candidate-01-attribution-decision-owner.md) | [#4](https://github.com/clock-worked/OpenBook-Svelte/issues/4) |
| 2 | Consolidate the chapter persistence module (save + load owner) | [candidate-02](candidate-02-chapter-persistence-owner.md) | [#5](https://github.com/clock-worked/OpenBook-Svelte/issues/5) |
| 3 | One provider profile, not six copies | [candidate-03](candidate-03-provider-profile.md) | [#6](https://github.com/clock-worked/OpenBook-Svelte/issues/6) |
| 4 | Give the audio manifest an owner | [candidate-04](candidate-04-manifest-owner.md) | [#7](https://github.com/clock-worked/OpenBook-Svelte/issues/7) |
| 5 | One answer for character stats (extends ADR-0012) | [candidate-05](candidate-05-character-stats-owner.md) | [#8](https://github.com/clock-worked/OpenBook-Svelte/issues/8) |
| 6 | Name the TTS engine seam (consistent with ADR-0011) | [candidate-06](candidate-06-tts-engine-seam.md) | [#9](https://github.com/clock-worked/OpenBook-Svelte/issues/9) |
| 7 | One pipeline progress record | [candidate-07](candidate-07-pipeline-progress-record.md) | [#10](https://github.com/clock-worked/OpenBook-Svelte/issues/10) |
| 8 | One queue job model | [candidate-08](candidate-08-queue-job-model.md) | [#11](https://github.com/clock-worked/OpenBook-Svelte/issues/11) |

## Dependencies / suggested order

- **2 before 5** (5 consumes 2's explicit stats step) and **2 before 3/4** for the audio-service rewires.
- **3 before 6** (6's adapters are named by 3's profile) and **3 before 8** (job payload provider).
- **6 before 7** (7 carries the plan's true chunk counts) and **7 before 8** (job progress field).
- 1 is independent of the rest.

A safe sequence: **1, 2, 3, 4, 5, 6, 7, 8**.

## Parked (not selected)

- 9 — Give the parser a text interface (tempfile round-trip, path-string chapter identity, options blackboard)
- 10 — Give the py providers a shared run shape (3 near-twin provider modules, JEV window in 3 files/2 languages)
- 11 — Sweep the pass-throughs (dead clients, `api_models` 331-line flat namespace, …)

## Cross-language parity fixtures (established pattern)

`tests/character_identity_vectors.json` (existing) → extended by this effort with `attribution_policy_vectors.json` (candidate 1), `provider_profile_vectors.json` (candidate 3), and a progress-record shape vector (candidate 7).
