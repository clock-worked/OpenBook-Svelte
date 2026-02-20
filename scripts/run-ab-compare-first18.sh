#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PY_SERVICES="$REPO_ROOT/py_services"
BOOK_ROOT="C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14"

MAIN_PY="$REPO_ROOT/.venv/Scripts/python.exe"
BOOKNLP_PY="$REPO_ROOT/.venv-booknlp/Scripts/python.exe"

PARSER_CSV="$BOOK_ROOT/evaluation_summary_v2_first18.csv"
BOOKNLP_CSV="$BOOK_ROOT/booknlp_ab_eval_first18.csv"
MERGED_CSV="$BOOK_ROOT/ab_eval_merged_first18.csv"

cd "$PY_SERVICES"

# 1) Run current parser eval in main env
PYTHONUTF8=1 USE_TF=0 TRANSFORMERS_NO_TF=1 \
"$MAIN_PY" openbook_parser/evaluate_against_curations_v2.py \
  "$BOOK_ROOT" \
  --limit 18 \
  --output-csv "$PARSER_CSV" \
  "$@"

# 2) Run BookNLP eval in isolated env
PYTHONUTF8=1 USE_TF=0 TRANSFORMERS_NO_TF=1 \
  "$BOOKNLP_PY" openbook_parser/booknlp_ab_eval_v2.py \
  "$BOOK_ROOT" \
  --limit 18 \
  --output-csv "$BOOKNLP_CSV" \
  "$@"

# 3) Merge reports
"$MAIN_PY" openbook_parser/merge_ab_reports.py \
  "$PARSER_CSV" \
  "$BOOKNLP_CSV" \
  "$MERGED_CSV"

echo "Done. Merged report: $MERGED_CSV"
