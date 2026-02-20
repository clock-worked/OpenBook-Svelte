@echo off
setlocal

for %%I in ("%~dp0..") do set REPO_ROOT=%%~fI
set PY_SERVICES=%REPO_ROOT%\py_services
set BOOK_ROOT=C:\Users\Chad\Documents\Code\Python\Useful-Scripts\Data\Resources\Primal-Hunter\Book-14
set PY=%REPO_ROOT%\.venv-booknlp\Scripts\python.exe

if not exist "%PY%" (
  echo Missing interpreter: %PY%
  echo Create/setup it first.
  exit /b 1
)

set PYTHONUTF8=1
set USE_TF=0
set TRANSFORMERS_NO_TF=1

cd /d "%PY_SERVICES%"
"%PY%" openbook_parser\booknlp_ab_eval_v2.py "%BOOK_ROOT%" --limit 18 --output-csv "%BOOK_ROOT%\booknlp_ab_eval_first18.csv" %*

endlocal
