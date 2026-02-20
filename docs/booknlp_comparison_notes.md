# BookNLP vs Current Parser: Comparison Notes

## Purpose
This document summarizes how BookNLP was evaluated against the current OpenBook parser, what the measured results were on Book 14 (first 18 chapters), and what the data suggests about next improvements.

## What was compared
Two attribution systems were compared against curated `dialogue.json` labels:

1. Current OpenBook parser
- Rule-based attribution with coreference support.
- Evaluated using [py_services/openbook_parser/evaluate_against_curations_v2.py](../py_services/openbook_parser/evaluate_against_curations_v2.py).

2. BookNLP pipeline
- Entity + quote + coreference pipeline from BookNLP.
- Converted to OpenBook-style line attribution via [py_services/openbook_parser/booknlp_ab_eval_v2.py](../py_services/openbook_parser/booknlp_ab_eval_v2.py).

Merged comparison output is generated with [py_services/openbook_parser/merge_ab_reports.py](../py_services/openbook_parser/merge_ab_reports.py).

## How BookNLP works in this integration
BookNLP processes each chapter text and writes intermediate files (tokens, entities, quotes, book summaries). The OpenBook adapter then:

1. Reads BookNLP quote spans and attributed character IDs.
2. Maps BookNLP coreference IDs to likely speaker names.
3. Canonicalizes names using OpenBook aliases/known character list (when available).
4. Aligns BookNLP quote lines to curated dialogue lines by normalized quote text (with positional fallback).
5. Scores predicted speaker against curated `characterId`.

Key implementation details are in [py_services/openbook_parser/booknlp_ab_eval_v2.py](../py_services/openbook_parser/booknlp_ab_eval_v2.py).

## Environment setup used
A dedicated environment was created to isolate BookNLP and avoid conflicts with the main parser environment:

- Repo-local env: `.venv-booknlp`
- Wrapper scripts:
  - [scripts/run-booknlp-ab-first18.sh](../scripts/run-booknlp-ab-first18.sh)
  - [scripts/run-booknlp-ab-first18.cmd](../scripts/run-booknlp-ab-first18.cmd)
  - [scripts/run-ab-compare-first18.sh](../scripts/run-ab-compare-first18.sh)

Windows-specific runtime constraints handled:
- TensorFlow import disabled via env vars for transformers paths.
- UTF-8 mode enabled for Python process running BookNLP.
- Chapter text normalized before BookNLP processing.

## Current measured results (Book 14, chapters 00-17)
Source reports:
- Current parser eval: `evaluation_summary_v2_first18.csv`
- BookNLP eval: `booknlp_ab_eval_first18.csv`
- Merged eval: `ab_eval_merged_first18.csv`

Aggregate dialogue attribution accuracy:
- Current parser: `280/531 = 0.5273`
- BookNLP: `273/531 = 0.5141`
- Delta (BookNLP - current parser): `-0.0132`

Interpretation:
- BookNLP is close, but currently slightly below the existing parser on this dataset slice.
- This is not a complete replacement win yet.

## Why early smoke output showed 0
The initial smoke run used chapter 00 (`Previously On`), which has no dialogue lines in curated labels. That made dialogue accuracy appear as zero/no-signal for that chapter.

A run on chapter 01 confirmed non-zero attribution from BookNLP.

## Error type breakdown for BookNLP
A targeted analysis split BookNLP dialogue errors into two buckets:

- Likely alias/cluster mapping problems: `120 / 258` errors (`46.5%`)
- Likely true attribution mistakes between known speakers: `137 / 258` errors (`53.1%`)
- Narrator/missing quote edge case: `1 / 258` errors

Takeaway:
- Performance gap is mixed: both canonicalization/mapping and speaker attribution quality matter.
- Fixing mapping alone is likely helpful but not sufficient.

## Practical recommendations
1. Improve canonicalization first (low-risk, high-yield)
- Add deterministic mapping rules for recurring BookNLP speaker names to OpenBook IDs.
- Add confusion-driven alias expansions from merged diagnostics.

2. Reduce fallback alignment usage
- Current BookNLP adapter uses many positional fallbacks in long dialogue-heavy chapters.
- Add stronger quote matching heuristics (span overlap + punctuation-aware normalization + local context window).

3. Keep A/B harness as regression gate
- Continue tracking both systems per chapter using merged report.
- Promote changes only when aggregate and worst-chapter metrics improve.

4. Consider hybrid operation, not replacement
- Use current parser as default.
- Use BookNLP as a secondary candidate source for low-confidence lines.

## One-command comparison run
From repo root in Git Bash:

`bash scripts/run-ab-compare-first18.sh --chapter-regex "^(0[0-9]|1[0-7])\\s-\\s"`

This regenerates parser + BookNLP reports and writes merged output to:

`C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14/ab_eval_merged_first18.csv`
