# Quote Attribution Miss Audit

Date: 2026-09-11. Scope: Primal Hunter Books 14, 15, 17, and 18.

## Coverage and Method

Reproduced the current sequential benchmark across all 323 chapters: **7,209/9,559 correct (75.416%)**, leaving **2,350 misses**. All misses are exported with source context and decision traces. Of these, 58 are unmatched gold quotes; the remaining 2,292 are matched attribution errors. Every matched prediction's span extracts its exact text from the source. No ModernBookNLP fallback occurred.

Each chapter was scored before gold review learned descriptors for subsequent chapters. Review used temporary copies, not curated files. Production parser behavior and gold labels were not changed. CRLF line endings were preserved while slicing source offsets.

All quotes were mechanically scanned for candidate cues. Representative fixes, counterexamples, and residual misses were inspected directly; this is not a claim of manual adjudication of every ambiguous quote. Cue statistics below are offline replacements of saved predictions, not an end-to-end benchmark of an implemented fix. Rules do not consult gold to select a speaker. Names and aliases come from each book's catalog, and ambiguous surfaces are excluded.

## Measured Opportunities

| Replay policy | Fixes | Regressions | Net correct |
| --- | ---: | ---: | ---: |
| Immediate named post-quote speech tag, excluding configured reaction verbs | 95 | 2 | +93 |
| Above, otherwise use subject-led same-paragraph pre-quote speech tag | 209 | 7 | +202 |
| Punctuated `NAME began/started/finished` post-tag | 10 | 0 | +10 |
| Immediate post-tag, otherwise use single named actor before quote | 393 | 10 | +383 |

Policies overlap and must not be added together. Wrong-to-wrong changes are recorded separately in the machine-readable summary. The conservative post/pre-tag policy also changes one wrong prediction into another wrong prediction; the action policy has three such changes.

The conservative post/pre-tag policy projects 7,411/9,559 (77.529%), versus 75.416% currently. Its per-book net changes are **+85, +6, +107, +4**. The broader action policy projects 79.423%, but action narration can describe a listener or a speaker change and needs stronger guards. Neither projection includes downstream changes to descriptor learning or dialogue propagation.

## Implemented Benchmark

The conservative production changes were benchmarked end to end after implementation: quote-local context now stops at neighboring dialogue spans, grammatical speech subjects take precedence over named addressees, configured reaction verbs are excluded from hard attribution, punctuation-bounded `began/started/finished` tags are recognized, and explicit local attribution survives consistency and ModernBookNLP overrides.

The fresh sequential result is **7,314/9,559 correct (76.514%)**, a gain of **105 quotes and 1.098 percentage points** over the 7,209 baseline. All 323 chapters completed with 9,501 matched quotes, zero backend fallbacks, and no non-ModernBookNLP chapter results.

| Book | Baseline | Implemented | Correct delta | Accuracy delta |
| --- | ---: | ---: | ---: | ---: |
| Book 14 | 1,470/2,133 | 1,522/2,133 | +52 | +2.438 pp |
| Book 15 | 2,054/2,717 | 2,058/2,717 | +4 | +0.147 pp |
| Book 17 | 1,760/2,311 | 1,814/2,311 | +54 | +2.337 pp |
| Book 18 | 1,925/2,398 | 1,920/2,398 | -5 | -0.209 pp |

Across stable quote keys, 212 predictions changed: 152 wrong predictions became correct, 47 correct predictions became wrong, and 13 changed between wrong speakers. The explicit-evidence guard retained the local speaker against 84 conflicting ModernBookNLP proposals; 76 retained predictions were correct and 8 were incorrect. Book 18 accounts for 14 of the 47 regressions. Several involve literal `Arachnec` tags conflicting with curated Archweaver or Nestmother labels, but all remain counted as regressions pending separate gold review.

The replay projection was therefore directionally useful but overstated the realized gain. Context bounding and downstream ranking/propagation interact with the local rules, so only the end-to-end result should be treated as production accuracy.

## Concrete Findings

### 1. Literal Speaker Tags Lose to Later Overrides

- Book 17, Chapter 1261, gold line 28: `"Thank you for your support," Jacob said` is assigned to Jake. The trace records the correct previous speaker, Jacob, before ModernBookNLP overwrites it.
- Book 17, Chapter 1290, gold line 67: `"Vesperia?" Jake asked` is assigned to the Viper. Again, the pre-ModernBookNLP speaker was correctly Jake.
- Book 14, Chapter 1, gold line 57: a quote immediately followed by `Carmen said` is assigned to Jake by `same_paragraph_context_consistency`.

