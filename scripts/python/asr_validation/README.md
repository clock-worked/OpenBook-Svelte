# ASR Validation Scripts

These scripts generate ASR artifacts, compare generated audio against source dialogue, and produce manifests for repair or regeneration.

## Files

| File | Role | Why It Lives Here |
| --- | --- | --- |
| `asr_word_timestamps.py` | Generate per-clip word timestamp JSON artifacts from audio files or folders | Offline artifact generation, not backend runtime |
| `asr_experiment_runner.py` | Run merged-split and punctuation A/B experiments | Research harness for pipeline tuning |
| `asr_chapter_audio_audit.py` | Audit chapter or book audio against `dialogue.json` and emit regenerate manifests | Batch audit CLI, not frontend-invoked |
| `review_book1_whisperx_audit.py` | Book-1 focused WhisperX review wrapper with TSV output and resume behavior | One-off validation workflow |
| `transcribe_whisper_small_en_gpu.py` | Low-level transcription helper shared by other standalone validation tools | Utility module for offline tools |
| `apply_audit_line_id_shift.py` | Apply line-id filename/manifest corrections after audit review | Maintenance script for repaired assets |

## Shared Dependencies

- Whisper / WhisperX
- `chapter_audio_test/` from `py_services/` for validation helpers where needed
- resource folders under the active book root

## Outputs

Common outputs from this folder include:

- `*.asr.json` timestamp artifacts
- per-chapter audit reports
- regenerate manifests
- review TSV files
- experiment comparison reports

## Boundary Notes

None of these scripts are part of the FastAPI runtime surface. They are intentionally kept out of `py_services/` so the backend folder stays focused on code that the app imports or serves.