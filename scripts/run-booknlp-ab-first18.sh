#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PY_SERVICES="$REPO_ROOT/py_services"
BOOK_ROOT="C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-14"
PY="$REPO_ROOT/.venv-booknlp/Scripts/python.exe"

if [[ ! -f "$PY" ]]; then
  echo "Missing interpreter: $PY"
  exit 1
fi

cd "$PY_SERVICES"
PYTHONUTF8=1 USE_TF=0 TRANSFORMERS_NO_TF=1 \
  "$PY" openbook_parser/booknlp_ab_eval_v2.py \
  "$BOOK_ROOT" \
  --limit 18 \
  --output-csv "$BOOK_ROOT/booknlp_ab_eval_first18.csv" \
  "$@"
