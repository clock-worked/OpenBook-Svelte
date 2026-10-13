# Book-level character store is the source of truth

Character colors and voice assignments are authoritative at the book level; chapter-level data is a derived cache that syncs from book-level on load. A single write point writes book-level only, and lookup precedence is book > chapter > persisted hash default, so colors stay stable across chapters and can no longer diverge between the two levels.

**Considered options**: dual writes to both levels (rejected — the old flow wrote chapter and book files and they diverged); regenerating hash-based defaults each run (rejected — colors must stay stable, so defaults are persisted).
