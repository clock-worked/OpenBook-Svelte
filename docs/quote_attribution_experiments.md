# Quote Attribution Experiments

This document records parser hypotheses, measured results, rejected changes, and review guidance. It exists to prevent repeating unsuccessful experiments or tuning rules to isolated lines.

## Evaluation policy

- Curated `dialogue.json` `characterId` is the gold label.
- Legacy Book 14 records without `characterId` use `chosenSpeaker`, resolved through `characters.json`.
- Predictions are aligned by span overlap, not array position.
- Parser display names and aliases are resolved to canonical character IDs before scoring.
- Books 14–16 are development data, Book 17 is validation, and Book 18 is held out.
- Thresholds and transition weights must not be changed after looking at held-out results.
- A change should be retained only when it improves multiple books and does not depend on title-specific names or phrases.

## Relationship to published systems

OpenBook's ordered rules are closest to the deterministic two-stage sieve of Muzny et al. (2017): high-precision explicit evidence is resolved first and harder conversational evidence is applied later. That system first links quotes to mentions and then mentions to entities. It reports 87.5 F1 across three novels and a precision-oriented operating point of 90.4 precision at 65.1 recall.

Vishnubhotla et al. (2023) decomposes the problem into character identification, coreference, quotation identification, and speaker attribution. Their reported speaker-attribution accuracies include approximately 0.40 for original BookNLP, 0.61–0.62 when candidates are restricted to resolvable character mentions, 0.64 on unseen novels for a sequential model, and 0.68 on unseen novels for a PDNC-trained BookNLP variant. Explicit quotations are much easier than implicit/anaphoric ones.

Michel et al. (2024) augments contextual BookNLP features with global character/style embeddings learned only from reliable explicit quotations. This principally improves anaphoric and implicit cases.

Michel, Attali, and Epure (2026) report 94.5% overall accuracy on PDNC with encoder-based joint scoring of multiple quotations in a large shared context. This is an August 2026 preprint rather than an independently replicated result. Its key architectural lesson is to avoid scoring each quotation independently.

Sources:

- https://aclanthology.org/E17-1044/
- https://aclanthology.org/2023.acl-short.64/
- https://aclanthology.org/2024.findings-emnlp.744/
- https://arxiv.org/abs/2608.02359

These published numbers are not directly comparable to OpenBook's end-to-end Primal Hunter score because datasets, candidate knowledge, quote extraction, metrics, and train/test conditions differ.

## Baseline correction

An initial Book 14 benchmark reported 1215/2038 (59.6173%). It incorrectly normalized 267 legacy `chosenSpeaker` records as narration. After resolving those records correctly, the frozen baseline at commit `664a31a` is:

| Book | Correct | Dialogue lines | Accuracy |
| --- | ---: | ---: | ---: |
| 14 | 1268 | 2133 | 59.4468% |
| 15 | 1907 | 2723 | 70.0331% |
| 17 | 1677 | 2311 | 72.5660% |
| 18 | 1551 | 2398 | 64.6790% |

## Experiment 1: post-quote role descriptors

Hypothesis: a nominal tag such as `the alchemist said` can map to a uniquely named catalog character such as `Female Alchemist`.

Implementation: identify unique role tokens in post-quote attribution clauses and add them as candidate evidence.

Result:

- Small positive result on Book 14.
- One fewer correct attribution on held-out Book 17.
- Broad action-verb variants changed more lines but introduced additional regressions.

Decision: **rejected and reverted**. A role noun can describe an occupation, addressee, observer, or incidental concept. The available character catalog also contains many generated descriptive labels, making apparent uniqueness unreliable across books.

## Experiment 2: unknown-to-known backpropagation

Hypothesis: an uncertain turn bracketed by later explicit evidence can be retrospectively assigned to the named character.

Attempt A: matching explicit anchors around an uncertain turn plus candidate support.

- Book 14: +3 net correct, but 45 assignments changed and identifiable correct lines were broken.
- Book 17: no aggregate gain.

Attempt B: exactly one candidate at confidence >= 0.90, immediately followed by a matching explicit anchor, at most one paragraph apart.

- Books 14 and 17: zero qualifying changes and therefore no improvement.

Decision: **rejected and reverted**. Existing candidate confidence is not calibrated strongly enough for safe propagation. Future backpropagation should use dialogue-local latent unknown IDs and graph constraints rather than rewrite individual uncertain labels.

## Experiment 3: explainable episode-level joint decoder

Hypothesis: uncertain dialogue should be decoded jointly within short, strict two-participant episodes instead of independently. Explicit attributions remain immutable; alternation is a soft transition feature.

Implementation:

- Segment dialogue episodes when paragraph distance exceeds one.
- Require at least two hard explicit anchors.
- Require the union of anchor and candidate identities to contain exactly two participants.
- Run Viterbi decoding over candidate confidence scores.
- Apply a `+0.28` transition score when the speaker changes and `-0.28` when the speaker repeats.
- Preserve an attribution trace with algorithm, weights, participants, original candidate, and selected candidate.
- Expose `episode_joint_decode` as a parser option; enabled by default in the experiment.

Measured results with unchanged parameters:

| Book | Baseline | Joint decoder | Correct delta | Accuracy delta | Changed lines |
| --- | ---: | ---: | ---: | ---: | ---: |
| 14 development | 59.4468% | 59.8687% | +9 | +0.4219 pp | 55 |
| 15 development | 70.0331% | 71.4653% | +39 | +1.4322 pp | 62 |
| 17 validation | 72.5660% | 73.4747% | +21 | +0.9087 pp | 40 |
| 18 held out | 64.6790% | 64.9291% | +6 | +0.2502 pp | 33 |

