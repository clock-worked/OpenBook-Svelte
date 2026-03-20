# Development Smoke Scripts

These scripts are small regression checks used during backend or model changes.

## Files

| File | Role | Notes |
| --- | --- | --- |
| `router_split_smoke_test.py` | Validate FastAPI router registration and a set of lightweight API behaviors | Good post-refactor backend regression check |
| `vibevoice_smoke_test.py` | Run a direct local VibeVoice inference smoke test | Useful for confirming local model wiring |

## Boundary Notes

They are important developer checks, but they are not runtime modules and should not live in `py_services/`.