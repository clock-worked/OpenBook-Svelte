"""In-memory stats accumulation for incremental character stats (ADR 0012).

The accumulation is a per-character, per-chapter line-count matrix, kept in
memory and keyed by book root. It is built (one full book scan) when a book
is selected, and afterwards each dialogue save only applies that chapter's
counts — no rescan. A save that arrives before any build (e.g. right after a
backend restart) triggers a lazy build first.

Stats are therefore eventually consistent: a crash between the dialogue
write and the delta apply leaves stats stale until a full rebuild
(`update_character_stats`, the manual "update stats" button).
"""

import json
import threading
from pathlib import Path
from typing import Dict, Optional

from update_character_stats import (
    character_counts_in_dialogue,
    derive_character_stats,
    scan_character_chapter_counts,
    write_character_stats,
)


class StatsAccumulator:
    """Per-book in-memory stats accumulation (char -> chapter -> lines)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._matrix: Dict[str, Dict[str, Dict[str, int]]] = {}

    def build(self, book_root) -> None:
        """(Re)build the accumulation for a book with one full scan.

        Called when the book root is set. Idempotent: a rebuild replaces
        the previous matrix for that book.
        """
        root = str(Path(book_root).resolve())
        matrix, _ = scan_character_chapter_counts(Path(book_root))
        with self._lock:
            self._matrix[root] = matrix

    def apply_chapter(
        self,
        book_root,
        chapter_id: str,
        chapter_counts: Dict[str, int],
    ) -> Dict[str, Dict[str, int]]:
        """Replace one chapter's counts and return the derived book-wide stats.

        Lazily builds the accumulation when it is missing (restart safety).
        """
        root = str(Path(book_root).resolve())
        with self._lock:
            if root not in self._matrix:
                matrix, _ = scan_character_chapter_counts(Path(book_root))
                self._matrix[root] = matrix
            matrix = self._matrix[root]
            # The matrix is keyed character -> {chapter: count}, so replacing
            # one chapter means dropping its old count from every character,
            # then recording the new counts (zero counts drop the entry).
            for chapters in matrix.values():
                chapters.pop(chapter_id, None)
            for character, count in chapter_counts.items():
                if count > 0:
                    matrix.setdefault(character, {})[chapter_id] = count
            return derive_character_stats(matrix)


stats_accumulator = StatsAccumulator()


def apply_dialogue_stats_delta(
    book_root,
    dialogue_path: Path,
    content: Optional[dict] = None,
) -> Dict:
    """Apply one chapter's dialogue save to the character stats.

    The fast path of ADR 0012: no book scan, only the saved chapter's
    counts are taken into the accumulation, and only the character files
    whose stats changed are written.

    `content` is the already-saved dialogue document (the combined
    save-dialogue endpoint has it in hand); when None the file is read
    from disk (the dual-I/O FSA endpoint, where the frontend wrote it).
    """
    root = Path(book_root)
    if content is None:
        content = json.loads(dialogue_path.read_text(encoding='utf-8'))

    is_v1 = dialogue_path.name.endswith('.script.json')
    chapter_id = dialogue_path.parent.name
    chapter_counts = character_counts_in_dialogue(content, is_v1)

    character_stats = stats_accumulator.apply_chapter(root, chapter_id, chapter_counts)
    return write_character_stats(root, character_stats, 1)
