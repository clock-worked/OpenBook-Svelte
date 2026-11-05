# Backend Python environments

Which Python the dev backend runs under, and how to diagnose or switch it.
(Added 2026-11-05 after a multi-environment whisperx debugging session.)

## The two environments on this machine

| | `OpenBook-svelte\.venv` (the default) | `C:\Users\CJDJ\anaconda3` |
|---|---|---|
| torch | 2.8.0+cu126 (CUDA) | 2.8.0+cu126 (CUDA) |
| whisperx | 3.8.1 (installed 2026-11-05) | 3.8.1 |
| GPU | RTX 4080 Laptop 12 GB | RTX 4080 Laptop 12 GB |

`dev-backend.mjs` defaults to `.venv\Scripts\python.exe`. Since 2026-11-05
`.venv` carries torch 2.8.0+cu126, so WhisperX transcription runs on the GPU
in-process (a 12-minute chapter transcribes in ~35 s). A pyannote
UserWarning about torchcodec is harmless: `whisperx.load_audio` falls back
to a raw 16 kHz numpy array, which faster-whisper accepts.

## Landmine: the `.venv` python.exe is a copy of anaconda3's

`.venv\Scripts\python.exe` is a file copy of anaconda3's python.exe. Its
processes show anaconda3 stdlib paths in `sys.path`, which makes a `.venv`
backend *look* like the anaconda3 one. Package resolution still comes from
`.venv\Lib\site-packages`. Do not infer the environment from `sys.path`
alone — use `sys.executable` (or the `/env` endpoint).

## Verify which Python is serving

```
GET http://127.0.0.1:8012/env
```

returns `pythonExecutable` plus torch/whisperx/pyarrow availability.

Or from PowerShell:

```powershell
$p = (Get-NetTCPConnection -LocalPort 8012 -State Listen | Select-Object -First 1).OwningProcess
(Get-CimInstance Win32_Process -Filter "ProcessId=$p").CommandLine
```

## Switching the backend Python

`dev-backend.mjs` honours `$OPENBOOK_PYTHON_EXE` (else `.venv`). Set it
where the npm script runs — the `dev:all` script in
`apps/desktop/package.json` — **not** in `.vscode/tasks.json`: VS Code runs
the auto-detected npm script, so env set there is a no-op.

## Logs

`dev-backend.mjs` tees uvicorn stdout/stderr to `apps/desktop/.dev/backend.log`.
