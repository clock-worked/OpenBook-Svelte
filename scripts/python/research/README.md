# Research Scripts

Offline experiments on the parser and on speaker attribution: evaluations against curated chapters, A/B runs, report merges and curation ingest. Nothing here runs in the app, and product code in `py_services/` must not import from here (`py_services/test_import_boundaries.py` checks it).

Each script puts `py_services` on `sys.path` through `scripts/python/_path_setup.py`, so it imports backend modules as `openbook_parser.…`, `api_models`, and so on.

| File | Role |
| --- | --- |
| `evaluate_against_curations.py` | Evaluate the parser against curated `.script.json` gold (v1 format) |
| `evaluate_against_curations_v2.py` | Per-chapter accuracy and character precision/recall against curated `dialogue.json` |
| `evaluate_heuristics_gate.py` | Curated accuracy gate to run before and after a heuristics change (saves a baseline, fails on regression) |
| `evaluate_dialogue_ai_against_curations.py` | Benchmark harness for the AI dialogue-assist providers |
| `evaluate_descriptors_ab.py` | Closed-world dialogue accuracy with learned descriptors off vs on |
| `booknlp_ab_eval_v2.py` | A/B of the current parser vs BookNLP on curated chapters |
| `merge_ab_reports.py` | Merge the parser v2 eval CSV with the BookNLP A/B CSV |
| `simple_eval.py` | Compare curated results without running the full parser |
| `standalone_ingest.py` | Curation ingest with no ML dependencies |
| `generate_blocklist.py` | Build a speaker blocklist from predicted speakers that are not in the gold |
| `local_attribution_audit.py` | Re-run the parser on curated chapters and classify each miss (blind spot, scoring gap) |

Run from the repo root, for example:

```bash
.venv/Scripts/python.exe scripts/python/research/evaluate_against_curations_v2.py --help
```
