# Setup Scripts

This folder contains environment and bootstrap helpers for external tooling.

## Files

| File | Role | Notes |
| --- | --- | --- |
| `vibevoice_asr_local.py` | Clone, update, install, download, and optionally run the official VibeVoice-ASR repo | Setup helper, not runtime code |
| `download_openjev.py` | Download the Open-Jev-9B adapter and its Qwen base model from Hugging Face | Edit `TARGET_DIR` first: it is a machine-specific path |

## Boundary Notes

Anything here is installation-oriented and should stay separate from backend runtime modules.