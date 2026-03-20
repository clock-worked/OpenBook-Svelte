"""Shared sys.path bootstrap for standalone Python scripts."""

from __future__ import annotations

import sys
from pathlib import Path


def _find_repo_root(start_dir: Path) -> Path:
    for candidate in (start_dir, *start_dir.parents):
        if (candidate / "py_services").is_dir() and (candidate / "scripts").is_dir():
            return candidate
    raise RuntimeError(
        f"Unable to locate repository root from {start_dir}. Expected py_services/ and scripts/."
    )


def bootstrap_python_script_paths(start_file: str) -> dict[str, Path]:
    repo_root = _find_repo_root(Path(start_file).resolve().parent)
    scripts_python_dir = repo_root / "scripts" / "python"
    py_services_dir = repo_root / "py_services"

    path_candidates = [repo_root, scripts_python_dir, py_services_dir]
    if scripts_python_dir.exists():
        path_candidates.extend(
            sorted(
                child
                for child in scripts_python_dir.iterdir()
                if child.is_dir() and not child.name.startswith("__")
            )
        )

    for candidate in reversed(path_candidates):
        candidate_str = str(candidate.resolve())
        if candidate.exists() and candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)

    return {
        "repoRoot": repo_root,
        "scriptsPythonDir": scripts_python_dir,
        "pyServicesDir": py_services_dir,
    }