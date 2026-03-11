# OpenBook Svelte: Python Parser Sidecar

This directory hosts the Python dialogue parser copied from the OpenBook Python app, packaged for use from the Svelte/Tauri app via a local virtual environment.

## Setup

Windows (PowerShell):
```powershell
cd web_applications/OpenBook-svelte/py_services
./bootstrap.ps1
```

macOS/Linux:
```bash
cd web_applications/OpenBook-svelte/py_services
./bootstrap.sh
```

## CLI

```bash
# Windows
../.venv/Scripts/python.exe py_services/openbook_cli.py parse --input <path-to-txt>
# macOS/Linux
../.venv/bin/python py_services/openbook_cli.py parse --input <path-to-txt>
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
../.venv/Scripts/python.exe py_services/router_split_smoke_test.py
```

Expected output ends with:

```text
Lane 5 router split smoke test passed
```

## ASR validation + audio experiments

For ASR word timestamps and short-segment generation experiments, use:

- `py_services/asr_word_timestamps.py`
- `py_services/asr_experiment_runner.py`

Detailed workflow and example commands:

- `docs/asr_validation_experiments.md`

## Heuristic Quality Gate (curated dialogue)

Before and after heuristic changes, run the curated gate and compare dialogue accuracy.

```bash
# BookNLP current state (save baseline snapshot)
../.venv/Scripts/python.exe py_services/openbook_parser/evaluate_heuristics_gate.py \
  "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/A-Practical-Guide-To-Evil/Book-1" \
  --backend booknlp \
  --chapter-regex "^(0[1-9]|1[0-8])-" \
  --save-summary "C:/temp/openbook_book1_first18_baseline.json"

# After heuristics change: compare against baseline and fail on regression
../.venv/Scripts/python.exe py_services/openbook_parser/evaluate_heuristics_gate.py \
  "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/A-Practical-Guide-To-Evil/Book-1" \
  --backend booknlp \
  --chapter-regex "^(0[1-9]|1[0-8])-" \
  --baseline-summary "C:/temp/openbook_book1_first18_baseline.json" \
  --fail-on-regression
```

This gate reports dialogue accuracy (overall + per chapter) and exits with code `2` if regression is detected.

### Current target: Primal Hunter Book 14 Chapter 1

```bash
# Save baseline for Book-14 Chapter 1 sample
../.venv/Scripts/python.exe py_services/openbook_parser/evaluate_heuristics_gate.py \
  "C:/Users/Chad/Documents/Code/JavaScript/OpenBook-svelte/py_services/samples" \
  --backend booknlp \
  --chapter-regex "^primal-hunter-book14-ch01$" \
  --save-summary "C:/temp/primal_hunter_book14_ch1_baseline.json"

# Compare current heuristics to the saved baseline and fail on regression
../.venv/Scripts/python.exe py_services/openbook_parser/evaluate_heuristics_gate.py \
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


