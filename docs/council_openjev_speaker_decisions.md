# Council Memo — Open-Jev for Speaker Decisions in OpenBook

**Date:** 2026-07-09
**Question:** How should OpenBook use Open-Jev (open-weights typed decision model) for speaker decisions in generated audiobooks — per-line speaker/character attribution (`characterId` in `dialogue.json` v3), narration-vs-dialogue detection, and voice/speaker assignment (`voices.json` v2.0)?
**Scope:** `py_services` attribution path only (new provider alongside Gemini and local llama). Excludes TTS model selection, parser rework, UI redesign, commercial licensing.
**Advisors:** `council-dreamer`, `council-stats`, `council-wiki` (all fresh-context, read-only, 2 passes). Pass 1 parallel launch failed 3/3 on provider admission timeout (`inference request expired while waiting for admission`, workflow `0c2ca931`); same-protocol sequential retry succeeded (workflow `8c8c0bae`). Pass 2 cross-exam (workflow `cd07e6b3`) used fresh-context fallback (advisors not resumable) — labeled, not true resume.

## Recommendation

Add Open-Jev as a **third parallel provider** in `py_services` following the existing local-dialogue pattern — not a replacement of Gemini.

1. **Backend:** new `openjev_local_service.py` + `/api/openjev-dialogue` endpoint pair in `parser_router.py` (mirrors `/api/local-dialogue-ai`). Thin client to a configurable `/v1/systemone` base URL. Reuse `build_assist_context` / `_build_scene_roster` from `dialogue_ai_context.py` verbatim as the `state` text.
2. **Question shape:** per line, one `choice` question over the **scene/chapter-local roster + a narrator/None option** (book rosters exceed the 52-option cap: Book 15 ~105, Book 19 74), plus one `noul` "dialogue vs pure narration" question. Freeze option order deterministically (upstream reports ~2.3% flip sensitivity). Candidate labels should carry name + alias + gender (rosters contain descriptive labels like `beastkin-male`, `warlord`).
3. **Decision contract:** map Open-Jev's top-option probability onto the **existing** `auto_apply_threshold = 0.92` disposition contract (`api_models.py:267`; `dialogue_ai_service.py:1807`). One threshold, no new parameters in the MVP. Record full per-option probability vector in `decisionTrace` (provider=`openjev`).
4. **Serving:** bf16 9B (LoRA adapter + decision head on Qwen/Qwen3.5-9B, pinned revision `c2022362...`), TP=1 on **one** RTX 3090 under WSL2, using upstream's documented single-GPU server (`python -m jev.server --device cuda:0 --max-length 4096 --batch-size 1`, Transformers 5.10.2 + PEFT 0.19.1) or the vLLM 0.29.0 shim — decided by the spike. Fallback: OpenJevPro wrapping Qwen3-8B on the existing LM Studio endpoint (`127.0.0.1:1234`, already serving llama-3-8b). MLX is moot (Windows box).
5. **Phase 2 (post-gate):** fast-path cascade — auto-apply high-confidence Open-Jev lines, escalate the rest to the existing Gemini tool loop. Voice casting as a `choice` over the voice roster per character (text-only, cheap).

## Dispute adjudication (Pass 2)