Recommendation: protect narrowly validated, quote-local speech subjects through consistency and ModernBookNLP passes. Do not simply trust the existing `nextSentenceAttributedSpeaker` signal: replaying that signal wholesale yields 111 fixes but 188 regressions.

### 2. Context Crosses Into Other Quotes

The parser slices pre/post context to physical paragraph boundaries, not neighboring dialogue boundaries. Some Book 14 paragraphs contain multiple speakers. At least **117 misses** have a post-tag signal despite having no intervening narration before the next quote. The tag must have come from beyond that immediate gap. This count identifies suspect cases, not guaranteed fixes.

For example, Book 14 Chapter 1, gold line 37 is Carmen's explanation, but the trace supplies Jake as a post-quote speaker although the immediate gap contains only closing/opening quote marks. Other nearby quotes repeat the same failure.

Recommendation: bound attribution context to neighboring quotes and make same-paragraph continuation conditional on actual local evidence. A physical paragraph is not necessarily one speaker's turn. Changing this boundary needs a fresh full benchmark; its effect cannot be inferred by summing the counts above.

### 3. Obvious Speech-Onset Tags Are Missing

`began` is absent from the speech-verb list. All ten observed fixes in the punctuation-bounded phase-tag probe use it. Examples:

- Book 14, Chapter 2, line 21: `"Thank you all for coming here today," Miranda began.` Predicted William; gold Miranda.
- Book 15, Chapter 1102, line 49: `Artemis began.` follows the quote, but the prediction does not resolve to a catalog character.
- Book 15, Chapter 1066, line 18: `Jacob began.` follows the quote; predicted Jake.

Recommendation: recognize speech onset/completion in quote-local syntax. The audit requires punctuation after `began/started/finished`, rejecting forms such as `Alex started walking`. These are generic language patterns, not book-specific identities.

### 4. The Addressee Can Be Mistaken for the Subject

Verified directly: `infer_sentence_attributed_speaker("Morgan asked Alex.", ["Alex", "Morgan"])` returns **Alex**, not Morgan. The helper accepts both `NAME VERB` and `VERB NAME` without distinguishing a direct object from inverted attribution. Catalog ordering can determine the result.

The strict corpus probe found three affected decisions: two fixes and one gold-scored regression. Recommendation: prefer the grammatical speech subject and distinguish `Morgan asked Alex` from inverted `asked Morgan`.

### 5. Reaction and Speech Evidence Are Mixed

`nodded` occurs in both speech and reaction verb files, and the speech list also contains `summoned`. A listener nodding after a quote is not a reliable speech attribution. Excluding configured reactions raises immediate post-tag precision from 3,025/3,059 to 2,583/2,589. Pre-quote action evidence is useful but should stay weaker than an explicit post-tag.

## Gold Review Candidates

The two regressions for strict immediate post-tags appear inconsistent with literal narration. They remain counted as regressions; no labels were changed:

- Book 14, Chapter 37, line 74 is labeled Ell'Hakan, but the next narration says `the Augur said`. Lines 72 and 76 are Jacob, around Ell'Hakan's interjection on line 73.
- Book 15, Chapter 1099, line 78 is labeled Jake, but its narration says `the Sword Saint asked Jake`.

## Recommendation

Start with bounded attribution windows and preservation of explicit speech subjects, plus the small speech-onset omission. Follow with guarded pre-quote subject cues. Leave broader action-based attribution and descriptor recalibration until these simpler paths are tested end to end. Do not apply blind previous-paragraph carryover: the audit found 325 fixes versus 818 regressions.

## Artifacts

- [All 2,350 misses](../throwaway_output/attribution_benchmark/miss_audit/all_misses.jsonl)
- [Cue counts and replay results](../throwaway_output/attribution_benchmark/miss_audit/cue_summary.json)
- [Every changed cue example, including regressions](../throwaway_output/attribution_benchmark/miss_audit/cue_examples.json)
- [Chapter coverage and baseline counts](../throwaway_output/attribution_benchmark/miss_audit/summary.json)
- [Implemented benchmark counts and decisions](../throwaway_output/attribution_benchmark/miss_audit_after_explicit_guards/summary.json)
- [Sequential quote exporter](../scripts/python/quote_attribution/audit_missed_quotes.py)
- [Offline cue evaluator](../scripts/python/quote_attribution/summarize_missed_quote_audit.py)