Changed-line analysis:

- Book 14: 32 fixes and 23 regressions among 55 changed lines, net +9.
- Book 17: 29 fixes, 8 regressions, and 2 wrong-to-wrong changes among 39 aligned changed lines, net +21.

Decision: **retained**. The same fixed parameters improve every measured book, including validation and held-out books. However, changed-line precision is not yet high enough to regard every decoder override as automatic ground truth. Provenance must remain visible and these assignments should be prioritized for targeted review.

## Opacity and explainability

The system should become less opaque, not more. Each final attribution should expose:

- canonical character ID;
- original parser candidate;
- selected candidate;
- candidate confidence scores;
- hard evidence and soft features;
- episode participant set;
- decoder name and transition weights;
- whether the assignment was explicit, independently inferred, or jointly revised.

The retained decoder stores this information under `attribution.decisionTrace.episodeDecoder`.

## Manual review guidance

Manual review is useful when it is structured as error analysis rather than converted directly into book-specific rules. Review a stratified sample containing:

1. correct-to-wrong decoder changes;
2. wrong-to-correct decoder changes;
3. unresolved high-frequency confusion pairs;
4. two-person versus three-plus-person scenes;
5. scene changes and long narration interruptions;
6. explicit, nominal, pronominal, and fully implicit quotations;
7. unknown speakers that later receive a name;
8. aliases, titles, surnames, and vocatives.

For each reviewed line, record the evidence a human used: explicit tag, pronoun/coreference, addressee/vocative, turn sequence, active participant set, scene boundary, character voice, or outside-world knowledge. Add a sieve only when the same feature explains multiple errors across multiple books and can be tested against counterexamples.

Do not add rules containing Primal Hunter character names. Do not tune a threshold using Book 18 after this evaluation. Future iterations should improve participant-set detection, use quote-to-mention edges, and calibrate abstention before increasing decoder coverage.

## Experiment 4: ModernBookNLP joint benchmark adapter

Implementation: `scripts/python/quote_attribution/modernbooknlp_benchmark.py` now builds a raw UTF-8 corpus from ordered `chapter.txt` files, preserves a chapter character-offset map, normalizes Book 14 legacy `chosenSpeaker` labels through the canonical character catalog, runs ModernBookNLP joint scoring, maps coreference clusters to canonical IDs using weighted proper/common/pronominal entity surfaces, and aligns predictions to gold by one-to-one span overlap.

Reported metrics are quote-detection precision/recall/F1, speaker accuracy on matched quotes, speaker accuracy on resolved matched quotes, and end-to-end correct-speaker recall. Narration is excluded because ModernBookNLP predicts direct quotation speakers. Detailed per-match provenance is retained in the generated benchmark JSON.

Tests: `scripts/python/quote_attribution/test_modernbooknlp_benchmark.py` covers UTF-8 byte offsets, chapter concatenation, canonical alias mapping, legacy labels, token-to-byte conversion, one-to-one overlap alignment, and metric calculations.

Run contract:

```bash
<modern-python> scripts/python/quote_attribution/modernbooknlp_benchmark.py \
  --book-dir <Book-14-or-Book-15> \
  --work-dir throwaway_output/modernbooknlp/<book> \
  --book-id <stable-id>
```

Measured joint-checkpoint results:

| Book | Gold dialogue quotes | Predicted quotes | Matched | Quote P / R / F1 | Speaker accuracy on matched | End-to-end correct-speaker recall | OpenBook frozen baseline |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 14 | 2,133 | 2,176 | 879 | 40.40% / 41.21% / 40.80% | 686/879 (78.04%) | 686/2,133 (32.16%) | 59.45% |
| 15 | 2,723 | 2,254 | 812 | 36.02% / 29.82% / 32.63% | 697/811 (85.94%) | 697/2,723 (25.60%) | 70.03% |

Decision: **do not integrate ModernBookNLP as a replacement parser** on this data. The public joint checkpoint ran successfully on CUDA and provides strong canonical speaker labels for matched quotations, but its end-to-end quote extraction recall remains below OpenBook's frozen baseline. It may be useful as a complementary attribution signal after quote alignment improves. A key adapter pitfall was discovered and tested: `.quotes` indices use `token_ID_within_document`, not TSV row positions, and `quote_end` is exclusive. The token export can contain gaps after token splitting, so quote boundaries must use the nearest available document-token spans. Also, BookNLP's `byte_onset`/`byte_offset` labels are actually spaCy Unicode character offsets and therefore align directly with OpenBook spans.

## Closed-world character continuity workflow

Implementation: existing `characters.json` entries are now passed forward with each chapter parse as a canonical ID/name/alias/gender catalog. Once a non-empty catalog exists, parser-discovered surfaces are no longer silently appended as canonical characters. Unresolved or out-of-catalog candidates appear in the chapter character panel, where review can create a canonical character (display name, gender, collision-safe ID) or attach the surface as an alias of an existing character. The reviewed line is immediately remapped and the persisted book catalog feeds subsequent parses.

The pure catalog/review logic is covered by Node tests in `closedWorldCharacterWorkflow.test.ts` and `parserCatalog.test.ts`. Existing empty projects retain bootstrap behavior until their first catalog entries exist.