| Dispute | Outcome | Rationale |
|---|---|---|
| **1. MVP shape: cascade vs toggle** | **Converged: toggle + single 0.92 auto-apply gate in the Open-Jev branch; full Gemini-escalation cascade = phase 2.** | Stats and Wiki verified the cascade machinery already exists end-to-end (threshold `api_models.py:267`, disposition mapping `dialogue_ai_service.py:1807,1863`, UI auto-apply `dialogueAiAssist.ts:199`, benchmark `ai_auto_apply` stage) — a third provider reusing it costs ~zero extra machinery. Dreamer **withdrew** the added `marginToSecond ≥ 0.25` gate: `marginToSecond`/`misattributionRisk` gate *context enrichment only* (`dialogue_ai_service.py:869-876`), not disposition, so it is an uncalibrated new parameter, not reuse. |
| **2. Serving: FP8 TP=2 vs bf16 TP=1** | **Converged: bf16, TP=1, one 3090.** | Dreamer **withdrew** FP8 TP=2 (was the H100-class recipe from the brief). 3090 is Ampere SM86 — no native FP8 tensor cores; vLLM's Ampere FP8 is Marlin weight-only dequant (memory savings, no compute speedup; vLLM PR #5975). bf16 9B (~18 GB weights + KV) fits one 24 GB card and leaves the second card for BookNLP/Unsloth training. Upstream's own documented path is its single-GPU server, not vLLM. |
| **3. Threshold books** | **Converged on the frozen policy.** Tune on dev Books 14–16; confirm on Book 17 (validation); Book 18 held out, **one confirm-only pass, no retuning after** (`docs/quote_attribution_experiments.md`: "Do not tune a threshold using Book 18"). Dreamer conceded. |

## Corrections surfaced by cross-exam (accepted, update the pass-1 common ground)

- **License:** the 9B checkpoint (`ZefanCai/Open-Jev-9B`) is **Apache-2.0** adapter/head + **MIT** source code; upstream Qwen3.5-9B under its own separate terms. The "CC BY-NC 4.0" in the pass-1 brief applied to other artifacts in the openjev org, **not** this checkpoint (verified directly against the HF README). Personal use is clean; redistribution of fine-tuned checkpoints still needs the upstream Qwen terms checked.
- **Benchmark harness is Gemini-hard-wired:** `evaluate_dialogue_ai_against_curations.py:31,642` imports/instantiates `DialogueAiAssistService` directly. A small provider/backend flag is a **required** change before Open-Jev can run the frozen protocol.
- **4096-token hard cap per candidate:** upstream rejects (does not truncate) candidates over the limit — the context builder must budget tokens per candidate label/state or whole requests fail on long scenes.
- **Calibration warning (verbatim, HF README):** "Synthetic held-out decision metrics do not establish broad real-world reliability… or a calibrated probability guarantee outside the evaluated distribution." 9B Wiki OOD expected accuracy is 32.74% — books are the closest domain to the model's weakest evaluated subgroup. Do not auto-apply on raw probabilities before calibration on Books 14–16.
- **Latency claims are H100-specific** (~80–210 ms measured on one H100, loopback, prefix cache off); expect 3090-bf16 to be several times slower. The fast path must still beat the Gemini tool-loop latency to matter.
- **Hardware open question resolved:** dev box is Windows 11 with **2× RTX 3090 24 GiB**, CUDA 12.4 driver 610.74, WSL2 (RAM capped 24 GB by `.wslconfig`) + LM Studio present (`quote-attribution-bench-research/ASSESSMENT.md`).

## Pre-registered promotion gate (toggle → cascade)

Before Book 18 is opened:

1. On dev Books 14+15: auto-apply precision on auto-applied lines **≥ 95%**, net `ai_auto_apply` accuracy **≥ full-escalation baseline − 0.5 pp**.
2. Book 17 (validation): `ai_auto_apply` accuracy **≥ 72.57%** (frozen baseline) **and** auto-apply precision **≥ 95%**; coverage target ≥ 30% of dialogue lines.
3. Book 18: exactly one confirm-only run, **≥ 64.68%**, no threshold changes afterward.

Fail on 17 ⇒ Open-Jev stays an **abstaining pre-filter / suggestion mode** (proposes only lines it is sure about), cascade deferred. The same benchmark also serves as the go/no-go for the whole path: if Open-Jev matched-line accuracy lands well below both the frozen baselines (59.45/70.03/72.57/64.68%) and the llama-8b pass, drop the Open-Jev-weights path and use OpenJevPro-on-LM-Studio as the local provider.

## Execution plan (MVP, in order)

