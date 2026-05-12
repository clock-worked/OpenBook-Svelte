# OpenBook Svelte: Python Backend Runtime

This directory now holds the Python runtime backend for OpenBook: FastAPI composition, routers, runtime state, parser packages, and shared support code that the backend imports directly.

Standalone developer utilities were moved out to `scripts/python/` so `py_services/` can stay focused on runtime modules.

## Setup

From repo root, install the shared backend dependencies into the repo virtual environment:

```bash
.venv/Scripts/python.exe -m pip install -r py_services/requirements.txt
```

## CLI

Frontend/Tauri still calls the parser CLI from here.

```bash
.venv/Scripts/python.exe py_services/openbook_cli.py parse --input <path-to-txt>
```

Output is a single JSON string to stdout:
```json
{
  "script": [ { "segments": [], "speaker": "Narrator", "line_type": "narration", "is_suggestion": false, "suggestions": [] } ],
  "characters": ["Narrator"],
  "meta": {"version": "1.0.0"}
}
```

## Lane 5 Router Split Smoke Test

Run a lightweight API smoke test that validates parser/audio router registration and key non-heavy endpoints.

```bash
.venv/Scripts/python.exe scripts/python/dev_smoke/router_split_smoke_test.py
```

Expected output ends with:

```text
Lane 5 router split smoke test passed
```

## ASR validation + audio experiments

For ASR word timestamps and short-segment generation experiments, use the standalone utilities under `scripts/python/asr_validation/`:

- `scripts/python/asr_validation/asr_word_timestamps.py`
- `scripts/python/asr_validation/asr_experiment_runner.py`

Detailed workflow and example commands:

- `docs/asr_validation_experiments.md`

## Heuristic Quality Gate (curated dialogue)

Before and after heuristic changes, run the curated gate and compare dialogue accuracy.

```bash
# BookNLP current state (save baseline snapshot)
.venv/Scripts/python.exe py_services/openbook_parser/evaluate_heuristics_gate.py \
  "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/A-Practical-Guide-To-Evil/Book-1" \
  --backend booknlp \
  --chapter-regex "^(0[1-9]|1[0-8])-" \
  --save-summary "C:/temp/openbook_book1_first18_baseline.json"

# After heuristics change: compare against baseline and fail on regression
.venv/Scripts/python.exe py_services/openbook_parser/evaluate_heuristics_gate.py \
  "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/A-Practical-Guide-To-Evil/Book-1" \
  --backend booknlp \
  --chapter-regex "^(0[1-9]|1[0-8])-" \
  --baseline-summary "C:/temp/openbook_book1_first18_baseline.json" \
  --fail-on-regression
```

This gate reports dialogue accuracy (overall + per chapter) and exits with code `2` if regression is detected.

## Dialogue AI evaluation against curated chapters

Run parser-only versus parser-plus-AI evaluation against curated `dialogue.json` chapters and save a baseline for future comparisons.

```bash
.venv/Scripts/python.exe py_services/openbook_parser/evaluate_dialogue_ai_against_curations.py \
  "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-15+" \
  --backend booknlp \
  --chapter-regex "^(000[1-9]|0010) -" \
  --save-summary "C:/temp/primal_hunter_book15_first10_ai_eval.json"
```

To compare future changes against that baseline and fail on regression:

```bash
.venv/Scripts/python.exe py_services/openbook_parser/evaluate_dialogue_ai_against_curations.py \
  "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-15+" \
  --backend booknlp \
  --chapter-regex "^(000[1-9]|0010) -" \
  --baseline-summary "C:/temp/primal_hunter_book15_first10_ai_eval.json" \
  --fail-on-regression
```

Dialogue AI assist now persists per-line request/response logs and caches identical model calls automatically.

- Cache directory: `py_services/.cache/dialogue_ai/`
- Run log directory: `py_services/logs/dialogue_ai/<chapter>/<timestamp>/`

The desktop AI Assist panel shows the run log directory and each line's log path after a run.

### Current target: Primal Hunter Book 14 Chapter 1

```bash
# Save baseline for Book-14 Chapter 1 sample
.venv/Scripts/python.exe py_services/openbook_parser/evaluate_heuristics_gate.py \
  "C:/Users/Chad/Documents/Code/JavaScript/OpenBook-svelte/py_services/samples" \
  --backend booknlp \
  --chapter-regex "^primal-hunter-book14-ch01$" \
  --save-summary "C:/temp/primal_hunter_book14_ch1_baseline.json"

# Compare current heuristics to the saved baseline and fail on regression
.venv/Scripts/python.exe py_services/openbook_parser/evaluate_heuristics_gate.py \
  "C:/Users/Chad/Documents/Code/JavaScript/OpenBook-svelte/py_services/samples" \
  --backend booknlp \
  --chapter-regex "^primal-hunter-book14-ch01$" \
  --baseline-summary "C:/temp/primal_hunter_book14_ch1_baseline.json" \
  --fail-on-regression
```

## Tauri integration

The Tauri app now uses a Rust-side command that calls the Python parser:

**Rust (src-tauri/src/main.rs)**:
```rust
#[tauri::command]
async fn run_parser(input: String, python_bin: Option<String>) -> Result<String, String> {
    let exe = python_bin.unwrap_or_else(|| "python".to_string());
    let cli = Path::new("py_services").join("openbook_cli.py");
    let output = Command::new(exe)
        .arg(cli)
        .arg("parse")
        .arg("--input").arg(&input)
        .output()
        .map_err(|e| format!("Failed to execute parser: {}", e))?;
    // Returns JSON stdout
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}
```

**TypeScript (src/lib/services/parser.ts)**:
```ts
import { invoke } from '@tauri-apps/api/core';

export async function runParserForChapter(args: { input: string; outdir: string }) {
  const bin = get(pythonBin) || 'python'; // User can configure Python path
  const res = await invoke<string>('run_parser', { input: args.input, python_bin: bin });
  const parsed = JSON.parse(res);
  // Writes .txt.script.json file to outdir
  // ...
}
```

**Python Path Configuration**: 
- Default: Uses `python` from PATH (which should resolve to `.venv/Scripts/python.exe` after bootstrap)
- Custom: Set Python path in app settings to use `.venv/Scripts/python.exe` explicitly


