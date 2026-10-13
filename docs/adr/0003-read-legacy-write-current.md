# Read legacy, write current schema only

Compatibility is asymmetric: legacy formats (v1 `script.json`, v2 dialogue, v2 `characters.json`) are read only where needed for compatibility, while all active app flows write only the current schema (`dialogue.json` v3.2 and per-character files v3.0). This keeps a single canonical write shape while legacy books remain openable; legacy migration data is treated as transitional input, never the output target.