1. **Day-1 serving spike** (owner-approved gate before any `py_services` code): load bf16 9B on one 3090 under WSL2 (second card reserved for training); serve `/v1/systemone` via upstream server (preferred) or vLLM 0.29 shim; pass = loads in 24 GB, valid probability vector on a frozen sample scene, p50 latency recorded; fail → OpenJevPro on LM Studio.
2. **Harness change:** provider flag in `evaluate_dialogue_ai_against_curations.py` (small, required).
3. **`openjev_benchmark.py`** reusing existing span-alignment + gold mapping on Books 14/15 (tune), 17 (confirm), 18 (one-shot).
4. **Router + service wiring** in suggestion mode writing `characterId` + `decisionTrace` into `dialogue.json` v3; provider entry in `apiContract.ts` + toggle in `SpeakerManager.svelte`. Frontend otherwise unchanged.
5. **Phase 2 (post-gate):** cascade to Gemini; voice-roster `choice` questions; FP8-Marlin only if KV pressure forces it.

## Owner decisions (Chad)

1. Approve the **bf16 TP=1 single-3090 WSL2 spike recipe** (upstream server preferred over vLLM shim) and reserve the second 3090 for training during it.
2. Approve the **pre-registered promotion gate numbers** (above) before Book 18 is opened.
3. **Serving stack ownership:** maintain the upstream `python -m jev.server` (Transformers) path vs the vLLM shim — who keeps it current against vLLM/Transformers releases.
4. **UI default provider** when multiple are enabled (Gemini vs llama vs Open-Jev).
5. Acknowledge the one-shot, irreversible **Book 18 confirm-only** run (no retuning allowed afterward).

## Residual risks

- **OOD gap** is the decisive unknown: published accuracy is on general text; web-novel prose is likely OOD (9B Wiki OOD 32.74%). Only the local benchmark settles it, before any integration cost.
- **Serving depends on the project's loader/shim** — a plain `AutoPeftModel` call cannot apply the separate decision head; if the shim is unmaintained, the upstream single-GPU server is the fallback (forfeits vLLM batching).
- **KV headroom:** bf16 9B on 24 GB is tight with long assist contexts + up-to-52 candidate labels; budget via the 4096-token cap, keep batch=1.
- **WSL2 instability** (RAM cap 24 GB, GPU passthrough) — the LM Studio fallback exists precisely for this.
- **Descriptive roster labels** make choice between near-identical candidates hard; include alias/gender in label text.
- **Miscalibration:** if reliability plots on Books 14–16 show top-p unreliable, keep Open-Jev abstain-only.

## Evidence and run IDs

- Pass 1 (retry, all fresh-context): workflow `8c8c0bae-4285-48ba-b23a-8122f6a2be95`; children `6cd9cce0` (dreamer), `d35376bc` (stats), `35b8e9d1` (wiki).
- Pass 2 (fresh-context fallback cross-exam): workflow `cd07e6b3-d8d8-4fe3-b20b-2938b5a74aea`; children `2e4b620b` (dreamer), `23203d7c` (stats), `a8588d7c` (wiki).
- First Pass 1 attempt `0c2ca931` — infrastructure failure (provider admission timeout, 3/3 lanes), not a council-protocol failure.
- Repo evidence (verified by advisors and re-verified by parent): `py_services/api_models.py:267`, `py_services/dialogue_ai_service.py:869-876,1134,1807,1863`, `py_services/local_dialogue_ai_service.py:19-34`, `py_services/dialogue_ai_context.py:355,395`, `py_services/parser_router.py:236-261`, `py_services/openbook_parser/evaluate_dialogue_ai_against_curations.py:31,430-442,611,642`, `docs/quote_attribution_experiments.md:11-12,134`, `quote-attribution-bench-research/ASSESSMENT.md:3`, `C:\Users\Chad\.wslconfig`.
- Web evidence: `huggingface.co/ZefanCai/Open-Jev-9B` (README: license, base model/revision, serving command, OOD warning — parent re-verified verbatim), `huggingface.co/openjev/openjev`, `pypi.org/project/openjevpro/`, `vllm-project/vllm` PR #5975 (Marlin FP8 on Ampere), vLLM 0.29.0 release.