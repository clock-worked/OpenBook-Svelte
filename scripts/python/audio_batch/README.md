# Audio Batch Scripts

These entrypoints run manual or offline generation flows that reuse the same VibeVoice and split-validation support code as the backend, but are not called by the frontend.

## Files

| File | Role | Notes |
| --- | --- | --- |
| `generate_and_split_chapter_narrator.py` | Full end-to-end harness for chapter generation, ASR alignment, splitting, and validation | Main manual testbed for the chapter pipeline |
| `run_book_audio_batch.py` | Multi-chapter wrapper around `generate_and_split_chapter_narrator.py` | Batch progress and summary reporting |
| `run_book_full_chapter_audio_batch.py` | Generate whole-chapter audio directly from `chapter.txt` using the chunk pipeline | Skips completed outputs by default |
| `run_book_title_audio.py` | Generate one combined chapter-title narration file, split it, and optionally copy clips back into chapter folders | Title-audio specific workflow |
| `generate_chapter_audio.py` | Older direct per-line generation workflow | Kept for manual fallback/legacy experimentation |
| `generate_chapter_audio.md` | Usage notes for the legacy direct generator | Historical doc kept with the script |

## Shared Dependencies

These scripts still rely on backend support that stays in `py_services/`:

- `vibevoice_local_service.py`
- `chapter_audio_test/`

That dependency is intentional. The backend now imports `chapter_audio_test/` too, so it is no longer just a standalone script helper package.

## Why These Are Not In py_services

- They are manual entrypoints, not backend runtime modules.
- The frontend does not invoke them as part of normal operation.
- They produce offline artifacts, logs, and batch outputs rather than API responses.