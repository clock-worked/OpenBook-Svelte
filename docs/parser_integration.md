# Parser Integration

## CLI wrapper

Create `text_processing/dialogue_parsing/dialog_parse_coref_cli.py` that accepts:

- `--input <chapter.txt>`
- `--outdir <bookRoot>`
- `--narrator <name>`
- `--json` (emit JSON summary)
- `--txt` (emit human readable)

Output JSON to stdout:

```json
{"ok": true, "chapter": "<slug>", "numLines": 0, "numConflicts": 0, "outdir": "<abs>"}
```

## Tauri bridge

- Command: `run_parser(input, outdir, narrator, python_bin?)` spawns the CLI.
- Returns raw stdout; frontend parses JSON.

## Parser integration

CLI wrapper: `text_processing/dialogue_parsing/dialog_parse_coref_cli.py`

### Invocation
```
python text_processing/dialogue_parsing/dialog_parse_coref_cli.py \
  --input "<chapter.txt>" \
  --outdir "<bookRoot>" \
  --narrator "Catherine" \
  --json --txt
```

### Outputs
- Directory: `<bookRoot>/<Chapter>/`
- Files:
  - `<Chapter>.script.json`
  - `<Chapter>.characters.json`
  - `<Chapter>.parsed.txt` (optional)

### JSON schema (minimal)
- `script.json`: `{ chapter, sourceFile, lines[], stats{ numConflicts, numLines } }`
- `lines[]`: `{ id, text, span|null, chosenSpeaker|null, candidates[], isConflict }`
- `characters.json`: `{ characters[]: { name, color|null, voice|null } }`


