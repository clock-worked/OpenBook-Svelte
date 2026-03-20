# Publishing Scripts

These tools operate after line or chapter audio already exists.

## Files

| File | Role | Outputs |
| --- | --- | --- |
| `apply_book_audio_finishing.py` | Apply final silence adjustments to title clips, chapter endings, proverb endings, and dash-only clips | Backups, report JSON, updated manifests |
| `assemble_book_m4b.py` | Assemble per-chapter and full-book M4B files from generated clips | WAV intermediates, chapter M4Bs, full-book M4B, assembly report |

## Boundary Notes

These are publishing/post-processing utilities. They do not participate in API runtime and should remain outside `py_services/`.