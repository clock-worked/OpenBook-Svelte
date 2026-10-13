"""Safe filesystem access for book-root-relative paths.

Centralizes the two invariants every file route needs:

- a client-supplied relative path must resolve to a location *inside* the
  book root. We enforce this with `Path.resolve()` + a parent check, not a
  naive ``".." in path`` substring test (which both defeats real traversal
  protection and rejects legitimate names like ``a..b.txt``).
- JSON files are written atomically (temp file in the same directory +
  ``os.replace``) so a crashed or concurrent write never leaves a half-written
  file behind.

No route should hand-roll path guarding or JSON writing; import from here.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Union

from fastapi import HTTPException


def resolve_within_root(book_root: Union[str, Path], rel_path: Union[str, Path]) -> Path:
    """Resolve ``rel_path`` against ``book_root`` and assert it stays inside.

    Returns the fully-resolved absolute path. Raises ``HTTPException(400)``
    on any traversal attempt (``..`` segments, absolute paths, symlinks that
    escape the root). A name that merely *contains* the substring ``..``
    (e.g. ``a..b.txt``) resolves to a child of the root and is allowed.
    """
    root = Path(book_root).resolve()
    requested = (root / str(rel_path)).resolve()
    if requested != root and root not in requested.parents:
        raise HTTPException(status_code=400, detail="Invalid file path")
    return requested


def write_json_atomic(resolved_path: Union[str, Path], data: Dict[str, Any]) -> None:
    """Atomically write ``data`` as JSON to ``resolved_path``.

    Writes to a temp file in the same directory, then ``os.replace``s it onto
    the target (atomic on the same filesystem). The temp file is always
    cleaned up on failure so a crash never leaves stray files behind.
    """
    path = Path(resolved_path)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary_path = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise
